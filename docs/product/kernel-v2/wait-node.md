# 等待节点（wait）设计稿【待复核 · v3】

> 2026-10-09。依据：《事件等待与事件网关-行业实践调研-2026-10》（docs_research/，六引擎对照）。
> 定位：spec §2.4「事件网关/消息等待 → 等待节点（见第 7 章），首版后置」的**提前实施**；范围变更由用户记入《本期建设边界》backlog 附录。
> 修订记录：v2 载荷写入回归 spec §7 节点级 output 映射、暂停挂靠既有拦截原则（用户裁定「回归最初的设计」）；**v3 纯延时进 v1（message 改可选，用户裁定「等待多久后继续确实要支持」）、周期性触发明确不做（用户裁定）、结果词汇 timeout→elapsed（中性覆盖纯延时到点与竞速超时）**。
> 拍板残留：§8 第 4 点（correlate_message 新增项）。拍板后并入 spec §7.2＋§2.5＋§2.4＋C.4。

---

## 1. 一句话与行业定位

**wait = 流程停下来，等一条消息、等一段时间、或两者竞速——先到者决定走向。**

- 三模式合一的行业先例是 **Conductor WAIT 任务**（定长延迟／等到某时刻／等外部信号，一个节点三种用法）；
- 等待与关联对标 **Zeebe receive task / message catch**（订阅式关联，基建已同构：求值快照、TTL 缓冲 FIFO 反查、message_id 幂等）；
- 竞速的超时侧对标 **Step Functions 回调 + TimeoutSeconds**（任务自带超时的二元竞速是全行业主形态；BPMN 阵营要画两个捕获事件+网关才能表达，我们不引入第二个节点类型）；
- **不做事件网关节点**：多事件竞速用「并行分支 + any 汇聚」图组合表达（内核已有竞速收尾），等齐用「并行分支 + all 汇聚」（放行门已为异步到达设计）——报告 §4.3 结论。

Zeebe 官方文档明确把消息捕获定位为「AI agent 发消息等回复」的构造（要求唯一关联键）——与 flowork 的 agent 治理定位同向。

## 2. 节点定义（kind=`wait`，内置第八种）

| 字段 | 类型 | 缺省 | 语义 |
|---|---|---|---|
| `message` | `{name: str, correlation_key: str \| null} \| null` | null | 订阅声明（**可选**——纯延时模式不填）。`name` 为静态字符串（消息契约名，权威在生产方）；`correlation_key` 为 CEL 表达式，**进入节点时求值一次并快照**（订阅期内变量变化不更新，Zeebe 同语义），结果须为 string，非 string=技术故障；缺省/null=仅按消息名匹配 |
| `output_mappings` | `dict[str, str]` | 空 | **载荷→变量映射（spec §7 原文：「载荷按节点 output 映射写入变量，与人工任务完成的写入路径同一条」）**。左=实例变量名，右=CEL 表达式于**消息上下文**（`payload.*` + `now`）求值，缺键得 None（CEL 位点统一批既有语义）。未声明 → 全量合并兜底（Zeebe 默认同款）。仅消息命中路径执行；纯延时模式无意义（validator 允许但不生效，或直接 ERROR——从简：允许） |
| `timeout` | `{after_minutes: int > 0} \| null` | null | **时间侧（可选）**。进入节点起算的相对时长（工作日历折算，暂停冻结恢复顺延——与节点定时器同规则）。纯延时模式它就是等待时长本身；与 message 同时声明即二元竞速。「等到指定时刻」（绝对时间/CEL 日期表达式）后置 |
| 公共字段 | code/name/advance_policy… | — | 多入边 wait 的汇聚语义=目标节点推进策略（既有），照常适用 |

**约束：`message` 与 `timeout` 至少声明其一**（都空=什么都不等，validator ERROR）。

**不支持**（pydantic `extra="forbid"` 自动拦截）：multi_instance、deadlines/timeout 旧形态、assignee 族、reject_target。驳回目标不可指向 wait 节点（§6）。**周期性触发（循环定时，如每小时的 R/PT 形态）明确不做**——属定时启动流程的调度域，现有 Job 体系另行解决（用户 2026-10-09 裁定）。

JSON 示例：

```json
// 竞速：等签署回调，三天没回转人工
{
  "code": "wait_signing", "kind": "wait", "name": "等待签署回调",
  "message": {"name": "contract_signed", "correlation_key": "instance.id"},
  "output_mappings": {"signed_doc_id": "payload.doc_id", "signer": "payload.signer"},
  "timeout": {"after_minutes": 4320}
}

// 纯延时：冷却三天后继续（单出边无需条件）
{
  "code": "cooldown", "kind": "wait", "name": "冷却期",
  "timeout": {"after_minutes": 4320}
}
```

出边路由（条件挂边，照旧；竞速模式用，纯延时通常单出边无条件）：

```
wait_signing → 人工跟进   条件: wait.result == "elapsed"
wait_signing → 归档       条件: wait.result == "matched"
```

设计器元数据：category「事件」、shape rect、icon clock、palette 两个预设（「等待消息」「延时等待」）、表单分组两档（基础：name/message/output_mappings；时间默认折叠）。

## 3. 生命周期语义（逐条）

**进入（activate）**：若声明 message → 实例上下文求值 correlation_key → `MessageService.subscribe`（登记订阅并反查缓冲，消息先到竞态由既有 FCFS 机制覆盖——命中即当场解决）；若声明 timeout → 按 `tasks/deadlines.py` 同款形态登记 timer Job（`job_type="timer"`，`payload.kind="wait_timeout"`，`run_at=now+after_minutes` 工作日历折算）。correlation 求值错/非 string = **技术故障**（挂起+故障单，对齐 start_on_message 错误语义）。

**命中（publish 匹配到本订阅，同事务）**：订阅置 matched（message_id 定格防重，既有）；**载荷按节点 `output_mappings` 写入变量**（spec §7 原文语义，与人工任务完成的写入路径同一条——右侧表达式在消息上下文 `payload.*`+`now` 求值，缺键 None）；未声明映射 → 全量合并兜底（既有 `_correlate` 行为）。两种路径均过 input 契约 `node_output` 边界把关，越界=技术故障、publish 失败消息不落库。发 `message_correlated`（既有）；**新增一步：推进**——同一命令事务内经 NodeRegistry 取 wait 行为完成该令牌（与任务完成同一推进管线），路由求值时注入 wait.* 快照（§4）。命中后取消未触发的 timer Job。

**到时（timer 先到；纯延时模式的正常完成）**：若声明了 message → 关闭本节点订阅（`close_subscriptions(node_id=...)`，既有）；wait.result="elapsed"；推进（无载荷写入）。复用 `timer_fired` 事件（spec §7 定时器行原文「到期发 timer_fired 再推进」），payload 增 `kind="wait_timeout"`，不新增事件类型。

**竞速仲裁（仅双声明模式）**：命中与到时几乎同时到 → 两条命令各自 `ctx.touch(instance)`（既有乐观锁）→ 数据库序列化，**先落者赢，后到者 no-op**（订阅已关/节点已推进，自然跳过）。零新仲裁代码。

**离开/取消/实例终止/admin 手术**：挂既有离开路径统一调 `close_subscriptions` + 取消 timer。特别地，**any 汇聚竞速收尾取消兄弟分支时，败者 wait 的订阅与定时器随取消路径关闭**——多事件竞速（消息A vs 消息B）由此图组合表达，无内核新语义。

**暂停/恢复**：挂靠既有「**暂停全动作拦截**」原则（P1 批既定语义）——消息关联是作用于实例的动作，暂停即拦截：订阅行保持（spec §7「暂停期间保持」原文），消息落缓冲（TTL 内），恢复后订阅登记时的缓冲反查既有机制接管。定时器暂停冻结、恢复顺延（既有节点定时器同规则）。零新状态、零新原则。

**循环重入**：离开即关订阅，再次进入重新求值快照、重新订阅（行为要专门测试）。

**发布侧匹配顺序不变**：在途订阅优先 → 启动匹配（start_on_message）→ 缓冲。wait 订阅自动参与第一优先级。

## 4. 结果词汇与路由

| 词汇 | 类型 | 值 | 注入 |
|---|---|---|---|
| `wait.result` | string | `matched`（消息到了）\| `elapsed`（时间到了） | 等待解决时刻，仅该节点出边的条件求值。**elapsed 中性覆盖两态**：纯延时到点是正常完成，竞速模式到点即超时——一个词两种用法，避免「纯延时=timeout」的语义别扭 |
| `wait.message_name` | string \| null | 命中的消息名；纯延时/无消息=null | 同上 |
| `wait.message_id` | string \| null | 命中消息 ID；**signal 广播唤醒与纯延时=null** | 同上 |

- **仅条件位、仅 wait 节点的出边**（词汇目录位点白名单表驱动扩行，F0 模式）；不落实例变量、不进变量历史（引擎内部写不发 `variables_updated` 的既有口径天然兼容）；需要持久化结果的用户由 output_mappings 自行落变量。
- `route_audit` 快照增 `wait` 键（matched/elapsed + message_name）——路由审计自然覆盖。
- 设计器公式目录（F4 后续批）随 catalog 自动下发。

## 5. 配套面

**5.1 发布反馈**：`MessageService.correlate_message(name, correlation_key, payload, *, message_id)` —— 同步入口，**不缓冲**：命中 open 订阅 → 关联+推进，回传 `(instance_id, node_id)`；未命中返回 None。幂等与 publish 同规（message_id 定格防重并集）。对标 Zeebe `correlate` 端点。api.py 面：MessageService 类已导出，**无新符号、快照契约不动**。

**5.2 等待实例反查**：`MessageService.waiting(name=None, correlation_key=None)` → `[(instance_id, node_id, definition_code, waited_since)]`，查 open 订阅行。CLI：`flowork message waiting`（运维面）。REST/MCP 出面随插件批登记（§12/§13 惯例：先登记不出面）。

**5.3 事件面**：命中=`message_correlated`（既有，node_id 落真实节点）；到时=`timer_fired`（复用，payload 增 kind）；进入等待不发新事件（节点激活本无事件，保持）。

## 6. 校验规则（validator，表驱动扩行）

| 规则 | 级别 | 说明 |
|---|---|---|
| `wait.mode.required` | ERROR | `message` 与 `timeout` 至少声明其一 |
| `wait.reject_target` | ERROR | 驳回目标不可为 wait |
| `wait.correlation.invalid_context` | ERROR | correlation_key 位点词汇白名单：实例变量 / instance.* / now / prev_task / node()；**payload.* 不可用**；取人位词汇不可用 |
| `wait.correlation.syntax` | ERROR | 语法与函数注册表照查；变量引用照查（未声明 ERROR） |
| `wait.output_mapping` | ERROR | 映射右侧：语法与函数注册表照查；变量引用**不查**（mapping_output 先例）；payload.* 与 now 可用，取人位词汇不可用 |
| `wait.timeout.range` | ERROR | after_minutes > 0；**周期/循环形态拒绝**（只有 after_minutes 相对时长） |
| `wait.exit.required` | ERROR | 出边 ≥1 |
| `wait.* 位点` | ERROR | wait.* 仅出现在 wait 节点出边条件（RESERVED_POSITIONS 扩行） |

## 7. 实现落点与测试（预估 2–3 天）

| 落点 | 内容 |
|---|---|
| `definitions/builtin.py` | WaitSpec（message 可选 + output_mappings + timeout）+ 注册（palette 两预设） |
| `tasks/wait.py`（新） | WaitBehavior：activate=（可选）求值+subscribe+（可选）timer 登记；on_matched=按映射写变量+推进；on_elapsed=（可选）关订阅+推进。**推进经 NodeRegistry 行为承接，引擎核心零 kind 分支**（铁律 2） |
| `engine/message_service.py` | correlate_message 入口；publish 命中订阅后回调行为推进（同事务）；waiting 反查；`_correlate` 增按映射写入（与任务输出同一写入路径） |
| `jobs` 既有分发 | timer payload kind="wait_timeout" → 行为 on_elapsed |
| `expr/catalog.py` / `expr/context.py` | wait 三词条入册（result 值域 matched/elapsed）+ 路由注入 |
| `engine/route_audit.py` | snapshot 增 wait 键 |
| `definitions/validator_*` | §6 规则 |
| docs | spec §7.2/§2.4/§2.5/C.4；API.md（correlate_message/waiting）；integration.md；用法手册（四模式：纯延时 / 单实例 / 请求-回复唯一键 / 聚合后置标注） |

**表结构：零新表零新列**（wf_message_subscription / wf_message_buffer / JobRow 全够用）。

**测试清单（先行写红，~16 例）**：等待命中推进；缓冲反查（消息先到）；到时竞速路由；命中与到时同时到（乐观锁序列化先落者赢）；**纯延时到时推进（无订阅无消息）**；**message/timeout 双缺 validator ERROR**；output_mappings 命中映射写入；映射缺键得 None；未声明映射全量合并兜底；暂停期间命中落缓冲、恢复后反查推进；暂停冻结定时器恢复顺延；取消/终止关闭订阅；any 汇聚竞速收尾时败者订阅关闭；循环重入重订阅；correlation 求值错=技术故障；payload 越界=技术故障 publish 失败；correlate_message 反馈三态；signal 唤醒（message_id=null）；validator 八规则。

## 8. 拍板点（v3 后残留一处）

1. ~~暂停语义~~ → 已挂靠既有「暂停全动作拦截」原则 ✓
2. ~~载荷写入~~ → 已回归 spec §7 节点级 output_mappings ✓
3. ~~超时形态/纯延时~~ → 已批：timeout 内置 + **message 可选（纯延时进 v1）** ✓；绝对时刻表达式后置 ✓
4. **发布反馈**〔新增配套，原设计无对应，源自调研〕：同步 `correlate_message`（不缓冲、回传实例、幂等同规）——**待认**
5. ~~结果词汇~~ → wait.* 仅条件位 ✓；**值域改为 matched/elapsed**（v3 名词调整，见 §4 理由）——如有异议可改回 timeout
6. ~~事件~~ → 复用 timer_fired（spec §7 原文语义）✓

## 9. 明确不做（背书+裁定，防动摇）

- **周期性触发（循环定时）**——属定时启动流程的调度域，Job 体系另行解决（**用户 2026-10-09 裁定**）；
- **等到指定时刻（绝对时间/CEL 日期表达式）**——后置项，有真实场景再做；
- **事件网关节点**——图组合表达竞速（any 汇聚竞速收尾既有）；
- **边界事件/中断式等待**——长期观察项（LittleHorse interrupts 是唯一代码系先例）；
- **聚合模式（到截止统一取一批）**——需 drain 新语义，第二刀，等真实需求。
