# 08 消息、等待与子流程

本章讲流程与外部世界的异步协作：消息怎么发、等待节点怎么等、消息怎么反过来发起
流程，以及把一段流程封装成子流程复用（含每人一个子实例的多实例形态）。

## 消息

消息是引擎与外部系统之间的业务事件（「合同签回来了」「支付完成了」）。
消息有名字（name），可选带关联键（correlation_key）和载荷（payload）：

```python
engine.messages.publish_message("contract_signed",
                                payload={"signed_at": "2026-01-01"})
```

`publish_message` 的语义是「发布并尽力关联」：发布出去后，引擎在**等待中的订阅**里
找匹配的（消息名 + 关联键都吻合），命中就唤醒那个等待节点；没有等待者就进**缓冲**，
等将来的订阅出现（缓冲有 TTL，过期丢弃）。另外两条路：

```python
engine.messages.correlate_message("contract_signed",
                                  payload={...})    # 同步签收：没等到就返回 None，不缓冲
engine.messages.signal("wake_up")                   # 广播唤醒：所有等这个名字的都醒
```

反查「现在有哪些流程在等什么」：

```python
engine.messages.waiting(name="contract_signed")     # 等待中的订阅清单
```

**关联键防串单**：多个实例同时等同一个消息名时，靠关联键区分——订阅时声明的
关联键表达式（求值成业务单号）与发布时带的 `correlation_key` 相等才命中。
等待节点与消息启动都用这套机制。

## 等待节点（wait）

wait 节点让流程**原地停住**，三种模式（可组合）：

```python
.wait("gate", "等合同回传",
      message={"name": "contract_signed"})                    # 模式 1：等消息
.wait("nap", "冷却 60 分钟",
      timeout={"after_minutes": 60})                          # 模式 2：等时长
.wait("gate", "回调或 60 分钟超时",
      message={"name": "callback"},
      timeout={"after_minutes": 60})                          # 模式 3：竞速，先到者走
```

实测的完整等待—唤醒回合：

```text
进入 gate：订阅 contract_signed；后续节点没有待办（流程在等）
messages.waiting(name="contract_signed")  ->  1 条等待记录
publish_message("contract_signed", payload={"signed_at": "..."})
  ->  订阅命中，gate 完成，后续待办出现
```

三个关键语义：

1. **结果词汇 `wait.*` 只能用在等待节点的直接出边条件上**（设计期校验拦截）：

```python
.flow("gate", "by_msg", condition="wait.result == 'matched'")   # 消息到
.flow("gate", "by_time", default=True)                          # 否则=超时
```

   `wait.result` ∈ `matched`（消息到了）/ `elapsed`（时间到了），
   另有 `wait.message_name` / `wait.message_id`。竞速实测：同一张图，
   发消息的实例选中 `gate->by_msg`，没人发消息超时的实例选中 `gate->by_time`。

2. **载荷进变量**：`output_mappings`（与人工任务完成同一条写入路径）声明
   「变量名 = 消息上下文表达式」；不声明就**全量合并**（payload 顶层键直接进变量，
   实测 `signed_at` 直接可用）。表达式在**消息上下文**求值（`payload.*` + `now`）；
   裸 `payload.键` 投影缺键得 None，复合表达式（如 `payload.a.b`、函数包裹）
   缺键＝求值错＝技术故障。

3. **暂停冻结**：实例暂停期间等待的定时器冻结、命中的消息落缓冲，恢复后补消费。

等待节点常用来接外部系统的异步回调：流程发起调用（service 节点）→ wait 等回调 →
带超时兜底——回调丢了走超时分支补救，比无限等健壮。

## 消息启动流程（start_on_message）

不光流程里能等消息，**消息还能发起流程**——上游系统发一条「订单已创建」，
引擎自动起一个实例。在图纸顶层声明（JSON 模板写法）：

```json
{
  "code": "order_flow",
  "start_on_message": [
    {
      "name": "order_created",
      "start_user": "'u_sys'",
      "input_mappings": {"order_id": "payload.order_id", "amount": "payload.amount"},
      "business_key": "payload.order_id"
    }
  ],
  "nodes": [ ... ], "flows": [ ... ]
}
```

声明里的表达式都在**消息上下文**（`payload.*` + `now`）求值——此刻实例还不存在：
`input_mappings` 把载荷映射成启动变量（实测 `order_id`/`amount` 落进变量）；
`start_user` 是发起人（缺省合成 `message:order_created`）；`business_key` 进实例
业务键；`correlation_key` 配了就要求发布的消息带相同关联键才启动（防串单）。

发布即生效（定义 published，订阅就活着），不用额外注册：

```python
engine.messages.publish_message("order_created",
                                payload={"order_id": "A001", "amount": 99})
# -> 新实例自动创建，start_user=u_sys，变量里有了 order_id/amount
```

## 子流程（call_activity）

把一段可复用的流程（比如「合规检查」）独立定义，主流程里调用：

```json
{ "code": "sub_check", "name": "合规子流程", "kind": "call_activity",
  "process_code": "compliance_check",
  "input_mappings": {"amount": "amount"},
  "output_mappings": {"compliance_result": "compliance_result"} }
```

- `process_code` 指向被调定义；`binding` 缺省 `latest`（每次解析最新已发布版本）；
  `deployment` 只是部署期校验加严（部署父时子必须已发布，L3 ERROR）——
  **binding 不锁运行期版本**，进入节点时仍解析最新 published；
- `input_mappings`：进入子流程时求值（**父上下文**），结果注入子实例变量；
- `output_mappings`：子流程完成时回传（**子上下文**），结果写回父实例变量；
- 调用恒为同步：父节点等子流程**结算**才推进。子实例挂起（suspended）会阻塞
  父侧等待；子被取消 / 终止算已结算，父节点照常完成（输出映射缺键得 None）——
  「驳回」是子流程内部语义，与父无涉。

子实例带 `parent_id` 可回溯父实例；父侧要知道子实例 id，看 `sub_process_started`
事件载荷（级联终止时子先父后落终态，见第 7 章 terminate）。

### 多实例子流程：每人一个子实例

`multi_instance` 把一次调用展开成**多个子实例**——审批场景的「给每个部门
各自走一遍检查」：

```python
.node(
    "fan_out", kind="call_activity", name="逐人合规检查",
    process_code="compliance_check",
    multi_instance={
        "subjects": ["'u_p1'", "'u_p2'", "'u_p3'"],          # 取人表达式，每人一子实例
        "completion": "child_failed >= 1 || child_completed == child_total",
    },
)
```

- `subjects`：取人表达式列表（第 5 章词汇全可用）；输入映射按子求值时注入
  `subject`（第 k 个主体），比如 `input_mappings={"dept": "subject"}` 就是
  每个子实例拿自己的切片；
- `completion`：完成公式，上下文是三计数——`child_total`（计划数）、
  `child_completed`（已完成）、`child_failed`（失败=terminated/cancelled/failed）。
  公式为真即**提前收工**：剩余还在跑的子实例终止。上例含义「任一失败立刻收工，
  否则全完成」；想「忽略失败等全完」就写 `child_completed + child_failed == child_total`。

实测：三个 subject 展开**三个子实例**，各自的待办办完，父实例自动 completed。

聚合回传是 `output_mode="collect"`（缺省）：输出映射的值收集成**按迭代序对齐**
的列表（第 1 个子实例的值落列表第 0 位，失败位补 null）——多实例下 `merge`
式回传会互相覆盖，validator 直接拦（配了 merge + 多实例是设计错误）。

## 本章小结

- 消息三路：`publish_message`（发布+缓冲）、`correlate_message`（同步签收不缓冲）、
  `signal`（广播）；关联键防串单。
- wait 三模式：等消息 / 等时长 / 竞速；`wait.result` 只在直接出边可用；
  载荷可全量合并或显式映射进变量。
- `start_on_message` 让上游消息直接发起实例，声明即生效。
- 子流程 `call_activity` 经 input/output 映射传数据；多实例展开每人一子实例，
  `completion` 三计数公式决定提前收工时机。

下一章讲怎么「看」：事件消费、路由审计、轨迹与 Job。
