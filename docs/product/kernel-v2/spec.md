# flowork 流程内核 v2 —— 架构与功能说明书

| | |
|---|---|
| 文档版本 | v2.2（v2.1 基础上按 对标产品 基准版本 行为基准对标＋开源引擎对标增补：all 汇聚放行语义、deadline 列表、实例时限、anticipate API；第七轮评审记录见附录 B） |
| 日期 | 2026-10-07 |
| 上游文档 | [kernel-v2-plan.md](./kernel-v2-plan.md)（调研与选型依据） |
| 读者 | 内核实现者（按本文可直接开写代码）、插件作者、AI 编码代理（本文即防跑偏的契约） |
| 现状标注 | ✓=已实现（以 flowork M0–M6 代码为准）｜◐=已有但需改造｜✗=新增 |

**怎么读**：第 0 章是名词速查（每条一句大白话，全篇术语以此为准）；第 1–8 章讲「系统是什么」（模型层）；第 9–13 章讲「怎么跟它打交道」（接口层）；第 14–16 章讲「怎么开工」（实施层）。凡与现有代码不一致处均显式标注【v2 改造】，不标注即现状已如此。

---

## 0. 名词速查表（全篇大白话基准）

| 名词 | 一句话解释 |
|---|---|
| 流程定义（FlowSpec） | 一张描述「先做什么后做什么」的图纸。存成一份 JSON，放在数据库里，发布后不许改 |
| 流程实例 | 按图纸实际跑起来的一次。张三今天提的那笔报销，就是一个实例 |
| 节点（node） | 图纸上的一个框，表示「到了这一步要发生的事」。每种事一个类型（kind），如审批、发通知 |
| 连线（flow） | 图纸上的一条箭头，表示「这一步走完去哪」。箭头上可以挂条件 |
| 令牌（token） | 实例里「现在走到哪了」的记号。一条并行路径一个令牌 |
| 与汇聚放行（all） | 多条入边的合流点。「到齐」不是死等所有边送令牌，而是「没有在途令牌还能到达这里」——条件没走的分支不阻塞（见 2.3） |
| 超时动作列表（deadlines） | 任务上挂的一串「到某时刻做某事」：先提醒、再升级、最后自动通过。WS-HumanTask 同款形态（见 5.1） |
| 实例时限（deadline） | 整张流程的最大存活时间，到点自动标记/作废/终止。Temporal 同款语义（见第 7 章） |
| 变量（variables） | 这个实例携带的业务数据，比如金额、事由。流程的判断只准看变量，不准现查业务数据库 |
| 人工任务（task） | 流程停在这里，等人在界面上点「同意/驳回」后才继续。它自己不执行任何代码 |
| 系统任务（job/action） | 流程到这里由程序自动干活，不需要人参与 |
| 传阅（cc） | 把这份单子给某些人「看一眼」，不卡流程、不需要他们操作 |
| 会签 | 一个节点要好几个人都处理（或达到某个比例）才算过 |
| 主体（Subject） | 「一个人/一个角色/一个 AI 代理」的统一写法：`user:zhangsan`、`role:manager`、`agent:bot1` |
| 参与者表达式 | 写在图纸上的取人规则，如 `manager_of(initiator)`＝「发起人的上级」。函数由宿主自己注册，内核只管类型和校验，不内置任何取人词汇 |
| 表达式（CEL） | 写条件用的小语言，如 `input.amount > 300`。谷歌为 Kubernetes 造的，不自造语法 |
| 结构化条件 | 不写代码、填表式的条件（字段＋大于/等于/属于…＋值），设计器能画出来，校验最严 |
| 租约（lease） | 外部程序领走一个活时先「租」住它一段时间；到期没交货，别人可以重新领 |
| 幂等 | 同一个请求发两遍，结果跟发一遍一样（不会重复扣款、重复启动） |
| 故障单（incident） | 系统自己搞不定的错误（比如调外部接口连续失败），停下来等人工处理 |
| 死信 | 重试次数用完还失败的活，单独存放，不再自动重试 |
| outbox 事件表 | 「发生了什么」的流水账，和业务数据在同一个数据库事务里写，保证不丢 |
| SPI 端口 | 内核留的标准插座（时钟、组织目录、通知……），插件实现插座，内核只认插座不认插件 |
| 快照 | 实例启动时把图纸整个抄一份存进实例。之后图纸改版，老实例仍按老图纸走完 |
| 身份快照 | 任务完成那一刻，把操作者的岗位、角色、部门原样存进历史。之后人怎么变，都不影响「当时他是谁」的记录 |

## Part A：模型层（系统是什么）

## 1. 范围与非目标

**范围**：流程内核 v2 的完整规格——图纸怎么写（定义）、怎么跑（实例）、任务怎么分派与完成、什么情况停、对外开什么口子。

**非目标**：表单/权限/通知等插件规格（另文）；多副本部署细则；webhook 出站与链路追踪（P2 占位）；BPMN XML 导入导出；提交时自选下一节点（BPMN 系引擎均无此路由模式，人的选择用输出变量＋条件边表达，见 2.4 注）；催办/提醒类动作（UrgeTask 形态，归宿主或通知插件）。

## 2. 流程图纸的三要素：节点、连线、推进策略

> **先说清一件容易误解的事：flowork 没有 BPMN 那种「网关」节点。**这是刻意的（builtin.py 注释原文：「用户词汇对齐现代工作流产品，无『网关』」）。BPMN 四种网关在 flowork 里被拆成三个更简单的机制组合表达，见 2.2/2.3 和映射表 2.4。好处：图上的框只表示「要做的事」，路由规则全部挂在箭头和节点的推进策略上，概念少一半。

### 2.1 节点（node）：图上的框

**定义**：节点表示「流程走到这一步要发生的一件事」。每个节点 = 公共字段 + 类型字段（由 kind 决定）。

**公共字段**（基类 `FlowNode`，definitions/node.py ✓）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| code | str | ✓ | — | 节点编码，图内唯一，连线用它互相引用 |
| kind | str | ✓ | — | 节点类型：start/end/user_task/service_task/call_activity/cc_task/branch，或自定义注册的类型 |
| name | str | — | "" | 显示名 |
| advance_policy | "any"/"all"/null | — | null | 推进策略（见 2.3）：多条入边到齐再开始（all）还是任一到即开始（any） |
| hooks | NodeHooks | — | 空 | 四个生命周期钩子的声明式绑定（见 2.6 层 1） |
| properties | dict | — | {} | 扩展袋：UI/外部系统放附加配置，引擎不解释 |

**类型机制（可扩展性的根）**：kind 是节点注册表（`NodeRegistry`）的键。注册一个新类型要交三样东西：Spec 类（数据字段，pydantic 校验）、Behavior 类（运行行为）、ui 元数据（设计器展示，可选）。**内置七种类型与自定义类型走完全相同的注册入口**（builtin.py 的 `register_builtin_nodes` 与用户代码调同一个 `register`，这是「官方插件不吃小灶」铁律的落点 ✓）。加载图纸时遇到未注册的 kind 直接报错（设计期拦截 ✓）。

### 2.2 连线（flow）：图上的箭头

**定义**：从节点 A 指向节点 B 的一条边，表示「A 走完可以到 B」。**路由条件挂在箭头上，不挂在节点上**——这是 flowork 与「网关节点」式引擎（Camunda）的核心差异。

字段（`SequenceFlow`，definitions/flow.py ✓）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| source | str | ✓ | — | 起点节点 code |
| target | str | ✓ | — | 终点节点 code |
| name | str | — | "" | 显示名 |
| is_default | bool | — | false | 默认边：所有条件边都不命中时走它 |
| condition | FlowCondition/null | — | null | 路由条件（写法见第 4 章） |
| on_take | HandlerRef/null | — | null | 走这条边时触发的钩子（见 2.6 层 1） |

**并行不需要任何特殊配置**：一个节点有多条**不带条件**的出边，令牌就同时走多条（自动并行）。这是规则不是节点。

### 2.3 推进策略（advance_policy）：多条入边怎么合流

**定义**：一个节点有多条入边时，决定「到几条才开始」：

| 值 | 语义 | 对应 BPMN |
|---|---|---|
| any（缺省） | 任一入边到达即开始，并取消其他还在路上的兄弟分支（竞速） | 排他/默认合并 |
| all | 与汇聚：按下方放行判定，全部入边满足才开始 | 并行/包容网关的合并侧 |

汇聚语义在**目标节点**上配置，不在分支节点上（对齐 对标产品 preCondition；cc_task 恒为 any ✓）。加上 2.2 的「无条件多出边=并行」，一拆一合就是完整的并行块。

**all 的放行判定【v2.2 定稿，修复现状死锁】**：判据只看**未完令牌**——一条入边视为「已满足」⇔ 不存在任何未完令牌能沿图经该入边到达本节点。全部入边满足即放行；任一边到达时重新评估。等价总判据：**除已停在本节点的令牌外，没有任何未完令牌能沿图到达本节点**（结构可达性，不评条件——条件为假的在途分支最终会落定，落定后即不再阻塞）。

三条推论：来源已落定且无在途可达 → 满足（正常并行等齐）；来源从未持有令牌 → 满足（条件分支未命中，不死等）；**已落定的旧令牌不算数**——循环迭代里上一轮的 settled 令牌不能让这一轮提前放行（顺手修掉现状「存在任意 settled 令牌即视为满足」的提前放行坑）。

为什么不是「等所有入边送来令牌」：上游条件分支未命中的边永远不会有令牌，死等＝永久卡死（现状 kernel 的缺陷，v2 修复）。本语义与 Flowable/Camunda 7（`InclusiveGatewayActivityBehavior`：静态可达性＋活跃执行扫描）和 Zeebe 8.6（camunda/camunda#10031，8.1 只做分叉、8.6 才补齐汇聚）同族；对标产品 的「来源无令牌＝满足」是它在「同轮分支令牌同步建齐」模型下的简化特例。可达性检查**不可**省略成「无令牌即可」——消息等待节点（第 7 章）引入异步到达后，「现在没令牌」不再等于「不会来」。

实现提示：判定＝图遍历（各未完令牌所在节点的正向可达集 ∩ 本节点入边的来源集）；定义不可变，可达集可按 (definition, node) 缓存。驳回归零、管理员 move_token 等直调 activate 的路径不经放行门（现状如此，保持）。

### 2.4 BPMN 网关 → flowork 映射（给读过 BPMN 的人）

| BPMN 概念 | flowork 表达 |
|---|---|
| 排他网关（XOR，走一条） | branch 节点（route_mode=first_match）＋出边挂条件；或任意节点直接挂多条条件出边 |
| 并行网关（AND，全走） | 任意节点多条无条件出边（自动并行）＋ 汇聚点 advance_policy=all（放行判定见 2.3） |
| 包容网关（OR，命中都走） | branch 节点（route_mode=all_match）＋ 汇聚点 advance_policy=all（可达性放行使未命中分支不阻塞，见 2.3） |
| branch 节点本身 | 纯路由锚点，无副作用。价值：决策落点进审计日志、静态校验有挂靠、AI 改流程时有明确操作对象 |
| 事件网关/消息等待 | 【v2 新增】等待节点（见第 7 章），首版后置 |

**关于「提交时自选下一节点」**：BPMN 系引擎（jBPM/Activiti/Flowable/Zeebe）均无此路由模式，flowork 也不做内核语义——人的选择用**输出变量＋条件边**表达（对标产品 的 alternativeFlag 属标准外私货，主流量表不跟）。设计器可提供「选择题」交互把选项落成枚举变量，引擎语义零新增。

### 2.5 内置七种节点类型（kind）字段表 ✓

| kind | 一句话 | 专有字段（公共字段见 2.1） |
|---|---|---|
| start | 起点，一个图一个 | 无 |
| end | 终点，令牌到这里消失；全消失则实例完成 | 无 |
| user_task | 人工任务：等人操作（定义详见第 5.1 章） | assignee/assignees/candidate_users/candidate_groups（取人，支持表达式）；multi_instance（会签，见下）；reject_target（驳回到哪个节点）；auto_pass_originator/previous/participated（命中者自动通过）；no_participant_policy（找不到人：raise 报错/to_admins 转管理员）；deadlines（超时动作列表，见 5.1；【v2.2】现状单策略 timeout 为其退化形态，validate 兼容读取） |
| service_task | 系统任务：程序自动执行（详见 5.2） | action（在 HandlerRegistry 注册的动作名，引擎内形态）；params（参数字典）；【v2】mode: handler\|worker（worker＝外部程序领取执行，此时 job_type 声明任务类型，见 5.2） |
| call_activity | 子流程：调起另一个流程定义当一个节点用 | process_code；is_synchronous（同步等它跑完/异步不管）；input_mappings/output_mappings（变量进出映射） |
| cc_task | 传阅：给人看一眼，不卡流程 | recipients（接收人列表，支持表达式）；advance_policy 恒 any |
| branch | 条件分支锚点 | route_mode：first_match（命中一条即走）/all_match（命中的都走） |

**会签（MultiInstance，user_task 专属字段）**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| mode | parallel/sequential | parallel | 并签（全员同时待办）/串签（一个批完下一个） |
| approve_exit | str/null | null | 通过出口：`"all"`（全同意）、`">=1"`、`">=50%"` 等比例式 |
| disapprove_exit | str/null | null | 驳回出口：同上语法 |

出口判定是**专用语法**（不是 CEL，见 4.1），比例基数＝参与人数。

### 2.6 怎么加一种新节点（两层扩展，全部不用改内核）

**层 1——声明式钩子（无状态动作，写在图纸里）**：任何节点可配 `hooks`，四个时机各绑一个已注册的处理器：

```
NodeHooks: on_enter（进入节点时）/ on_exit（离开时）/ on_cancel（被取消时）/ on_fail（失败时）
HandlerRef = { handler: "处理器名", params: {...} }   # 按名从 HandlerRegistry 解析
```

**层 2——全代码 Behavior（有状态的完整行为）**：继承 `NodeBehavior`，按需覆写五个钩子：`on_enter / on_exit / on_cancel / on_fail(ctx, error) / on_task_completed(ctx, task_id, outcome)`（runtime/behavior.py ✓，钩子统一接收 NodeContext；引擎传入的 RuntimeNodeContext 才提供内核服务，行为内 `assert isinstance` 收窄）。

**完整示例（自定义 http_request 节点）**：

```python
from flowork.definitions.node import FlowNode
from flowork.runtime.behavior import NodeBehavior

class HttpRequestSpec(FlowNode):
    kind: str = "http_request"        # Spec：声明字段（pydantic 自动校验+序列化）
    url: str = ""
    method: str = "GET"

class HttpRequestBehavior(NodeBehavior):
    def on_enter(self, ctx):          # Behavior：覆写需要的钩子
        ...  # 经 ctx.kernel 建一个 Job，交给 worker 或内部执行

node_registry.register("http_request", HttpRequestSpec, HttpRequestBehavior,
                       ui={"name": "HTTP 请求", "category": "自动化", ...})
```

注册后：图纸 JSON 里写 `"kind": "http_request"` 即可用；校验、序列化、设计器调色板、semantic diff 自动获得。**这就是「要素足够抽象可扩展」的验证标准：加新要素=注册，不是改内核。**

## 3. 数据结构总纲

### 3.1 流程定义（图纸 JSON）完整结构 ✓（v2 两处改造）

顶层（`ProcessDefinition`，definitions/definition.py）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| code | str | — | 流程编码，全局唯一标识 |
| name | str | "" | 显示名 |
| version | int | 1 | 【v2 改造】拆为 major + revision（见第 6 章） |
| status | published / retired | published | 【v2】取消 draft（见 6.2）：deploy 即 publish；retired 不接新实例 |
| nodes | list[FlowNode] | [] | 节点列表（各 kind 字段见 2.5） |
| flows | list[SequenceFlow] | [] | 连线列表（见 2.2） |
| variables | list[ProcessVariable] | [] | 变量声明。**现状：仅供设计器变量选择器与 dry-run 取样，不约束运行时传参**（见 3.2 的 v2 改造） |
| deadline | {after_minutes, strategy} / null | null | 【v2.2 新增】实例级时限：整单最大存活时间，strategy=mark/cancel/terminate（见第 7 章）。对齐 Temporal Workflow Execution Timeout（原生实例级超时）；BPMN 系引擎需以子流程边界定时事件建模，flowork 取声明式字段 |
| layout | dict[code→{x,y}] | {} | 画布坐标，纯展示数据，引擎不消费，semantic diff 忽略 |

`ProcessVariable`：`{name, type: string|number|boolean|list, label, sample}` ✓。

### 3.2 变量的「声明」与「契约」【v2 关键改造，现状与目标的差异要读清】

**现状（✓，与很多人直觉相反）**：`variables[]` 只是设计器元数据——发起时多传、少传变量引擎都接受，后果由条件求值自然承担（引用不到即条件不成立）。

**v2 改造（◐）**：新增顶层 `input` 字段，值为 **JSON Schema**（2020-12，由 pydantic 模型生成）。它**不是**元数据，是强制契约：

- 发起实例时、人工任务提交 output 时、节点输出映射时，三处边界按 schema 校验，不合格**直接拒绝**（报错不入库）；
- `variables[]` 保留（设计器体验不变），但 `validate` 会检查两者矛盾（input 里没有的变量出现在 variables 里→警告）；
- 例子见 3.3 的完整图纸。

**运行时变量存储**：实例 JSON 列（现状 ✓）；【v2】searchable 字段同步进 `wf_variable_index`（第 14 章）；单实例变量序列化上限 1MB。

### 3.3 完整图纸示例（含 v2 新增字段）

```json
{
  "code": "expense-approval",
  "name": "报销审批",
  "major": 1, "revision": 3, "status": "published",
  "deadline": {"after_minutes": 10080, "strategy": "cancel"},
  "input": {
    "type": "object",
    "properties": {
      "amount": {"type": "number", "description": "金额"},
      "reason": {"type": "string"}
    },
    "required": ["amount", "reason"]
  },
  "variables": [
    {"name": "amount", "type": "number", "label": "金额", "sample": 500},
    {"name": "reason", "type": "string", "label": "事由", "sample": "差旅"}
  ],
  "nodes": [
    {"code": "start1", "kind": "start"},
    {"code": "fill",   "kind": "user_task", "name": "填写报销单", "assignee": "initiator"},
    {"code": "check",  "kind": "branch", "route_mode": "first_match"},
    {"code": "mgr",    "kind": "user_task", "name": "主管审批",
      "assignee": "manager_of(initiator)",
      "multi_instance": {"mode": "parallel", "approve_exit": "all"},
      "reject_target": "fill",
      "deadlines": [
        {"after_minutes": 1152, "action": "notify"},
        {"after_minutes": 1440, "action": "escalate"}
      ]},
    {"code": "notify", "kind": "service_task", "action": "send_slack",
      "params": {"channel": "#finance"}},
    {"code": "end1",   "kind": "end"}
  ],
  "flows": [
    {"source": "start1", "target": "fill"},
    {"source": "fill",   "target": "check"},
    {"source": "check",  "target": "mgr",
     "condition": "input.amount > 300"},
    {"source": "check",  "target": "end1", "is_default": true},
    {"source": "mgr",    "target": "notify"},
    {"source": "notify", "target": "end1"}
  ],
  "layout": {"start1": {"x": 40, "y": 200}, "fill": {"x": 180, "y": 200}}
}
```

### 3.4 运行时对象（实例跑起来后数据库里有什么）

| 表/对象 | 字段（现状 ✓，【v2】为改造） | 说明 |
|---|---|---|
| wf_process_instance 流程实例 | id；definition_code；definition_version【v2→major+revision】；**definition_snapshot**（发起时图纸全文抄本，不可变——快照原则的实现）；business_key（业务单号，仅查询用）；state（running；【v2】扩 paused/terminated）；start_user；variables（JSON）；parent_id/parent_token_id（作为子流程时的父指针）；deadline_at【v2.2】（实例时限到期时刻，图纸顶层 deadline 按 WorkCalendar 折算后物化）；version（乐观锁）；started_at/finished_at/updated_at | 一次流程运转的全部持久状态 |
| wf_token 令牌 | id；instance_id；token_no（实例内自增）；activity_code（当前在哪个节点）；source_activity_code（从哪来）；state（unfinished/finished）；item_count/approve_count/disapprove_count（会签 应办/同意/驳回计数）；approval_exit（会签出口判定结果）；reject_flag（驳回产生的令牌）；manual_participants（人工调整的参与人）；created_at/finished_at | 「走到哪了」的记号；会签计数在此不在任务上 |
| wf_task 人工任务（活表） | id；instance_id；token_id；activity_code；name；assignee；task_type（approval/assist）；state（ready/completed/cancelled）；context_payload（审批卡片数据，AI 预审共用）；root_task_id（加签/委派链，指回原任务）；【v2】priority/due_at；created_at | 还没办完的人工任务 |
| wf_task_history 已办（只插入不改） | 同上 + task_id；outcome（agree/reject/transfer/auto_pass/withdrawn）；comment；acted_by；**acted_as_agent**（是否 AI 代理代办）；**idempotency_key**（完成幂等）；**actor_context**（身份快照 JSON 袋，完成事务内定格，见 5.6）【v2.1】；cause_token_id（驳回复活的关联）；cost_seconds（耗时）；created_at | 审计的唯一真相，append-only |
| wf_job 系统任务 | id；job_type（timer/async）；state（scheduled/…/dead-letter 分级）；run_at；retry_count/max_retries（默认 3）；instance_id；payload；result_payload；last_error；【v2】worker_id/lease_until/next_retry_at | 定时与异步执行；重试与死信状态机在此 |
| wf_incident 故障单 | id；instance_id；message；error_class；state（open/resolved）；resolution（resume/cancel，【v2】扩 retry/skip/fail）；created_at/resolved_at | 系统自己搞不定的错误 |
| wf_event_outbox 事件流水 | id（UUID）；event_name；instance_id；payload；occurred_at | 与业务变更同事务写入 |
| wf_identity_link 任务-人关联 | task_id；user_id；link_type（candidate） | 候选人/候选组 |
| wf_cc_task 传阅件 | instance_id；activity_code；recipient；title；context_payload；is_read/read_at | 传阅状态 |

### 3.5 定义的存储与生命周期：文件 → 数据库 → 内存 → 快照

四层存储，各有职责，不是四选一：

```
git 里的 YAML/JSON 文件      ← 编辑态事实来源：人和 AI 只改这里（PR/diff/CI/回滚全用 git 机制）
        │  deploy（校验 + 版本判定）
        ▼
数据库 wf_process_definition  ← 运行态事实来源：发布即不可变，content 列存完整图纸 JSON
        │  进程内只读缓存（key = code + major + revision）
        ▼
实例的 definition_snapshot    ← 每实例自带图纸抄本 ✓，跑起来后不再查任何定义
```

规则：不直接读文件运行（定义需要版本身份与不可变性，多副本需共享，容器文件易失）；缓存不需要失效机制（发布不可变 ⇒ 同 key 永远同内容，仅删除定义等管理操作按 key 驱逐；**不引入 Redis 等外部缓存**）；快照是第三道保险（定义表清空不影响在跑实例）。

## 4. 表达式与条件（横切能力：谁都用它，单独一章）

> 本章回答「条件写在哪些地方、每种地方用什么写法」。后面所有章节（连线、取人、超时、消息、子流程映射）一律引用本章，不再各自定义语法。

### 4.1 两种写法，各管一摊【v2.3 单轨化定稿，2026-10-08】

| 写法 | 用在哪 | 现状→v2 |
|---|---|---|
| **CEL 公式**（`SequenceFlow.condition` 及取人/超时/关联键/映射表达式） | 连线路由条件（**单轨**：condition 直接存公式字符串）；以及所有「需要一个值」的地方 | ✓ 全位点已接线（**位点统一批，2026-10-08**：取人/传阅/超时改派/子流程映射均 CEL，见 4.2；关联键随等待节点） |
| **专用出口语法**（`">=50%"` / `"all"` / `">=1"`） | 仅会签 approve_exit/disapprove_exit | ✓ 保留（比例基数＝参与人数，CEL 表达不了百分比语义，不硬套） |

**单轨化决策**（结构化条件组 groups 退役，理由成文）：
- flowork 的用户画像＝开发者+AI agent（engine-v1 三面同构），非低代码业务人员——
  groups 的核心受益人不在目标用户内；主流开源引擎（Flowable/Camunda 7 的 UEL、
  Zeebe 的 FEEL）均为单一表达式语言，无双轨存储格式。
- CEL 静态检查（§4.3）已追平 groups「天然合法」的可靠性优势。
- AI 生成 Spec 时写一行 CEL 比嵌套 groups 结构更自然。
- 可视化点选体验不丢：设计器将以下拉拼装**生成 CEL 文本**（辅助输入而非第二存储语言）。
- 兼容：**零兼容**（系统早期，用户拍板 2026-10-08）——不做旧格式翻译，存量模板/测试
  直接迁移；错误语义统一为求值错→技术故障（挂起+故障单，spec §8）。
- **位点统一**（同日批次）：取人（assignee/assignees/recipients 每项一表达式，字面人
  写 `'u_a'`、`originator` 魔法词退役为 `initiator`）、超时改派（fire 时求值，task.* 可用）、
  子流程映射（输入父上下文/输出子上下文；裸变量名是 CEL 子集零迁移）全部换绑
  evaluate_formula_value；取人/传阅/改派/映射求值错同样挂起+故障单；ParticipantFunctions
  双注册面退役，ExpressionFunctions（cel_vocabulary）成唯一注册面。

### 4.2 CEL 的使用位置清单（全篇唯一清单）

| 位置 | 字段 | 示例 | 状态 |
|---|---|---|---|
| 连线条件 | flows[].condition（CEL 字符串） | `input.amount > 300 && input.urgency == "high"` | ✓ |
| 取人 | user_task/cc_task 的 assignee/assignees/recipients（每项一表达式） | `'u_chen'`、`manager_of(initiator)`、`users_with_role("finance")`（函数来自 contrib 或宿主注册，见 5.5；null/空=查无传播） | ✓ |
| 超时改派 | deadlines[].action=reassign 时的 reassign_to | `manager_of(task.assignee)`（fire 时求值，task.* 见 C.4；结果须单个非空人） | ✓ |
| 消息关联键 | 等待节点的 correlation_key | `"input.order_no"`（求值结果用于匹配） | 随等待节点 |
| 子流程映射 | input_mappings/output_mappings 的右侧 | `input.amount * 1.0`（输入在父上下文、输出在子上下文求值；裸标识符缺键→None） | ✓ |

### 4.3 CEL 求值语义【v2 改造】

- **变量命名空间**：`input.*`（流程契约输入）、`vars.*`（运行中变量）、`task.*`（任务快照，当前仅超时改派位注入，见 C.4）、`initiator`（发起人 Subject，全位点注入）。
- **自定义函数**：经 SPI 端口 `ExpressionFunctions` 注册（第 9 章），**必须声明参数与返回类型**（词汇表 one_user/users/groups/mixed/bool，兼容性检查见 5.5）。**内核函数注册表出厂为空**——取人词汇全部由宿主注册（v2.1 定稿，理由见 5.5）；`flowork.contrib` 便利包提供 `manager_of` / `users_with_role` / `has_role` 三个常见词汇，纯基于 OrgDirectory 端口实现，一行代码挂上。
- **设计时检查**：`validate` 阶段做 CEL 静态检查（**全位点**，validator_conditions）——
  编译护栏/引用未声明变量/未注册函数与参数个数直接报错（K8s CRD 校验同款体验）；
  取人与改派位的根调用另做 returns 类型对位（谓词 bool 误用发布期即拦）。
- **运行时限制**：非图灵完备；表达式长度上限 2KB、二元运算链 ≤256 项、变量序列化 ≤1MB（P0-1 决策护栏；进程内硬超时为已知缺口）。
- **P0-1 先做 PoC**：cel-python 的函数注册/类型检查/性能验证；不达标换 cel-expr-python（C++ 绑定），本章接口不变。

## 5. 任务：人工任务与系统任务（定义清楚，不许含糊）

### 5.1 人工任务（user_task）

**一句话定义**：流程走到这个节点就**停下来**，等一个（或一组）人在界面上做出动作——同意、驳回、转办——之后才继续。人工任务自己**不执行任何代码**，它只是「等人」的占位，外加一张给操作者看的数据卡片（context_payload）。

**怎么产生**：令牌进入 user_task 节点 → 行为按取人字段算出参与者 → 每人（或会签每人）生成一条 wf_task + 候选关联 → 发 `task_created` 事件 → 令牌停住。

**怎么算完成**：

- 单人节点：assignee 调 complete（带 output，按 3.2 契约校验后写入变量）→ 令牌离开。
- 会签节点：按 approve_exit/disapprove_exit 判定（专用语法，4.1）——比如 approve_exit=">=50%" 且 3 人中 2 人同意即过；驳回出口同理。
- 驳回：outcome=reject，令牌按 reject_target 回退（未配则回上一人工节点）；被驳回的任务复活=生成**新**任务行，旧行进历史（cause_token_id 关联）✓。**驳回落点豁免自动通过【v2.2 成文】**：驳回产生的令牌、经 manual_participants 定格的参与者**不应用** auto_pass_* 策略——否则「发起人驳回→重新提交→又被 auto_pass 跳过」原地打转；现状由解析器 manual 短路隐式实现（participants/resolver.py），此处升格为语义保证，防未来重构丢掉（对标产品 doOnRejectedActivity 同规则）。

**任务动作全集（每个都是 API，详见第 11 章）**：claim（从候选里认领）/ unclaim / complete / reject / transfer（转办，保持待办换人）/ assist（委派：生成 task_type=assist 的子任务，root_task_id 指回原任务，原任务保留在负责人名下）/ complete_assist（受托人交还）/ add_countersigner（加签）。

**任务状态（对齐现状存储，不新增状态列）**：活表 state ∈ `ready | completed | cancelled`；「已认领」＝ ready 且 assignee 已固定（不设独立 claimed 态）；终态语义在历史的 outcome 里（agree/reject/transfer/auto_pass/withdrawn，✓ 现状值）。已被他人认领再认领 → `TaskAlreadyClaimedError`。

**超时【v2.2 定稿：deadline 列表形态，对齐 WS-HumanTask】**：任务可挂一组有序动作 `deadlines: [{after_minutes, action}]`（自进入节点起算，按 WorkCalendar 工作日历折算——SPI 端口，默认自然时间；after_minutes 必须为正整数，同一节点上限 8 条）。动作枚举五种：

| action | 行为 |
|---|---|
| notify | 提醒，不处理（多条 notify deadline＝多级预警：T-3 天、T-1 天…各提醒一次） |
| reassign | 按 4.2 CEL 表达式改派人（超时求值可用 task.*） |
| escalate | 引擎自动转上级：调 OrgDirectory.manager_of（宿主实现可内部沿部门树向上），查无上级 → 故障单，不静默丢 |
| auto_approve | 自动通过 |
| auto_reject | 自动驳回 |

- `auto_approve`/`auto_reject` 会终结节点等待，之后的 deadline 不再触发；`reassign`/`escalate` 改写等待人后，剩余 deadline 继续对新任务计时。
- wf_task.due_at 物化首条 deadline 的到期时刻（due_before 过滤与排序用，§12）。
- **现状 `TimeoutPolicy{due_minutes, strategy}` 是单条 deadline 的退化形态**（strategy=notify → `[{after_minutes, notify}]` 一一映射），validate 兼容读取旧字段、semantic diff 视两种形态等价。
- 形态依据：WS-HumanTask 1.0（OASIS，jBPM 完整实现）即「多 deadline 各挂 escalation（notification / reassignment）」；对标产品 的 timeoutWarning1..5 是其劣化实现（预警水位写死五个字段）。Camunda 7/Flowable 无内建人工任务升级（要边界定时器＋监听器自搭），flowork 直接取标准形态。
- 实例级时限是**另一个字段**（图纸顶层 `deadline`，第 7 章），不混在任务 deadlines 里。

**AI 的位置**：assignee 可以是 `agent:xxx` 主体；人也可以让 AI 代点（历史行 acted_as_agent=true 记账 ✓）。谁有权限点，属权限插件（M9），内核只记事实。

### 5.2 系统任务（service_task）

**一句话定义**：流程走到这个节点由**程序自动执行**一段动作，不需要人参与。执行体分两种形态：

| 形态 | 怎么跑 | 适用 |
|---|---|---|
| **引擎内动作（action）**✓ | 图纸写 `action: "send_slack", params: {...}`，动作是注册进 HandlerRegistry 的 Python 函数，引擎调它 | 和宿主同进程的轻动作 |
| **外部 worker 领取**【v2 新增】 | 节点声明任务类型（如 `job_type: "http_call"`）→ 引擎生成一条 wf_job → 外部程序（脚本/AI 代理）**领走执行、干完汇报** | 重活/异构技术/独立部署 |

**外部 worker 的领取-汇报协议【v2】**（对齐 Zeebe 语义）：

1. `activate_jobs(types, max, visibility_timeout, request_timeout)`：批量领活。领到即「租」住（写 worker_id + lease_until）；
2. 干完 `complete_job(job_id, output)`；失败 `fail_job(job_id, error, retries, backoff)`——还剩几次重试由 worker 自己报；
3. **租约到期没交货 → 别人可以重新领**。因此交付语义是「至少一次」，worker 必须自己幂等（文档级承诺，写进 AGENTS.md）；
4. 重试参数：默认初次 1 秒、每次翻倍、单次上限 100×初次、默认最多 3 次（与现状 max_retries=3 一致）；次数耗尽 → 死信 + 故障单。

**失败不重试的口子**：`non_retryable_types` 可配错误类型清单，命中直接进故障单。

### 5.3 两个辅助节点

- **传阅（cc_task）**：给某些人「看一眼」，生成传阅件（可标已读），**不产生待办、不卡流程**（恒 any 推进）✓。
- **子流程（call_activity）**：把另一个流程定义当一个节点调用。同步=等子流程跑完才继续（子实例 parent_id 挂回父实例）✓；input/output mappings 做变量进出（右侧写 CEL 表达式，4.2）。

### 5.4 主体（Subject）：统一写「一个人/角色/代理/部门」

格式：`user:{id}` | `role:{name}` | `agent:{id}` | `org:{code}`（前三种 ✓ 现状，`org` 【v2】新增——部门树场景：candidate_groups 可挂 org:，认领时按目录判断归属）。凡写人的字段（assignee/candidate/recipients/start_user）都用此格式。内核**不存**用户表、不校验用户存在（对齐 C7 IdentityService 惯例）；表达式里的取人由宿主注册的函数完成（5.5），引擎需要的「人↔组」关系判断走 OrgDirectory 端口（第 9 章）。

**宿主视角使用模型（三句话）**：进——发起时给 `initiator`，节点取人要么图纸直接写 Subject、要么从契约输入（`input.approver`）拿、要么注册自定义函数教内核算；记——token 表记走到哪个节点、task 表记谁在办、history 表记谁办过（含身份快照，5.6）；出——查询 API（我的待办、实例详情、搜索）随时读这些事实。内核是「只认 id、不管 id 是谁」的流转记账引擎。

### 5.5 参与人解析：类型系统【v2.1 定稿：函数零内置，内核只保语法】

**分工铁律**：取人词汇（组→人的函数）全部由宿主注册，**内核注册表出厂为空**；内核只保留「语法」——类型词汇、注册契约、兼容性检查、传播规则。理由与节点注册表同源：官方不该在表达式求值器里焊死词汇（师徒制、项目制组织根本没有 manager），且内置表与类型枚举必然漂移（v2.0 的 org 返回类型缺口即例证）。

**类型词汇（参数与返回共用，另含 CEL 原生标量 string/number/boolean/list/map）**：

| 类型 | 含义 | 形态 |
|---|---|---|
| one_user | 单个人，查无可为 None | Subject 字符串 |
| users | 多个人（已展开） | list[Subject] |
| groups | 组引用（未展开） | list[Subject]，元素限 role:/org: |
| mixed | 混合集 | ParticipantSet |
| bool | 谓词 | 布尔 |

数据形态统一为 `ParticipantSet`（one_user/users/groups 由引擎自动提升）：

```
ParticipantSet = { subjects: [user:*|agent:*],   # 具体的人（已展开）
                   groups:   [role:*|org:*] }    # 组引用（未展开，保留动态性）
```

**两个槽不可拍平**：组引用在候选制下不展开（明天入组的人能认领明天的待办），指派/会签制下展开定格（快照语义）。组展开＝**直接成员，不含子部门**；要含子部门，宿主注册递归函数自行实现。

**注册契约**：宿主经 `ExpressionFunctions` 端口（第 9 章）注册，必须声明参数与返回类型，校验器做使用位置兼容性检查（P0-1 PoC 验证点：类型签名喂给 CEL 静态检查）：

```python
register("team_leader",  fn, params=["one_user"], returns="one_user")
register("on_call",      fn, params=[],           returns="users")
register("finance_path", fn, params=[],           returns="groups")
register("approvers_of", fn, params=["one_user"], returns="mixed")
```

**`flowork.contrib` 便利包**（可选，一行挂上，纯基于 OrgDirectory 端口实现）：`manager_of(one_user)→one_user`（吃 user:/agent: 返回其上级；吃 org: 返回部门负责人——部门负责人数据由宿主目录自定；吃 role: 设计期报错）、`users_with_role(string)→users`、`has_role(one_user, string)→bool`。部门类词汇（org_of、dept_leader 等）的数据源无端口支撑，不进 contrib，由宿主注册。

| 使用位置 | 接受的返回 | 引擎行为 |
|---|---|---|
| assignee（单人） | one_user | 进入节点展开定格为 1 人；空/多解 → no_participant_policy |
| assignees/会签 | users、mixed | 展开定格为 N 人 |
| candidate 槽（candidate_users ∪ candidate_groups 联合声明） | users、groups、mixed | mixed 自动拆槽：subjects 计入 candidate_users，groups 计入 candidate_groups；groups 保留引用，认领时再展开 |
| 连线条件 | 表达式整体必须为 bool | 见下 |

**连线条件三规则**（条件不操作集合结构，只比较与谓词）：① 单主体是字符串，`==` 直接可用；② 多人集合是字符串列表，`in` 成员测试可用；③ 组的问题用谓词：`has_role(vars.last_handler, "finance")` 返回 bool，不把组展开进条件。

**查无结果的传播（语义契约，宿主函数须遵守）**：函数查无一律返回 None/空集合，**不抛异常**；组合表达式中 None 短路传播（整个取人表达式结果为空）；落到取人位置后走 no_participant_policy。异常只留给「目录服务不可用」类技术故障 → 故障单（对齐第 8 章错误二分）——抛异常的宿主函数＝宣告技术故障，引擎照此处理。

**规范化（引擎统一处理）**：None→空集合；空集合→no_participant_policy；subjects 槽出现 role:/org: → 设计期报错（写错槽）；去重＋按字符串排序（目录返回顺序不可信，排序保证解析结果确定、审计可重放——重放靠任务定格快照＋走线记录，不重新取人）。

**检查强度（诚实声明）**：Subject 在 CEL 里本质是 string，静态检查只保证「是个字符串」，不保证「是合法 Subject」；槽合法性由运行时规范化兜底（上一条）。

| 求值时机 | subjects | groups |
|---|---|---|
| 进入节点（指派/会签） | 展开定格进任务 | 同样展开定格 |
| 进入节点（候选制） | 定格为候选 | 保留引用，认领时展开 |
| 超时 reassign/升级 | 重新求值（可用 task.*，见 C.4） | 重新求值 |
| 条件求值 | 即时比较 | 走谓词即时 |

（位点统一注：assignee/assignees/recipients 为**进入节点**逐项 CEL 求值；子流程输入映射在子实例启动时按父上下文求值、输出映射在子完结时按子上下文求值；静态检查对全位点同一套——见 4.3。）

### 5.6 身份快照（actor_context）【v2.1 新增：审计的严肃性要求】

**问题**：流程审计要回答的不只是「当时是谁审的」，还有「**他当时是谁**」——岗位、角色、部门。组织数据在宿主那边且会变，一年后光有 `user:zhangsan` 和时间戳，答不出「张三凭什么有权限批这笔单」。

**机制（内核管定格的时机，宿主管定格的内容）**：

- 任务完成的**事务内**（complete/reject/transfer/auto_pass 等所有动作），引擎调 `OrgDirectory.snapshot(subject)`，把返回 dict **原样**存进 `wf_task_history.actor_context`（JSON 袋，上限 4KB，超限拒收）；内核不解析、不校验、不理解内容。
- 默认实现返回 `{}`（裸跑/单测不受影响）；想要完整审计的宿主自行实现，存什么、存多细是宿主的审计策略（如 `{"dept", "title", "roles"}`）。
- 必须在完成事务内定格：事后补录存在时间窗口，组织一变补出来的就是错的；history 本来就 append-only，写入即不可篡改。
- AI 代办（acted_as_agent=true）照常定格**人**的上下文，代理事实由既有列另行记录。

**可选加强（记下不做）**：身份快照解决「他是谁」，审计有时还要「**为什么是他**」——assignee＝`manager_of(张三)` 定格成李四，一年后上级已换成王五。同款机制可在节点解析参与人时把解析依据（当时的上下级关系）一并定格，成本略高，待真实需求出现再做。

## 6. 流程定义的版本与发布【v2 定稿】

### 6.1 哲学三句与一个承诺

**发布即不可变；实例钉快照；默认不迁移。**设计承诺：**运行中实例只读自己的 definition_snapshot，定义表对它们只写不读**——定义被更新甚至删除，在跑实例无感。**start 永不读磁盘**：启动事务内 SELECT 最新 published（数据库），磁盘文件未经 deploy 对运行时完全不可见，变更边界＝deploy 时刻（deploy 前新实例全走旧版、之后全走新版，无意外分裂；漏部署检查靠 definition_drift 对账，不靠引擎监听文件——自动部署属 CI 职责）。由此推出：进程内定义缓存（key=code+major+revision）永不失效，**无缓存一致性协议**（无 TTL/无失效广播/无外部缓存）；多副本不需要 LISTEN/NOTIFY（PG 的留作未来优化）；`start(code)` 不带版本时在启动事务内现查 latest published——**运行中程序不需要感知定义更新**，展示与对账需求由事件+查询满足（6.5）。

不迁移的立场（用户定稿）：迁移不做；逃生门＝开发人员自行改库（文档须明确哪些表能动及后果，优先引导用 move_token 做单实例手术）。适用边界：实例生命周期以天/周计的审批场景；数月级长流程超出本设计假设。

### 6.2 状态机与 draft 的取消【v2 定稿】

状态只有两个：`published`（活跃，接新实例）→ `retired`（不接新实例报 `DefinitionRetired`，在跑跑完，行保留）。**DB 无 draft 状态**（终态砍掉，单一事实源：编辑态在 git/设计器本地）；对现状 draft/published 的迁移随 P0-4 处理。恢复 retired＝重新 deploy 同内容为新版本。版本号：`code + major + revision`，同 code 允许对老 major 继续发 revision（维护分支）；新实例默认最高 published，可显式钉版本启动。

### 6.3 破坏性判定：机制、默认规则、输出、时机

- **机制**：每条规则＝semantic_diff 的检查器（注册式，同节点注册表哲学）；默认四条，宿主可加严/放宽；deploy 可 `force_major`/`allow_minor` 覆盖（**必须带 reason**，进事件）。
- **默认规则**（与已发布最高版本比对）：R1 input 契约破坏＝required 扩大或既有字段类型变化（含收窄、enum 缩小）；R2 索引契约破坏＝searchable 集合变化；R3 节点身份破坏＝已发布节点 code 被删或同 code 节点 kind 改变；R4 拓扑语义破坏＝任一节点入边集合变化。
- **输出（可解释硬要求）**：`VersionVerdict{next_major, next_revision, breaking, triggered_rules:[{rule,detail}], content_hash, identical_to?, override?}` → 进 deploy API 响应、事件载荷、日志；AI 可先跑 verdict 预演（只判定不落库）。
- **时机**：deploy 事务内、写库前判定；内容哈希与某已发布版本相同 → **幂等去重**（不产生新版本、返回既有版本号、**不发事件**）；并发撞车由 (code,major,revision) 唯一索引兜底重试。

### 6.4 定义生命周期事件（outbox 投递，instance_id 为空）

| 事件 | 载荷 | 触发 |
|---|---|---|
| definition_deployed | code, major, revision, breaking, triggered_rules[], content_hash, published_by, is_new_code, override? | deploy 事务提交 |
| definition_retired | code, actor, reason | retire 提交 |
| definition_purged | code, actor | 物理删除提交（仅 CLI --force，且校验无实例引用该版本） |

### 6.5 感知更新的五种姿势与 CRUD 面

| 谁想知道 | 姿势 |
|---|---|
| 宿主 UI「有新版本」 | 订阅 definition_deployed |
| 「多少在跑实例钉旧版」 | 按定义版本过滤实例的查询 API |
| AI/CI「git 与库是否一致」 | `definition_drift(git目录)`：未部署/库多余/库落后 三张清单 |
| 部署审计 | definition_deployed 事件流 |
| 内核自身 | 不需要（6.1 三机制） |

CRUD：新增与更新＝同一操作 `deploy`——**纯数据写入（INSERT 一行），应用零重启零重打包，秒级生效**（下一条 start 即用新版）。三入口：CLI（`flowork deploy 文件`）/ HTTP（`POST /v2/definitions/deploy`，designer「发布」按钮即此）/ MCP 工具。git 文件是可选增强（想要 PR 审查的团队走 git→CI→deploy），非必经；纯设计器用户可无文件直接发布。定位＝`get_definition(code, major?, revision?)`（缺省 latest）＋ `list_definitions(code_prefix, status, latest_only)`；删除两级＝`retire`（软删默认）＋ `purge`（物理删，CLI 专用、--force、无实例引用校验）。同批部署多定义原子（全成或全不成）。

### 6.6 快照与审计

实例抄全文 snapshot（图纸 JSON 通常几 KB，10 万实例≈数百 MB，可接受；引用化优化不做）。`wf_process_definition` 补 `published_by/published_at/content_hash` 列。**审计四件套**（每件不可变，合起来回答「当时发生了什么、凭什么发生」）：definition_snapshot（当时按哪版图纸走）＋ actor_context 身份快照（他以什么身份操作，5.6）＋ wf_task_history（什么时间、什么动作、什么结论）＋ 走线记录（流程怎么走的）。「当时谁能审什么」＝实例 snapshot + 部署事件流 + 身份快照。资源（表单/权限）版本沿用同款哲学：发布不可变 + 定义引用资源版本（M8 落地）。

## 7. 等待与唤醒【v2 新增语义，当前最大缺口】

流程停下来等三样东西，语义各不同：

| 机制 | 等什么 | 语义 |
|---|---|---|
| 定时器（现状 timeout/timer job ✓，成文） | 等到某个时刻 | UTC 计时；到期发 `timer_fired` 再推进。实例暂停期间冻结，恢复后按工作日历顺延 |
| **消息**（✗ 新增） | 等一个外部事件带数据来 | 发布 `publish_message(name, correlation_key, payload, message_id, ttl_seconds)`。等待节点登记订阅 `(name, correlation_key)`——correlation_key 是 CEL 表达式（4.2），求值结果如报销单号用于匹配。**先到的消息进缓冲区**（wf_message_buffer，message_id 唯一防重，TTL 过期清理），订阅出现时按先来先服务关联；关联成功 → 载荷按节点 output 映射写入变量（与人工任务完成的写入路径同一条）→ 推进。订阅生命周期：节点离开（完成/取消/实例终止）即关，暂停期间保持。消息也可启动实例（start_on_message，message_id 唯一保证至多启动一个） |
| **信号**（✗ 新增） | 等一声广播（不带数据） | `signal(name)`：唤醒所有在等这个名字的订阅。与消息的分工：信号只唤醒不写数据，消息带数据且点对点 |

**实例时限（deadline）【v2.2 新增】**：图纸顶层 `deadline: {after_minutes, strategy}`，strategy ∈ mark（实例打超时标记＋发事件，流程继续跑）/ cancel（作废）/ terminate（终止）。发起时按工作日历折算 deadline_at 物化进实例表，到点由 timer job 触发 `instance_deadline_reached` 事件并执行策略；实例已完成则静默不触发。暂停期间冻结、恢复后顺延（与节点定时器同规则）。依据：Temporal 的 Workflow Execution Timeout 是原生实例级超时（限制实例总生命周期）；BPMN 系引擎无原生等价物（要拿子流程包住主流程再挂中断型定时边界事件）——flowork 取声明式字段，不逼用户画「定时炸弹并行分支」。

## 8. 错误模型：什么算「业务上的不通过」，什么算「系统坏了」

**二分原则**：业务上的失败（审批驳回、表单不合格）是**流程语义**，走图纸里画好的路；系统故障（接口连不上、表达式算错）不是流程语义，**停下来等人修**（故障单），绝不假装成驳回。

| 类别 | 例子 | 处理 |
|---|---|---|
| 业务失败 | 审批驳回；output 不符合 input 契约（拒绝提交） | 驳回走 reject_target；契约不合格当场拒收——都不产生故障单 |
| 技术故障 | action 抛异常、CEL 求值错、外部 worker 重试耗尽 | 先自动重试（5.2）；耗尽 → 死信 + 故障单（state=open）。人工处置：`resolve_incident(instance_id, action)`，action 现状 resume/cancel ✓，【v2】扩 retry（重派）/skip（跳过节点）/fail（终止实例）。根因没修好，故障单会再冒出来（允许复发，成文） |

## Part B：接口层（怎么跟内核打交道）

## 9. SPI 端口（内核的标准插座，内核只依赖协议）

现有 5 个端口 ✓（默认实现保证内核可裸跑可单测）：`Clock.now()`；`WorkCalendar.duration_minutes(start,end)`；`Notifier.notify(event,payload)`（【v2】默认实现改为 outbox 分发器）；`OrgDirectory`（【v2.1】瘦身为引擎必需四方法，见下）；`VariableStore.load/save`（【v2】save 增加 schema 校验职责）。

**找人分工（v2.1 定稿）**：取人（组→人）＝表达式词汇，全部宿主注册（5.5，内核零内置，contrib 三个便利函数基于本端口实现）；关系判断（人→组）与身份定格＝**引擎自己的行为**，走本端口——方向相反，自定义函数表达不了（「我的待办」要算张三属不属于 role:finance，是引擎在问，不是图纸在问）。

【v2】新增 3 个端口、重构 OrgDirectory、扩展现有 1 个端口（签名如下）：

```python
class OrgDirectory(Protocol):            # ◐ v2.1 瘦身定稿：引擎必需的四方法
    def is_member(self, user: str, group: str) -> bool: ...        # 人→组：候选命中判断（引擎查「张三属不属于这个组」）
    def subjects_in(self, group: str) -> list[str]: ...            # 组→人：候选列表展开（直接成员，不含子部门，5.5）
    def manager_of(self, user: str) -> str | None: ...             # 转上级：超时 escalate 的引擎自动行为
    def snapshot(self, subject: str) -> dict: ...                  # 身份快照：完成事务内定格操作者当时的岗位/角色/部门（5.6）

class ExpressionFunctions(Protocol):     # ✓ CEL 自定义函数来源（4.3，已接线；contrib.cel_vocabulary 三词汇）
    def functions(self) -> Mapping[str, Callable]: ...

class ResourceProvider(Protocol):        # ✗ 表单/权限等资源类型的注册与获取（M8/M9 接入口）
    def resource_types(self) -> Mapping[str, type]: ...
    def get(self, resource_type: str, resource_id: str) -> Any | None: ...

class EventSink(Protocol):               # ✗ 事件外发（默认=outbox 轮询；webhook 实现挂这）
    def emit(self, envelope: dict) -> None: ...
```

**目录的默认实现与强制程度（显式选择，不强制实现）**：默认给**静态目录**（配置文件写死映射，小团队、demo、CI 裸跑够用）；空配置时四方法返回 False/[]/None/{}。validate 对「图纸用了 role:/org: 组主体而目录为空配置」给**警告**（deploy 不拦，后果＝组待办无人能认领，宿主部署时已见过警告）；escalate 查无上级→故障单（5.1，不静默丢）。纯服务编排、agent 编排类宿主根本不用组主体，完全不需要实现目录。

铁律（CI 强制）：内核模块 import 任何插件包即红灯。

## 10. 领域事件

现有 14 个 ✓（process_started/completed/cancelled；node_entered/exited/cancelled/failed；task_created/completed/rejected/claimed/transferred；incident_raised；job_dead_lettered）。【v2】增补 14 个：process_paused / process_resumed / process_terminated / definition_deployed / definition_retired / definition_purged / task_delegated / task_resolved / task_escalated / incident_resolved / timer_fired / instance_deadline_reached【v2.2】 / message_received / message_correlated。幂等部署（内容哈希相同）不发 definition_deployed（降噪）。

- **信封**：CloudEvents 1.0.2——type=事件名，id=outbox 行 id（UUID，消费端按 id 去重），source=`flowork://{engine_id}`，data=事件字段。
- **交付保证**：事件与业务变更**同一个数据库事务**写入 outbox（transactional outbox，库形态独有优势）；分发为至少一次，消费端需幂等。
- **不发什么**：变量值变化不发事件（防刷屏）；传阅已读不发（查询可得）。

## 11. Python API（进程内调用，四组）

| 组 | 方法 | 现状 |
|---|---|---|
| 定义 | `deploy / validate / dry_run / semantic_diff / verdict(预演) / get_definition / list_definitions / retire / purge / definition_drift` | ✓（validate/dry_run/semantic_diff）；deploy ◐（版本判定+幂等去重）；retire/purge/drift/get/list ✗ |
| 实例 | `start_process(code, variables, initiator, idempotency_key?, reuse?)`；`pause / resume / cancel / terminate / get_instance` | start ◐（幂等【v2】）；pause/terminate ✗ |
| 任务 | `get_task / list_tasks / claim / unclaim / complete / reject / transfer / assist / complete_assist / add_countersigner / list_cc_tasks / mark_cc_read`；`anticipate(task_id, outcome) → {node_exit, instance_complete}`【v2.2 ✗】 | 除 anticipate 外 ✓ 全部 |
| 系统 | `signal / publish_message`【v2】；`activate_jobs / complete_job / fail_job`【v2】；`list_incidents / resolve_incident / move_token` | move_token/incident ✓，其余 ✗ |

启动幂等三策略：`reject`（撞 key 报错，默认）/ `return_existing`（返回已有实例）/ `allow_duplicate`（明示放弃幂等）。任务完成侧幂等已由历史表 idempotency_key 支撑 ✓。

**anticipate（只读预判）【v2.2 新增】**：给定任务与假想 outcome，返回 `{node_exit, instance_complete}`——预判「照此提交后节点是否出、实例是否完」。纯读：不落库、不改任何状态（读 token 会签计数＋出口规则即时计算）。用途：UI 的「你是最后一个审批人」提示、AI 预审。主流引擎无等价 API（Camunda/Flowable/jBPM/Zeebe/Temporal 均未提供，源自 对标产品 的产品化创新），是 flowork 的 AI 原生差异点。

## 12. REST API（web 包，C8 v2 风格）【v2 大部分新增】

- 搜索：`POST /v2/{definitions|instances|tasks|jobs|incidents|messages|events}/search`，请求体 `{filter, sort:[{field,order}], page:{after?/before?, limit}}`（游标分页）；响应带 `next_cursor`。
- tasks 过滤维度：state × assignee × candidate × flow_code × instance_id × priority × due_before（后两项依赖 wf_task 新列，第 14 章）。
- 动作：`POST /v2/tasks/{id}/{claim|unclaim|complete|reject|transfer|assist|complete_assist}`；`POST /v2/instances/{id}/{pause|resume|cancel|terminate|signal}`；`POST /v2/messages/publish`；`POST /v2/jobs/activate`（长轮询：request_timeout 参数）；`POST /v2/jobs/{id}/{complete|fail}`；`POST /v2/definitions/deploy`；`POST /v2/definitions/{code}/retire`；`GET /v2/definitions/{code}?major=&revision=`（purge 仅 CLI，不出 REST）。
- 只读预判【v2.2】：`POST /v2/tasks/{id}/anticipate`（body 携带假想 outcome，返回 `{node_exit, instance_complete}`，不落库不改状态）。
- 错误体统一 `{"error": {code, message, details}}`。

## 13. MCP 工具（M7 落地）

首批 17 个：validate_spec / dry_run / deploy / **definition_drift**（AI/CI 对账，6.5）/ list_definitions / start_instance / query_instance / signal_instance / publish_message / list_my_tasks / claim_task / complete_task / reject_task / **anticipate_task**（AI 预判提交后果，只读，v2.2）/ pause_instance / list_incidents / resolve_incident。入参出参直接复用第 11 章的 pydantic DTO（tasks/models.py 已为 MCP 预留 typed DTO ✓）。

## Part C：实施层（怎么开工）

## 14. 持久化变更清单

现有 11 表保留（见 3.4）。变更：

| 表 | 变更 |
|---|---|
| wf_process_definition | version:int → (major, revision)；加 breaking 标记列；补 published_by / published_at / content_hash（6.6） |
| wf_process_instance | state 扩 paused/terminated；加 idempotency_key（有值时与 definition_code 联合唯一）；加 deadline_at（实例时限物化时刻，7 章）【v2.2】 |
| wf_task | 加 priority / due_at |
| wf_task_history | 加 actor_context（身份快照 JSON 袋，上限 4KB，完成事务内写入，默认 {}，见 5.6）【v2.1】 |
| wf_job | 加 worker_id / lease_until / next_retry_at |
| wf_incident | resolution 枚举扩 retry/skip/fail |
| wf_message_buffer（新表） | name, correlation_key, message_id（唯一）, payload, expires_at, received_at |
| wf_message_subscription（新表） | instance_id, node_id, name, correlation_key, state(open/matched) |
| wf_variable_index（新表） | instance_id, name, value_text, value_num |

迁移走 Alembic 基线（与多副本欠账合并排期）。

**存储策略（与宿主库的关系）**：

- **默认同库**（PG 独立 schema；SQLite/MySQL 靠 wf_ 前缀隔离）。理由：事务性 outbox 要求事件与业务变更同事务（工单更新+流程推进+事件落库，要么全成要么全不成），跨库即失效；备份一致性；少运维一个库。
- **分开的三种例外**：多副本 sidecar（flowork 独立 PG，outbox 降级为至少一次）；宿主库不在支持矩阵；合规强制隔离。
- **支持矩阵**：SQLite（开发默认）/ PostgreSQL（生产一等）/ MySQL（社区尽力）。**不做 NoSQL 后端**（outbox 依赖关系型事务）。
- **日志边界**：领域事件/已办历史/定义版本**必须进库**（审计与状态同事务是根基）；**应用运行日志不进库**（走 logging/OTel 标准通道——库不是日志系统）。outbox 设保留策略（消费确认后可清理/归档，默认 90 天可配）。

## 15. 模块布局（目标）

```
flowork/
  definitions/          # + versioning.py（major/revision 判定）
  expr/cel.py           # ✓ CEL 求值（已替换 asteval；formula.py 已删除；条件单轨化后 groups 亦退役，§4.1）
  engine/
    message_service.py  # ✗ 第 7 章消息
    job_service.py      # ✗ 5.2 领取-汇报
    runtime_service.py  # ◐ pause/resume/terminate/幂等启动
    （其余四服务/ops/kernel/routing 保持）
  events/envelope.py    # ✗ CloudEvents 封装
  spi/ports.py          # 8 端口
  contrib/              # ✗ v2.1 便利包：org 词汇三函数（基于 OrgDirectory 端口），可选一行挂上
  mcp/                  # ✗ M7
  participants/ web/ cli/ jobs/ persistence/（保持）
```

## 16. 开工清单（每项有验收，按依赖排序）

| # | 任务 | 验收标准 |
|---|---|---|
| P0-1 | CEL PoC：cel-python 函数注册/类型检查/超时/性能 | bench 脚本 + 换绑决策记录 |
| P0-2 | SPI 定稿：OrgDirectory 四方法（is_member/subjects_in/manager_of/snapshot）＋静态目录默认实现＋contrib 便利包＋CI import-linter | 内核 import 插件时红灯演示；空配置目录＋图纸用组主体 → validate 警告测试 |
| P0-3 | CloudEvents 信封 + outbox 消费器 | 消费端按 id 幂等测试 |
| P0-4 | major/revision 判定（接 semantic_diff）＋取消 draft 状态迁移＋幂等去重＋VersionVerdict 报告 | 破坏性/兼容性表驱动测试；同内容重复部署不增版本不发事件；override 带 reason 进事件 |
| P0-5 | 启动幂等三策略 | 并发同 key 竞态测试 |
| P0-6 | input 契约三边界校验（发起/任务提交/节点输出） | 越界拒收测试 |
| P0-7 | all 汇聚放行判定（2.3 可达性语义，修复现状死锁）【v2.2】 | 包容分支未命中时汇聚放行测试；未完令牌在途时等待测试；循环迭代不因旧 settled 令牌提前放行测试；含驳回重入 all 汇聚子图测试；死循环守卫（max_node_loops）回归 |
| P1-1 | 消息服务（两新表+TTL 缓冲+关联） | 消息先到竞态测试 |
| P1-2 | Job 领取-汇报+租约 | 租约过期重领测试 |
| P1-3 | pause/resume/terminate+事件 | 暂停期 timer 冻结测试 |
| P1-4 | 错误二分落地（resolve action 扩枚举） | 三类错误路径测试 |
| P1-5 | wf_variable_index | 索引与变量同事务一致性测试 |
| P1-6 | 定义生命周期：retire/purge（--force+无实例引用校验）/definition_drift/按版本过滤实例查询 | purge 拒绝被引用版本测试；drift 三清单测试 |
| P1-7 | 身份快照：actor_context 列＋snapshot 完成事务内回调＋历史/查询带出【v2.1】 | 完成事务内写入（组织变更不影响已定格内容）测试；默认空实现裸跑不受影响测试；4KB 超限拒收测试 |
| P1-8 | 超时演进：deadline 列表＋实例级 deadline＋escalate 兜底（5.1/7 章）【v2.2】 | 多级预警按序触发测试；auto_approve 后剩余 deadline 不触发测试；旧 timeout 字段兼容读取与 semantic diff 等价测试；实例到点 mark/cancel/terminate 测试；暂停期 deadline 冻结测试；escalate 查无上级→故障单测试 |
| P1-9 | anticipate 只读 API（11/12/13 章）【v2.2】 | 会签中途预判正确（含比例出口）测试；调用后实例状态零变化测试 |

**DoD**：P0/P1 全过；测试不低于现基线（207）且新语义均有覆盖；`validate → deploy → start(幂等) → claim → complete → 审计查询` 在单 SQLite 文件上零配置跑通；AGENTS.md 与本文同步；CI（mypy/ruff/pytest/import-linter）全绿。

---

## 附录 A：对标依据

见 [kernel-v2-plan.md](./kernel-v2-plan.md)（Temporal / Zeebe-Camunda 8 / Flowable / LittleHorse / CloudEvents 官方文档，核实于 2026-10-07）。

v2.2 增补对标的直接来源（核实于 2026-10-07）：对标产品 基准版本 行为基准源码（out/基准材料，逐文件对拍）；Flowable `InclusiveGatewayActivityBehavior` 源码（静态可达性＋活跃执行扫描）；Zeebe 汇聚包容网关 issue camunda/camunda#10031（8.1 仅分叉 / 8.6 补齐汇聚，激活三条件）；WS-HumanTask 1.0（OASIS，jBPM 实现——多 deadline 挂 escalation）；Temporal Workflow Execution Timeout（原生实例级超时）；OR-Join 语义对比论文（Global vs. Local Semantics of the BPMN 2.0 OR-Join——jBPM OR-join 死锁/死循环双漏检）。

## 附录 B：评审记录

### 第一轮（2026-10-07，v0.9→v1.0，一致性/完备性）

修正 8 处：事件计数 14+11；4.7 任务状态机与存储不符（claimed/assisted 非独立态）；wf_job 现状已有三级重试；CloudEvents id 改为 outbox 行 id；P0/P1 与上游计划矛盾拆分；facade 补 add_countersigner/CC；start_on_message 幂等语义；REST 过滤字段补列。

### 第二轮（2026-10-07，v1.0，开工者视角）

修正 4 处：消息订阅生命周期、载荷映射规则、升级无上级兜底、文档定版。

### 第三轮（2026-10-07，用户反馈，v1.0→v2.0 整体重写）

用户指出四类问题，重写响应：
1. **要素未定义清/扩展性不明** → 新增第 2 章：节点/连线/推进策略三要素逐一给定义+字段表+「无网关」设计说明+BPMN 映射表+两层扩展机制+自定义节点完整示例（§2.6 的验证标准：加新要素=注册，不改内核）。
2. **数据结构缺失** → 新增第 3 章：图纸 JSON 顶层全字段表、七种节点字段表、九张运行时表逐列清单、完整图纸示例。
3. **人工/系统任务定义不清+黑话** → 新增第 0 章名词速查表（20 条大白话）；5.1/5.2 每节以「一句话定义」开头并写明产生/完成/卡住路径；全篇术语以第 0 章为准。
4. **结构混乱（CEL 混在各处）** → 表达式独立成第 4 章（三种写法各管一摊+使用位置唯一清单），其余章节只引用不重复定义。

重写时对照代码修正 v1.0 的三处失实：①变量声明现状是设计器元数据不约束运行时（3.2 显式区分「声明」与 v2 新增「契约」）；②超时策略现状枚举是 notify/auto_approve/auto_reject（v2 扩展而非替换）；③公式现状是 asteval 沙箱、结构化条件组保留不动（CEL 只替换 formula 一支，非全量替换）。

### 第四轮（2026-10-07，v2.0 重写后自评审，交付前审计）

审计方法与结论：
1. **结构完整性**：grep 全部章节标题——§0 名词速查 + Part A（1–8）+ Part B（9–13）+ Part C（14–16）+ 附录，无缺章；用户四点反馈逐一有对应章节（要素→§2、数据结构→§3、大白话与任务定义→§0/§5、表达式独立→§4）。
2. **交叉引用**：全部「见 x.y／第 n 章」引用与实际章节对得上（0/2.2/2.6/3.2/3.3/3.4/4.1/4.3/5.1/5.2/7/9/11/14）。
3. **残留检查**：v1.0 三处失实（asteval 全量替换、超时策略枚举、变量声明约束力）无残留；「claimed」仅存在于「不设独立 claimed 态」的说明性文字。
4. **字段对拍**：要素字段表逐项来源于 flow.py / node.py / builtin.py / orm.py / behavior.py 现场读取，非记忆。
5. 修正 1 处本稿小出入：§9 标题「新增 3 个端口」与代码块列出 4 个类的出入，改为「新增 3 个、扩展现有 2 个」。

### 第五轮（2026-10-07，多轮增补后的整体一致性评审）

背景：v2.0 定稿后经历七轮增补（§5.5 参与人类型系统、附录 C 类型清单、§3.5 存储层级、§14 存储策略、§6 版本定稿重写、§10/§11/C.3/P0-4/P1-6 联动），重点查跨章节不一致。发现并修正 6 处：

1. **§3.1 status 字段与 §6.2 矛盾**（最重）：§3.1 仍写 draft/published，与「取消 draft」定稿冲突。改为 published/retired 并标注迁移随 P0-4。
2. **§14 定义表变更行缺列**：§6.6 定的 published_by/published_at/content_hash 未落进持久化变更清单。已补。
3. **org_of 函数三处不一致**：仅在附录 C.4 出现，§4.3 内置白名单与 §9 OrgDirectory 协议均缺。两处补齐。
4. **§2.5 service_task 字段表缺外部 worker 形态**：§5.2 说「节点声明 job_type」，字段表却只有 action/params。补 mode: handler|worker。
5. **§12 REST 缺 retire 端点与单查**：补 `POST /v2/definitions/{code}/retire` 与 `GET /v2/definitions/{code}`；确认 purge 仅 CLI 不出 REST。
6. **§13 MCP 清单缺 definition_drift**：§6.5 明确 AI/CI 靠 drift 对账，而 MCP 是 AI 主入口。加入，首批 15→16。

其余核对通过：事件计数 14+13、C.3 枚举与各章一致、§3.3 示例 JSON 与新状态机一致、交叉引用有效、术语表无 draft 残留（§3.1 修复后全篇仅 §6.2/P0-4 的迁移说明性文字提及 draft）。

### 第六轮（2026-10-07，v2.0→v2.1，找人函数定稿）

用户发起两轮讨论后定稿四项，同步修复前三轮遗留三处硬伤：

1. **参与者函数零内置**：内核函数注册表出厂为空，取人词汇全部宿主经 ExpressionFunctions 注册，内核只保类型系统与校验（§5.5）。理由与节点注册表「官方插件不吃小灶」铁律同源。四个原内置函数中三个挪 `flowork.contrib`（§15 新增目录）；部门类词汇（org_of/dept_leader）数据源无端口支撑，归宿主。
2. **OrgDirectory 瘦身为引擎必需四方法**（is_member/subjects_in/manager_of/snapshot）：取人（组→人）归表达式词汇，关系判断（人→组）与定格归引擎——方向相反，后者无法用自定义函数表达。默认实现＝静态目录（空配置四方法返回 False/[]/None/{}）；validate 对「图纸用组主体而目录空配置」警告；不强制宿主实现。
3. **身份快照 actor_context**（§5.6 新增、§3.4/§14/§16-P1-7 联动）：任务完成事务内调 snapshot(subject) 把操作者当时的岗位/角色/部门原样定格进 wf_task_history（JSON 袋 4KB 上限，内核不解析）。动机：审计要回答「他当时是谁」，不能只记 id。§6.6 落「审计四件套」公式。可选加强（解析依据快照「为什么是他」）记下不做。
4. **类型词汇统一＋硬伤修复**：返回类型枚举 subject/subjects/groups/participants/bool → **one_user/users/groups/mixed/bool**（参数与返回共用，消 org 缺口）；超时示例 `manager_of(assignee)` → `manager_of(task.assignee)`（C.4 task.* 可用范围扩「超时策略求值」）；候选槽接受 mixed 自动拆槽（消「混合 ParticipantSet 用不到候选」）；补 None/空传播三规则、组展开「直接成员不含子部门」、Subject=string 检查强度诚实声明；§3.3/§4.2 示例裸 originator 统一为 initiator（与 C.4 命名空间一致）。

### 第七轮（2026-10-07，v2.1→v2.2，对标产品 行为基准对标＋开源引擎对标）

背景：以 对标产品 基准版本 行为基准代码逐文件对拍内核设计，再对标 jBPM / Activiti 血统（Flowable、Camunda 7）/ Zeebe 8 / Temporal / WS-HumanTask，落七项修改：

1. **§2.3 all 汇聚放行判定定稿**（本轮最重要，正确性修复）：现状「等全部直接入边 settled」在上游条件分支未命中时永久死锁。改为可达性语义（除已停靠令牌外无未完令牌可达即放行；只看未完令牌，旧 settled 令牌不算数），与 Flowable（静态可达性＋活跃执行扫描）、Zeebe 8.6（#10031）同族；对标产品「来源无令牌＝满足」为同步建令牌模型下的简化特例，因 v2 消息节点引入异步到达不可沿用，故一步到位。§2.4 映射表两行联动（并行/包容汇聚都指回 2.3）。
2. **§5.1 超时改 deadline 列表形态**（对齐 WS-HumanTask「多 deadline 各挂 escalation」）：`deadlines: [{after_minutes, action}]` 五动作（notify/reassign/escalate/auto_approve/auto_reject）；多 notify＝多级预警（对标产品 timeoutWarning1..5 的标准形态替代）；escalate＝OrgDirectory.manager_of（宿主可内部沿树向上）→查无上级转故障单；现状 TimeoutPolicy 为退化形态，validate 兼容、semantic diff 等价。§2.5/§4.2/§3.3/C.3/C.5 联动。
3. **实例级 deadline 新增**（§3.1 字段/§3.3 示例/§3.4+§14 deadline_at/§7 语义/§10 事件）：Temporal 原生 Workflow Execution Timeout 同款；BPMN 系需子流程边界定时事件建模，flowork 取声明式字段。新事件 instance_deadline_reached（增补 13→14）。
4. **§5.1 驳回落点豁免 auto_pass 成文**：现状靠解析器 manual_participants 短路隐式实现（行为等价于 对标产品 doOnRejectedActivity），升格为语义保证，防「发起人驳回→重提交→又被跳过」打转及未来重构丢失。
5. **anticipate 只读 API 新增**（§11/§12/§13，MCP 首批 16→17 加 anticipate_task）：预判提交后果，主流引擎无等价物，定位 AI 原生差异点。
6. **§1 非目标 +2**：提交时自选下一节点（BPMN 系引擎均无此路由模式，输出变量＋条件边建模——推翻讨论初稿「加 allow_target_choice」的倾向，§2.4 落建模指引）；催办归宿主/通知插件。
7. **开工项 +3**：P0-7（汇聚放行）、P1-8（deadline 演进）、P1-9（anticipate）。

讨论过但**不采纳**：审批托付按流程限定作用域（delegation_of 加流程参）——与第六轮「OrgDirectory 瘦身为引擎必需四方法」定稿冲突，托付替换可在宿主目录/表达式层组合实现，记下不做。对拍确认不借的 对标产品 设计：驳回复活改写历史行（flowork 新任务行＋cause_token_id 更优）、Redis 分布式锁、引擎内线程池异步推进、connection 空节点、实例 bak 归档体系、单 int 版本号。

## 附录 C：类型清单（须提前定义的全集）

原则：**跨边界的类型提前定，业务语义类型不进内核**（Money/TicketPriority 之类由插件用 JSON Schema format 扩展表达）。五条边界：图纸↔引擎、表达式↔变量、函数↔校验器、API/事件↔外部、数据库↔代码。

### C.1 值类型与 JSON↔CEL 映射【v2 定义】

变量域 = JSON Schema 原生类型：string / number / boolean / array / object / null ＋ format 白名单（date-time / date / uuid / uri）。

| JSON | CEL | 落盘规则 |
|---|---|---|
| string | string | 原样 |
| string + format:date-time | timestamp | 落盘回 ISO 8601 UTC 字符串 |
| number | int / double | 统一 number，整数值比较自动 unify |
| boolean | bool | 原样 |
| array | list | 原样 |
| object | map | 原样 |
| null | null | 原样 |
| —（表达式内可用） | duration | **变量域永不出现**；落盘转秒数（number） |

### C.2 主体与参与人（见 §5.5）

Subject：`user:{id} | agent:{id} | role:{name} | org:{code}`；ParticipantSet 两槽；函数类型词汇（参数与返回共用，另含 CEL 原生标量）：one_user/users/groups/mixed/bool。

### C.3 状态枚举（全部封闭，validate 拒绝未知值）

实例 running/paused/completed/cancelled/terminated；定义 published/retired（**v2 取消 draft**）；任务活表 ready/completed/cancelled；已办 outcome agree/reject/transfer/auto_pass/withdrawn；令牌 unfinished/finished；Job scheduled/…/dead-letter（值开工时对齐现状）；故障单 open/resolved 及处置 resume/cancel【v2 扩 retry/skip/fail】；推进 any/all；路由 first_match/all_match；会签 parallel/sequential；deadline 动作 notify/reassign/escalate/auto_approve/auto_reject【v2.2 列表形态】；实例时限策略 mark/cancel/terminate【v2.2】；无人策略 raise/to_admins；幂等复用 reject/return_existing/allow_duplicate。

### C.4 表达式命名空间与内置函数签名【v2 定义】

| 命名空间 | 类型来源 |
|---|---|
| input.* | 按 input JSON Schema |
| vars.* | 运行时（静态检查按声明，未声明按 any 降级为警告） |
| task.* | 固定形状：task.id:string、task.assignee:subject、task.outcome:string——超时策略求值可用（✓ 已实现，fire 时注入；活任务 outcome=null）；节点离开时的条件求值/输出映射注入为后续项（静态检查：非改派位引用 task.* 即 ERROR task_unavailable） |
| initiator | subject |

contrib 签名（便利包，可选，纯基于 OrgDirectory 端口实现）：`manager_of(one_user)→one_user`（吃 user:/agent: 返回其上级；吃 org: 返回部门负责人；吃 role: 设计期报错）、`users_with_role(string)→users`、`has_role(one_user, string)→bool`。部门类词汇（org_of 等）数据源无端口支撑，由宿主注册。自定义函数注册必须带全签名（参数＋返回，词汇表见 5.5）；内核注册表出厂为空。

### C.5 词法与单位规则【v2 定义】

- code：`^[a-z][a-z0-9-]{0,99}$`；Subject：`^(user|role|agent|org):[A-Za-z0-9_@.\-+]+$`；id 一律 UUID 字符串。
- searchable 变量仅限 string/number/boolean（object/array 标 searchable → validate 报错）。
- correlation_key 表达式必须求值为 string（否则设计期报错）。
- 会签出口 BNF：`exit := "all" | "any" | ">=" 整数 | ">=" 整数 "%"`（any≡">=1"；比例基数＝实际参与人数）。
- 单位：时长一律分钟（after_minutes、WorkCalendar），落盘秒数；金额无单位纯数值；时区全 UTC。
- deadlines：after_minutes 必须为正整数；同一节点上限 8 条；顶层实例时限 deadline 为单对象（非列表）。
- priority 取值 0–100（小→大＝低→高），默认 50。

### C.6 错误与 DTO

异常四族：定义类（DefinitionError：UnknownNodeKind / InvalidDefinitionData / 校验失败）／运行类（handler、求值异常）／冲突类（LockConflictError / DuplicateStartError / TaskAlreadyClaimedError）／插件类（插件自定义）。每族映射 REST `error.code`。对外 DTO：TaskInfo / TaskHistoryInfo / InstanceInfo / EventInfo（✓ 已有）＋【v2】JobInfo / IncidentInfo / DefinitionInfo / 搜索请求响应（filter+sort+cursor）。
