# 06 管理实例

本章讲实例层面的管理面：生命周期动作（暂停 / 恢复 / 终止 / 取消）、变量读写、
故障处理（错误二分与故障单五动作）、管理员手术，以及发布前的模拟试跑。

## 实例生命周期

实例状态机：`running`（在跑）→ 终态 `completed` / `cancelled` / `terminated` /
`failed`；中间还有两个冻结态：`paused`（人工暂停）与 `suspended`（出故障挂起）。

| 动作 | 效果 |
|---|---|
| `runtime.pause_process(iid, actor, reason)` | 暂停：任务全动作被拦、定时器冻结顺延，数据保持快照 |
| `runtime.resume_process(iid, actor)` | 恢复运行，冻结期间到点的定时器顺延补跑 |
| `runtime.terminate_process(iid, actor, reason)` | 终止（管理员强杀）：**级联砍树**——整实例未完令牌全撤、未结算的子实例子先父后落终态 |
| `runtime.cancel_process(iid, actor)` | 业务取消（与 terminated 语义区分，都是终态） |

实测暂停语义：

```text
pause_process  ->  state=paused
暂停期间 complete  ->  FloworkError: 实例已暂停，恢复后才可办理
resume_process ->  state=running
```

两个冻结态的差别要记住：`paused` 是你主动按的暂停；`suspended` 是技术故障把实例
挂住了（见下文故障单）。终态是单向门——终态实例不能再办任务、不能改变量。

## 变量读写面

发起之后，业务系统经常要修正或补录变量。外部写入走统一入口：

```python
engine.runtime.variables_of(iid)                        # 读变量全集（只读）
engine.runtime.update_variables(                        # 外部受控改值（PATCH 合并）
    iid, {"amount": 200}, actor="ops", reason="金额录错，修正"
)
engine.runtime.variable_history(iid, name="amount")     # 变量血缘
```

`update_variables` 有三道门槛（实测）：

1. **键白名单**：只能写「已声明的变量 ∪ input 契约字段 ∪ 实例里已有的键」——
   凭空造新键、写保留标识符（`now` / `initiator` 这些）都被拒绝；
2. **类型对位**：声明的类型对不上（number 写成字符串）拒绝；
3. **状态门槛**：终态实例拒改（数据定格）；暂停实例也拒改（冻结快照——
   要改先 resume，改完再 pause）。

每次写入留**血缘**（`variable_history`，新在前）：改前值 → 改后值、谁改的
（`acted_by`）、哪个写方（`external_update` / `engine` / 任务办理……）：

```text
amount 100 -> 200 by ops    写方 external_update
amount None -> 100 by None  写方 engine        （发起时的初值也是一条）
```

## 故障处理：错误二分与故障单

引擎把运行期错误分成性质完全不同的两类：

- **业务失败**（驳回、契约拒收、条件不满足走默认边）：流程语义的正常分支，
  **不产生故障单**；
- **技术故障**（handler 代码抛异常、表达式求值错、取人解析失败）：流程自己
  处理不了的问题——实例**挂起**（suspended）+ 产生一张**故障单（incident）**，
  停下来等人处置。

一个技术故障的实测全程（service 节点的 handler 抛异常）：

```python
def explode(ctx, params):
    raise RuntimeError("外部接口炸了")

engine.register_handler("explode", explode)
# 图纸：start -> a(user_task) -> boom(service, action=explode) -> after -> end

t = engine.task.list_tasks(instance_id=iid)[0]
engine.task.complete(t.id, actor="u_a")
# -> 抛 FloworkError：失败命令整笔回滚（A 的任务还在待办）
# -> 但实例已转 suspended，且落了一张故障单

engine.runtime.get_instance(iid).state           # 'suspended'
engine.runtime.list_incidents(instance_id=iid)
# [{'id': '...', 'error_class': 'EngineExecutionError',
#   'message': "service_task boom 动作 'explode' ...", 'state': 'open', ...}]
```

注意「**命令回滚 + 实例挂起**」的组合：让实例炸的那条命令整体回滚（A 的待办
原样保留），但实例状态与故障单留下来了——数据自洽，修好后从断点重办。

处置动作有五个（`resolve_incident`）：

```python
engine.runtime.resolve_incident(incident_id, action="retry", actor="ops")
```

| action | 语义 | 适用 |
|---|---|---|
| `retry` | 恢复运行——根因修好后**重办**导致失败的那一步 | 改完代码 / 外部接口恢复了 |
| `resume` | 仅恢复运行，不重做 | 挂起误伤，直接放行 |
| `skip` | 当前停住的节点视为完成，路由推进后继 | 这一步不做了，跳过去 |
| `cancel` | 取消实例 | 这单不要了 |
| `fail` | 实例判失败（终态 failed） | 认定整单报废 |

上面例子修好 handler 后 `retry`：

```python
engine.register_handler("explode", explode_fixed, replace=True)   # 换上修好的
engine.runtime.resolve_incident(incident_id, action="retry", actor="ops")
# state -> running；重办 A 的待办，boom 这次不炸，推进到 after
```

根因没修好就 retry，故障会复发（再挂起、再出故障单）——这是设计行为，不是 bug。

### 管理员手术

两个更硬的手工干预（都在 `runtime` 上，绕过正常流转，慎用）：

- `move_token(token_id, target_activity, actor)`：把一枚令牌直接搬到指定节点；
- `activate_node(instance_id, activity_code, actor)` / `cancel_node(...)`：
  手工激活 / 取消某节点。

手术不走条件求值与汇聚判定，图复杂时容易绕出语义漏洞——优先用驳回、skip 这类
「走正门」的动作，手术留给数据修复类场景。

## 模拟试跑

`simulate` 在不落任何真实数据的前提下把整张图**真跑一遍**（实测）：

```python
report = engine.runtime.simulate("sim_flow", variables={"amount": 5000},
                                 start_user="u_z")
report.final_state      # 'completed' —— 走大额臂还是默认边，直接看结果
```

模拟的隔离语义：条件**真求值**（用你给的变量）；人工节点即到即过；
service 节点**恒为桩**（绝不执行 handler——试跑不能真打外部接口）；等待节点
冻结为卡点；不建定时任务、不发事件、实例列表默认排除模拟实例。
报告里有终态、令牌时间线、桩清单、卡点与路由轨迹——发布前先跑一遍，
比上线后发现条件写反便宜得多。它也能直接吃**未部署的草稿**（`spec=` 传定义对象），
设计器与 CI 都用这条路做「证据先于发布」。

## 实例查询

```python
engine.runtime.list_instances(definition_code="expense", state="running")
engine.runtime.get_instance(iid)                       # InstanceInfo
engine.history.instances(state="completed")            # 历史侧（只读）
engine.history.trace_instance(iid)                     # 轨迹页（见第 8 章）
```

`list_instances` 可按定义版本过滤（「多少实例还钉在旧版本上」），
`include_simulated=False` 是缺省——模拟实例不混进业务列表。

## 本章小结

- 四个生命周期动作；暂停是冻结（任务拦、定时器延），终止是级联砍树（含子实例）。
- 变量外部写入三门槛：键白名单、类型对位、状态门槛；每次写入有血缘。
- 错误二分：业务失败不出故障单；技术故障＝挂起 + 故障单，五动作处置
  （retry 要重办、skip 是跳节点）。
- `simulate` 隔离试跑：条件真求值、系统节点恒桩，发布前的廉价保险。

下一章讲异步协作：消息、等待节点与子流程。
