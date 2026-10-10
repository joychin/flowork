# flowork 流程内核 v2 —— 架构与功能说明书

| | |
|---|---|
| 文档版本 | v2.3（v2.2 基础上按 对标产品 对标两轮定稿＋两批实施：动作票数语义/复活最近一轮/前视跳过列非目标（第八轮）；撤回下游闸门/动作表与 available_actions/WorkCalendar 端口删除（第九批，见附录 B）；§4.1/§4.2 的 v2.3 标记为 2026-10-08 条件单轨化与位点统一批，统一升版） |
| 日期 | 2026-10-09 |
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
| 主体（Subject） | 「一个人/一个角色/一个 AI 代理」的统一写法：人与 AI 代理写裸 id（`u_zhang`、`bot1`）、组引用带前缀（`role:manager`、`org:rd_be`）【v2.3 第十六轮：`agent:` 写法删除】 |
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

**非目标**：表单/权限/通知等插件规格（另文）；多副本部署细则；webhook 出站与链路追踪（P2 占位）；BPMN XML 导入导出；提交时自选下一节点（BPMN 系引擎均无此路由模式，人的选择用输出变量＋条件边表达，见 2.4 注）；催办/提醒类动作（UrgeTask 形态，归宿主或通知插件）；**前视式自动跳过参与者【v2.3】**（「他后面还会审、这关免审」的 participateLater 形态——主流引擎零实现，且需用未来变量做决策、失效模式是审批链空洞且审计不可见；「同一人别重复审批」由 auto_pass_participated/previous 向后看覆盖，前视信息只做 anticipate 只读提示，依据见 对标明细.md A7）。

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
| condition | str(CEL)/null | — | null | 路由条件：CEL 公式字符串（写法见第 4 章；groups 结构化条件组已退役） |
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
| 事件网关/消息等待 | 等待节点 wait（见 7.2，2026-10-09 已实施）：等消息/等时长/竞速三模式合一；多事件竞速=并行分支+any 汇聚图组合，等齐=+all 汇聚 |

**关于「提交时自选下一节点」**：BPMN 系引擎（jBPM/Activiti/Flowable/Zeebe）均无此路由模式，flowork 也不做内核语义——人的选择用**输出变量＋条件边**表达（对标产品 的 alternativeFlag 属标准外私货，主流量表不跟）。设计器可提供「选择题」交互把选项落成枚举变量，引擎语义零新增。

### 2.5 内置八种节点类型（kind）字段表 ✓

| kind | 一句话 | 专有字段（公共字段见 2.1） |
|---|---|---|
| start | 起点，一个图一个 | 无 |
| end | 终点，令牌到这里消失；全消失则实例完成 | 无 |
| user_task | 人工任务：等人操作（定义详见第 5.1 章） | assignee/assignees/candidate_users/candidate_groups（取人，支持表达式）；multi_instance（会签，见下）；reject_target（驳回到哪个节点）；auto_pass_originator/previous/participated（命中者自动通过）；no_participant_policy（找不到人：raise 报错/to_admins 转管理员）；deadlines（超时动作列表，见 5.1；【v2.2】现状单策略 timeout 为其退化形态，validate 兼容读取） |
| service_task | 系统任务：程序自动执行（详见 5.2） | action（在 HandlerRegistry 注册的动作名，引擎内形态）；params（参数字典）；【v2】mode: handler\|worker（worker＝外部程序领取执行，此时 job_type 声明任务类型，见 5.2） |
| call_activity | 子流程：调起另一个流程定义当一个节点用 | process_code；binding【v2.3 第十一批】（latest 缺省=运行期解析最新版，目标未发布 deploy 仅 WARNING；deployment=部署时必须已发布，ERROR——Zeebe bindingType 同义）；**multi_instance【v2.3 第十三批 A9】**（subjects 取人表达式＝每人一子实例，parent_index 记迭代序；input_mappings 按子求值注入 `subject`；completion 公式注入 child_total/completed/failed 只做提前收工；output collect＝index 对齐聚合 null 占位，空集合＝立即完成）｜is_synchronous（同步等它跑完/异步不管）；input_mappings/output_mappings（变量进出映射） |
| cc_task | 传阅：给人看一眼，不卡流程 | recipients（接收人列表，支持表达式）；advance_policy 恒 any |
| branch | 条件分支锚点 | route_mode：first_match（命中一条即走）/all_match（命中的都走） |
| wait | 等待节点：停下来等一条消息/一段时间/两者竞速，先到者决定走向（见 7.2） | message（{name, correlation_key?}，进入时求值快照）；output_mappings（载荷→变量映射，缺省全量合并）；timeout（{after_minutes}，纯延时即等待时长本身）。结果词汇 wait.* 仅出边条件 |

**会签（MultiInstance，user_task 专属字段）**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| mode | parallel/sequential | parallel | 并签（全员同时待办）/串签（一个批完下一个） |
| approve_exit | str/null | null | 通过出口：CEL 布尔公式（task.* 计数快照，见 4.2/C.4；缺省＝该侧全票） |
| disapprove_exit | str/null | null | 驳回出口：CEL 布尔公式（同上；缺省＝该侧全票） |

出口判定是 CEL 布尔公式（见 4.1/4.2），task.* 注入三计数（基数显式写在公式里）。

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
| start_on_message | list[{name, correlation_key?, input_mappings?, start_user?, business_key?}] | [] | 【v2 新增】消息启动声明：发布该名消息即发起实例（见 7.1）。启动订阅＝定义派生——published 即生效、retire 即失效，不落订阅表 |
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
        {"after_minutes": 1440, "action": "escalate", "escalate_to": "manager_of(task.assignee)"}
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
| wf_process_instance 流程实例 | id；definition_code；definition_version【v2→major+revision】；**definition_snapshot**（发起时图纸全文抄本，不可变——快照原则的实现）；business_key（业务单号，仅查询用）；state（running；【v2】扩 paused/terminated）；start_user；variables（JSON）；parent_id/parent_token_id（作为子流程时的父指针）；deadline_at【v2.2】（实例时限到期时刻，图纸顶层 deadline 按自然分钟折算后物化——WorkCalendar 已删，见 §9）；simulated【v2.3 第十二批 A10】（模拟实例标记：试跑不建任务/传阅/Job、事件不投 outbox、查询面默认隔离）；version（乐观锁）；started_at/finished_at/updated_at | 一次流程运转的全部持久状态 |
| wf_token 令牌 | id；instance_id；token_no（实例内自增）；activity_code（当前在哪个节点）；source_activity_code（从哪来）；state（unfinished/finished）；item_count/approve_count/disapprove_count（会签 应办/同意/驳回计数）；approval_exit（会签出口判定结果）；reject_flag（驳回产生的令牌）；manual_participants（人工调整的参与人）；created_at/finished_at | 「走到哪了」的记号；会签计数在此不在任务上 |
| wf_task 人工任务（活表） | id；instance_id；token_id；activity_code；name；assignee；task_type（approval/assist）；state（ready/completed/cancelled）；context_payload（审批卡片数据，AI 预审共用）；root_task_id（加签/委派链，指回原任务）；【v2】priority/due_at；created_at | 还没办完的人工任务 |
| wf_task_history 已办（只插入不改） | 同上 + task_id；outcome（agree/reject/transfer/auto_pass/withdrawn/assist/assist_cancel【v2.3：协办交还与随主任务收口】/cancelled/simulated 预留）；comment；acted_by；**idempotency_key**（完成幂等）；**actor_context**（身份快照 JSON 袋，完成事务内定格，见 5.6）【v2.1】；cause_token_id（驳回复活的关联）；cost_seconds（耗时）；created_at | 审计的唯一真相，append-only |
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

### 4.1 一种写法：CEL【v2.3 单轨化 + 位点统一 + 会签出口统一，2026-10-08】

| 写法 | 用在哪 | 现状→v2 |
|---|---|---|
| **CEL 公式** | 全部表达式位：连线条件、取人、传阅、超时改派、会签出口、子流程映射（见 4.2）；关联键随等待节点 | ✓ 全位点已接线（含会签出口——原专用语法 `">=50%"/"all"/">=1"` 退役，整数乘法精确等价：`>=N%` ≡ `task.approve_count * 100 >= task.item_count * N`；缺省不配＝该侧全票） |

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
- **会签出口统一**（同日补刀）：原「CEL 表达不了百分比语义」的豁免理由不成立
  （`ceil(item×N/100) ≤ done ⟺ done×100 ≥ item×N`，整数乘法精确等价）——专用出口语法
  退役，task.* 计数快照注入后 approve_exit/disapprove_exit 即 CEL 布尔公式，且解锁
  复合条件（≥2 同意且 0 反对）与业务变量法定人数；缺省不配＝该侧全票（语义不变）。

### 4.2 CEL 的使用位置清单（全篇唯一清单）

| 位置 | 字段 | 示例 | 状态 |
|---|---|---|---|
| 连线条件 | flows[].condition（CEL 字符串） | `input.amount > 300 && input.urgency == "high"` | ✓ |
| 取人 | user_task/cc_task 的 assignee/assignees/recipients（每项一表达式） | `'u_chen'`、`manager_of(initiator)`、`users_with_role("finance")`（函数来自 contrib 或宿主注册，见 5.5；null/空=查无传播） | ✓ |
| 超时改派 | deadlines[].action=reassign 时的 reassign_to | `manager_of(task.assignee)`（fire 时求值，task.* 见 C.4；结果须单个非空人） | ✓ |
| 会签出口 | multi_instance 的 approve_exit/disapprove_exit | `task.approve_count * 100 >= task.item_count * 50`、`task.approve_count >= 2 && task.disapprove_count == 0`（task.* 计数快照，见 C.4；缺省＝该侧全票） | ✓ |
| 消息关联键 | 等待节点的 correlation_key | `"input.order_no"`（求值结果用于匹配） | 随等待节点 |
| 消息启动位 | start_on_message[] 的 correlation_key / input_mappings 右侧 / start_user / business_key | `payload.order_no`（求值于**消息上下文** payload.*+now，见 7.1；correlation/start_user/business_key 须 string） | ✓（7.1） |
| 子流程映射 | input_mappings/output_mappings 的右侧 | `input.amount * 1.0`（输入在父上下文、输出在子上下文求值；裸标识符缺键→None） | ✓ |

### 4.3 CEL 求值语义【v2 改造】

- **变量命名空间**：`input.*`（流程契约输入）、`vars.*`（运行中变量）、`task.*`（任务快照，超时改派/会签出口位注入，见 C.4）、`initiator`（发起人 Subject，全位点注入）。
- **判断面事实目录**（expression-dataface F0–F3，2026-10-08 落地）：`now`（求值时刻，统一时钟 naive UTC，一次装配钉一时刻——组内多条公式同刻；全位点）、`instance`（实例全局事实 {id, started_at, business_key}，全位点注入；2026-10-09 内核收口批落地，模拟面试算面注入占位形状）、`prev_task`（上一站已完成任务，条件+取人位；查无不注入，静态预检温和跳过）、`node("code")`（引擎注入函数，点名查节点事实：runs/skipped/最近轮人物与票数；查无＝固定形状）、`wait`（等待结果 {result=matched|elapsed, message_name, message_id}，仅等待节点出边条件位，2026-10-09 §7.2 批）——与变量读写面（§10 variables_updated、§11 变量三方法）互补。词汇目录权威在 `expr/catalog.py`（保留词与位点白名单表驱动），validator 规则名 `*.{name}_unavailable`。路由决策审计落 `wf_route_decision`（§14），查询面 `history.route_decisions`。
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
- 会签节点：按 approve_exit/disapprove_exit 判定（CEL 公式，4.2）——比如 `task.approve_count * 100 >= task.item_count * 50` 且 3 人中 2 人同意即过；驳回出口同理；缺省不配＝该侧全票。
- 驳回：outcome=reject，令牌按 reject_target 回退（未配则回上一人工节点）；被驳回的任务复活=生成**新**任务行，旧行进历史（cause_token_id 关联）✓。**复活圈定「最近一轮」【v2.3 定稿，2026-10-09 拍板】**：复活名单＝目标节点**最近一个已完结令牌**名下的办理人（按 token_no 取最大；无完结令牌才回退到该节点全部非取消历史）——多轮驳回时名单不随轮次膨胀。与主流引擎的**刻意差异**在此声明：BPMN 系引擎（Camunda/Flowable/jBPM）重入活动时按定义**重新解析**办理人（人可能换、离职者自动剔除）；flowork 复活**原办理人**——驳回就是退回给原审批人重新表态，这是国内审批语义，不是缺失。兜底：原办理人名单为空（如首节点发起人）时走正常表达式解析。**驳回落点豁免自动通过【v2.2 成文】**：驳回产生的令牌、经 manual_participants 定格的参与者**不应用** auto_pass_* 策略——否则「发起人驳回→重新提交→又被 auto_pass 跳过」原地打转；现状由解析器 manual 短路隐式实现（participants/resolver.py），此处升格为语义保证，防未来重构丢掉（对标产品 doOnRejectedActivity 同规则）。

**任务动作全集（每个都是 API，详见第 11 章）**：claim（从候选里认领）/ unclaim / complete / reject / transfer（转办，保持待办换人）/ assist（委派：生成 task_type=assist 的子任务， root_task_id 指回原任务，原任务保留在负责人名下；**主任务终态（complete/reject）同事务收口其协办待办，outcome=assist_cancel【v2.3 第一批】**）/ complete_assist（受托人交还）/ add_countersigner（加签）/ consult（咨询【v2.3 第三批 A8】：生成 task_type=consult 的意见任务——「先收集意见、原办理人最后拍板」的两轮形态；意见不占票；咨询期间主任务软挂起，全部交回自动解禁并发 task_consult_finished 事件；对应 AI 场景「AI 给建议、人拍板」。**行业修正**：不抄 C7 delegation 的 assignee 轮转——派生独立工作项更现代，且 C7 的门禁不是硬门禁）/ complete_consult（交回意见：outcome=agree/reject 均为意见，不进会签计数）/ withdraw（撤回本人的已办，见下）。

**动作表与 available_actions【v2.3 第二批 A1】**：动作可用性由**引擎的动作表**声明——按 `(state × task_type × 认领态)` 三维判定（tasks/actions.py，形态对齐 jBPM 的 操作×状态 声明表；**不是**节点级配置——BPMN 无此位置、主流引擎不做节点级动作白名单）。`TaskInfo.available_actions` 由服务端算完下发（已认领的审批任务＝complete/reject/transfer/assist/add_countersigner/consult/unclaim；候选任务＝claim；协办任务＝complete_assist；咨询任务＝complete_consult【v2.3 第三批】）——**主流引擎均不下发此面**（C7/C8/Flowable 的任务响应逐字段核实无 permissions 字段），AI/前端拿到任务即知能做什么，属三面同构的差异点。分组借 Camunda 8 动词切分：work（complete/reject＝出结果）与 dispatch（transfer/assist/add_countersigner/claim/unclaim＝转移办理权）；**谁能做属宿主权限（M-A/插件），表只回答"这个任务当前允许哪些动作"**。

**撤回（withdraw）【v2.3 第二批 A2 定稿，拍板④】**：撤回本人的已办（agree）任务＝**取消当前全部未完令牌 + 在原节点新建令牌与待办**（"取消当前+新建上游"，不改写历史——对齐全行业"完成不可变"铁律；Camunda 8 用独立权限 MODIFY_PROCESS_INSTANCE 管实例修改，flowork 将其归类为实例修改类动作，开放面授权归宿主）。**下游闸门**：本节点正向可达的下游（图遍历，不评条件）存在**人工进展**——非取消已办、或工作节点（user_task/call_activity）的已完结令牌——则**拒绝撤回并提示改用驳回**（错误携带进展明细：哪个节点、谁、什么结果）；穿透节点（branch/cc_task/start/end）的即时完结不拦（对齐 对标产品 穿透 CONNECTION/CIRCULATE 口径）。闸门生效后"取消全部未完令牌"与"只取消下游"等价（下游全未办时并行兄弟亦无已办）——不需要精细取消算法。撤回策略扩展（如"下游未读才可撤"的 UNREAD 档）后置：flowork 任务面暂无已读态。

**任务状态（对齐现状存储，不新增状态列）**：活表 state ∈ `ready | completed | cancelled`；「已认领」＝ ready 且 assignee 已固定（不设独立 claimed 态）；终态语义在历史的 outcome 里（agree/reject/transfer/auto_pass/withdrawn/assist/assist_cancel/cancelled；✓ 现状值 + v2.3 增 assist_cancel）。已被他人认领再认领 → `TaskAlreadyClaimedError`。

**动作的票数语义【v2.3 定稿，2026-10-09 拍板】**（会签计数 item_count/approve_count/disapprove_count 只被下列动作改变）：

- **transfer（转办）＝票随人走**：原任务归档为 transfer（不计票），新任务完成后正常计一票——即「这一票由被转办人代为行使」，审计上谁代行有记录。转办不增加也不减少应办总数。
- **add_countersigner（加签）＝加签人各占一票**：item_count 随名单同步增加，每个加签人完成即计票——加签是「增加表决权」，不是「只增加把关人」。若业务想要「只要意见、不改票权」的形态，用 consult（咨询，已实装【v2.3 第三批】：意见任务 + 主任务软挂起）或 assist（协办，不挂起主任务），两条路天然分流，不引入按加签组归并票数的机制（对标产品 rootTaskId 组算一票的形态不采纳——行业对拍：主流引擎无票数概念，无从对齐，且归并会让 item_count 与任务行数失去对应）。
- assist/complete_assist 不计票（委派是「看一眼/搭把手」，不改变节点完成条件）✓ 现状。

**超时【v2.2 定稿：deadline 列表形态，对齐 WS-HumanTask】**：任务可挂一组有序动作 `deadlines: [{after_minutes, action}]`（自进入节点起算，after_minutes 必须为正整数，同一节点上限 8 条；**自然分钟直算到期时刻**（`now + after_minutes`；【v2.3 第二批定稿】after_minutes 恒为自然分钟——历史耗时全行业一律墙钟、零引擎按人算日历，双日历口径不做，WorkCalendar 死端口已删除（见 §9）；将来若需工作日口径，按 Flowable 形态重立：节点级具名日历+引擎级注册表+发布期校验名字，见 对标明细.md A5）。动作枚举五种：

| action | 行为 |
|---|---|
| notify | 提醒，不处理（多条 notify deadline＝多级预警：T-3 天、T-1 天…各提醒一次） |
| reassign | 按 4.2 CEL 表达式改派人（超时求值可用 task.*） |
| escalate | **目标由图纸给**（`escalate_to` CEL 表达式，必填）：求值出人 → 升级改派并记 `task_escalated`；求值出空 → 故障单，不静默丢。引擎不含「上级」概念（spec §5.5 分工铁律）——科层组织图纸写法 `escalate_to='manager_of(task.assignee)'`【v2.3 第十四轮】 |
| auto_approve | 自动通过 |
| auto_reject | 自动驳回 |

- `auto_approve`/`auto_reject` 会终结节点等待，之后的 deadline 不再触发；`reassign`/`escalate` 改写等待人后，剩余 deadline 继续对新任务计时。
- wf_task.due_at 物化首条 deadline 的到期时刻（due_before 过滤与排序用，§12）。
- **现状 `TimeoutPolicy{due_minutes, strategy}` 是单条 deadline 的退化形态**（strategy=notify → `[{after_minutes, notify}]` 一一映射），validate 兼容读取旧字段、semantic diff 视两种形态等价。
- 形态依据：WS-HumanTask 1.0（OASIS，jBPM 完整实现）即「多 deadline 各挂 escalation（notification / reassignment）」；对标产品 的 timeoutWarning1..5 是其劣化实现（预警水位写死五个字段）。Camunda 7/Flowable 无内建人工任务升级（要边界定时器＋监听器自搭），flowork 直接取标准形态。
- 实例级时限是**另一个字段**（图纸顶层 `deadline`，第 7 章），不混在任务 deadlines 里。

**AI 的位置**：assignee 可以是任何主体（人/AI/外部系统），一律裸 id 字符串——引擎不区分、不记录主体类型（是不是 AI 归宿主身份系统）。AI 领待办/办理/驳回与人同一条路；引擎与目录不提供任何 AI 专属设施（身份表/凭证/状态机/问责链均不设）【v2.3 第十七轮：Agent 域整体移除】。谁有权限点，属权限插件（M9），内核只记事实。

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

**两种字段两套写法【v2.3 第十四轮口径修正】**：
- **人字段写裸 id**（规范写法）：`assignee` / `candidate_users` / `recipients` / `start_user` 等指向具体人的位置写目录里的 id 原样（`u_zhang`、`bot1`；AI 代理同写法）【v2.3 第十六轮：`agent:` 写法已零兼容删除】。
- **组字段带前缀**：`candidate_groups` 写 `role:{name}` / `org:{code}`——前缀是**组引用的类型标签**，引擎与目录实现都按 `split_group` 解析（`org:` 即部门树，认领时按目录判断归属）。

**`user:` 前缀的地位（诚实口径）**：前缀**在部分路径上互认**、并非全局归一——
- 互认（已实现）：候选判定（`tasks/candidates.py::person_key` 剥前缀比对，认领守卫与候选查询维度共用）、目录 `manager_of`（两种物化都剥）；
- **不互认**（原样字符串比较）：委托替换、`auto_pass_originator`、`handled_assignees`、认领后的 `assignee` 比对。

因此混写（同一人在不同字段一半带前缀一半不带）在互认路径上看着正常、在不互认路径上静默错过（如发起人自动通过失效）。**推荐一律裸 id**；要全库归一需在引擎各比较点统一走 `person_key`，属独立批次。

（此前 §5.4 曾要求人字段也带 `user:` 前缀，但引擎多数路径从不解析它——两个目录实现当时一个认一个不认，是一句引擎不兑现的承诺。前缀的实际价值在组引用：它是类型标签。人是不是 AI 由宿主目录决定，不走字符串装饰，引擎也不记录该事实【第十七轮：acted_as_agent 标志已随 Agent 域移除】。）

内核**不存**用户表、不校验人是否存在（对齐 C7 IdentityService 惯例）；表达式里的取人由宿主注册的函数完成（5.5），引擎需要的「人↔组」关系判断走 OrgDirectory 端口（第 9 章）。

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
ParticipantSet = { subjects: [<id>],             # 具体主体（人/AI 代理的裸 id，已展开）
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

**`flowork.contrib` 便利包**（可选，一行挂上，纯基于 OrgDirectory 端口实现）：`manager_of(one_user)→one_user`（返回其上级；吃 `org:` 返回部门负责人——数据由宿主目录自定；吃 `role:` 设计期报错）、`users_with_role(string)→users`、`has_role(one_user, string)→bool`。部门类词汇（org_of、dept_leader 等）的数据源无端口支撑，不进 contrib，由宿主注册。

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

（位点统一注：assignee/assignees/recipients 为**进入节点**逐项 CEL 求值；会签出口在**每次办理后**以当前计数求值；子流程输入映射在子实例启动时按父上下文求值、输出映射在子完结时按子上下文求值；静态检查对全位点同一套——见 4.3。）

### 5.6 身份快照（actor_context）【v2.1 新增：审计的严肃性要求】

**问题**：流程审计要回答的不只是「当时是谁审的」，还有「**他当时是谁**」——岗位、角色、部门。组织数据在宿主那边且会变，一年后光有 `user:zhangsan` 和时间戳，答不出「张三凭什么有权限批这笔单」。

**机制（内核管定格的时机，宿主管定格的内容）**：

- 任务完成的**事务内**（complete/reject/transfer/auto_pass 等所有动作），引擎调 `OrgDirectory.snapshot(subject)`，把返回 dict **原样**存进 `wf_task_history.actor_context`（JSON 袋，上限 4KB，超限拒收）；内核不解析、不校验、不理解内容。
- 默认实现返回 `{}`（裸跑/单测不受影响）；想要完整审计的宿主自行实现，存什么、存多细是宿主的审计策略（如 `{"dept", "title", "roles"}`）。
- 必须在完成事务内定格：事后补录存在时间窗口，组织一变补出来的就是错的；history 本来就 append-only，写入即不可篡改。
- 归因只有一条：`acted_by` 记实际操作者字符串（人/AI 一律裸 id，引擎不区分）。

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
| **消息**（✗ 新增） | 等一个外部事件带数据来 | 发布 `publish_message(name, correlation_key, payload, message_id, ttl_seconds)`。等待节点登记订阅 `(name, correlation_key)`——correlation_key 是 CEL 表达式（4.2），求值结果如报销单号用于匹配。**先到的消息进缓冲区**（wf_message_buffer，message_id 唯一防重，TTL 过期清理），订阅出现时按先来先服务关联；关联成功 → 载荷按节点 output 映射写入变量（与人工任务完成的写入路径同一条）→ 推进。订阅生命周期：节点离开（完成/取消/实例终止）即关，暂停期间保持。消息也可启动实例（start_on_message，见 7.1） |
| **信号**（✗ 新增） | 等一声广播（不带数据） | `signal(name)`：唤醒所有在等这个名字的订阅。与消息的分工：信号只唤醒不写数据，消息带数据且点对点 |

**实例时限（deadline）【v2.2 新增】**：图纸顶层 `deadline: {after_minutes, strategy}`，strategy ∈ mark（实例打超时标记＋发事件，流程继续跑）/ cancel（作废）/ terminate（终止）。发起时按工作日历折算 deadline_at 物化进实例表，到点由 timer job 触发 `instance_deadline_reached` 事件并执行策略；实例已完成则静默不触发。暂停期间冻结、恢复后顺延（与节点定时器同规则）。依据：Temporal 的 Workflow Execution Timeout 是原生实例级超时（限制实例总生命周期）；BPMN 系引擎无原生等价物（要拿子流程包住主流程再挂中断型定时边界事件）——flowork 取声明式字段，不逼用户画「定时炸弹并行分支」。

**终止/取消＝砍树（子树级联）【v2.3 第十五轮】**：`terminate` / `cancel` 与实例时限的 cancel/terminate 策略都是**整树动作**——父实例进入终态时，未结算子实例（含暂停/挂起中的）递归同置终态，待办撤销落历史（outcome=cancelled）；已结算子不动。顺序契约：先把树内全部未完令牌撤光、再逐实例落终态（子先父后）——`resume_parent_if_waiting` 只在父令牌仍 unfinished 时推进，先撤令牌保证子终态不误唤醒中间层。事件：父终态事件携带 `cascaded_instances` 清单，每个级联子发同款终态事件并带 `cascade_from` 溯源。多实例提前收工（§8.3 completion 命中终止剩余 running 子）**不走此语义**——那是完成侧的「提前下班」，paused/suspended 子属管理员域不动；管理员砍树不豁免暂停子（对齐 Camunda/Zeebe 子树终止，行业标配）。

### 7.1 start_on_message：消息启动实例【v2 新增，2026-10-09 定稿】

定义顶层声明 `start_on_message: [{name, correlation_key?, input_mappings?, start_user?, business_key?}]`（列表可多条，常见一条）——**启动订阅＝定义派生**：published 定义即生效、retire 即失效，不落订阅表（部署动作天然增删订阅，零表结构变更）。

- **匹配顺序**（publish 内，同事务）：在途节点订阅优先（既有 FCFS）→ 启动匹配 → 缓冲。同一消息先喂给在等实例，未被消费才可启动新实例。
- **消息上下文**：`payload.*`（消息载荷）+ `now`。实例尚不存在——initiator/prev_task/instance 等不可用（静态检查同 mapping_output 先例：变量引用不查，payload 形状消息侧才知；语法与函数注册表照查）。
- **correlation_key**（可选）：声明位表达式对消息上下文求值，结果须 string 且**等于 publish 携带的 correlation_key** 才命中（防串单）；未声明 → name 命中即启动。
- **input_mappings**（可选）：右侧表达式于消息上下文求值 → 启动变量，过 input 契约 start 边界（越界拒收，P0-6）。
- **start_user**（可选表达式，须 string）：缺省合成 `message:{name}`（initiator 解析为此值，归因可追溯）。
- **business_key**（可选表达式，须 string）：→ 实例业务键（招标单号等对账场景）。
- **幂等与防重定格**：重复 publish 同 message_id 在匹配前静默忽略（既有）；启动消费把消息定格为 matched 订阅行（node_id=`(start)`），进「buffer ∪ 订阅定格」防重并集——同 message 至多启动一个实例。
- **不回溯缓冲**：部署时刻起算——部署前到达的缓冲消息不被启动匹配消费（避免「一部署冒出一批实例」的意外）；缓冲消息仍只在节点订阅登记时被反查（第 7 章既有语义不变）。
- **事件**：命中启动＝process_started（payload 增 `start_message: {name, message_id}`）+ message_correlated（定格记录配套，node_id=`(start)`）；message_received 仅缓冲路径发（与节点直接命中同口径）。
- **错误语义**：声明位表达式求值错 / 非 string → **技术故障**（错误二分，第 8 章）：publish 命令失败（消息不落库不缓冲），错误信息带定义 code 与表达式——不静默吞、不伪装成未命中。
- **版本语义**：start_on_message 变更＝定义内容变更（content_hash / minor 续发）；不新增破坏性规则（在途订阅为派生态，无残留状态需迁移）。

### 7.2 等待节点（wait）【2026-10-09 实施，wait-node.md v3 设计】

三模式合一（Conductor WAIT 先例）：**等消息**（订阅式关联，基建与第 7 章消息机制同源）、**等时长**（纯延时）、**两者竞速**（先到者决定走向——SF 回调+TimeoutSeconds 形态）。多事件竞速与等齐用图组合表达（并行分支 + any/all 汇聚），无事件网关节点。

- **进入**：实例上下文求值 correlation_key 一次并快照（非 string=技术故障）→ `subscribe` 登记（反查缓冲，消息先到 FCFS）；声明 timeout 则登记 timer Job（`kind="wait_timeout"`）。`message`/`timeout` 至少其一（validator 把关）。
- **命中**（同事务）：matched 定格（message_id 防重）→ 载荷按 `output_mappings` 白名单写入（右侧表达式于消息上下文 `payload.*`+`now` 求值，裸投影缺键得 None；未声明=全量合并，Zeebe 默认同款；均过 input 契约 node_output 边界）→ 推进。**推进仅对等待节点**（NodeBehavior.advances_on_message 标志中介——宿主在任意节点上的纯订阅用法保持仅关联不推进）。
- **到时**：订阅行置 `elapsed` 留痕 → `timer_fired` 事件（spec §7 定时器行承诺兑现）→ 推进（无载荷写入）。竞速仲裁零新代码：命中与到时各经 touch 乐观锁，先落者赢、后到跳过（不重试不死信）。
- **取消清理**：`cancel_token` 经行为 on_cancel 钩子（全仓首个调用点）关订阅+删到时 Job——any 汇聚竞速收尾/终止/实例时限统一覆盖。
- **暂停**：全动作拦截原则——订阅保持、命中落缓冲（TTL），恢复后 open 订阅反查补消费；定时器冻结恢复顺延（既有规则）。
- **结果词汇**：`wait.result`（matched|elapsed）、`wait.message_name`、`wait.message_id`（signal 唤醒=null）——仅出边条件、不落实例变量；路由审计快照钉 wait 键。
- **配套面**：`correlate_message`（同步关联：不缓冲、命中回传签收凭证 {instance_id/node_id/business_key/definition_code}、无人在等 None、重复幂等回放——Zeebe correlate 端点同款）；`waiting` 反查（open 订阅谁在等，CLI `flowork message waiting`）。api 面零新符号（dict 返回）。
- **明确不做**：周期性触发（调度域，用户裁定）、等到绝对时刻（CEL 日期表达式后置）、边界事件/中断式等待（长期观察）。

## 8. 错误模型：什么算「业务上的不通过」，什么算「系统坏了」

**二分原则**：业务上的失败（审批驳回、表单不合格）是**流程语义**，走图纸里画好的路；系统故障（接口连不上、表达式算错）不是流程语义，**停下来等人修**（故障单），绝不假装成驳回。

| 类别 | 例子 | 处理 |
|---|---|---|
| 业务失败 | 审批驳回；output 不符合 input 契约（拒绝提交） | 驳回走 reject_target；契约不合格当场拒收——都不产生故障单 |
| 技术故障 | action 抛异常、CEL 求值错、外部 worker 重试耗尽 | 先自动重试（5.2）；耗尽 → 死信 + 故障单（state=open）。人工处置：`resolve_incident(instance_id, action)`，action 现状 resume/cancel ✓，【v2】扩 retry（重派）/skip（跳过节点）/fail（终止实例）。根因没修好，故障单会再冒出来（允许复发，成文） |

## Part B：接口层（怎么跟内核打交道）

## 9. SPI 端口（内核的标准插座，内核只依赖协议）

现有 2 个端口 ✓（默认实现保证内核可裸跑可单测）：`Clock.now()`；`OrgDirectory`（【v2.1】瘦身为引擎必需四方法，见下）。（**死端口收口，2026-10-09**：Notifier 删除——notify 动作实际直接 `record_event` 落 outbox，事件即内核原生通知面，端口零消费多余；ResourceProvider 删除——M-B 预声明零消费，落地时按实际形状重声明；**WorkCalendar 删除【v2.3 第二批 A5】——全仓零消费的死端口，且行业对拍确认双日历口径零实现（历史耗时一律墙钟、C7 按人算的参数是死参数、Flowable 已删）**；将来要工作日超时按 Flowable 形态重立（节点级具名日历+注册表+发布期校验），不复活本端口。VariableStore 端口于变量 I/O 批删除——零引用零实现：变量持久化由实例行 JSON 列承担，schema 边界校验落在 contract.py 四边界，外部读写走 §11 RuntimeService 变量面。）

**找人分工（v2.1 定稿）**：取人（组→人）＝表达式词汇，全部宿主注册（5.5，内核零内置，contrib 三个便利函数基于本端口实现）；关系判断（人→组）与身份定格＝**引擎自己的行为**，走本端口——方向相反，自定义函数表达不了（「我的待办」要算张三属不属于 role:finance，是引擎在问，不是图纸在问）。

【v2】新增 2 个端口、重构 OrgDirectory（签名如下；ResourceProvider 曾在此预声明，2026-10-09 死端口收口删除）：

```python
class OrgDirectory(Protocol):            # ◐ v2.1 瘦身定稿：引擎必需的四方法
    def is_member(self, user: str, group: str) -> bool: ...        # 人→组：候选命中判断（引擎查「张三属不属于这个组」）
    def subjects_in(self, group: str) -> list[str]: ...            # 组→人：候选列表展开（直接成员，不含子部门，5.5）
    def manager_of(self, user: str) -> str | None: ...             # 转上级（*宿主词汇的数据源*，如 contrib 的 manager_of 函数；引擎自身不再消费【v2.3 第十四轮】）
    def snapshot(self, subject: str) -> dict: ...                  # 身份快照：完成事务内定格操作者当时的岗位/角色/部门（5.6）

class ExpressionFunctions(Protocol):     # ✓ CEL 自定义函数来源（4.3，已接线；contrib.cel_vocabulary 三词汇）
    def functions(self) -> Mapping[str, Callable]: ...

class EventSink(Protocol):               # ✗ 事件外发（默认=outbox 轮询；webhook 实现挂这）
    def emit(self, envelope: dict) -> None: ...
```

**目录的默认实现与强制程度（显式选择，不强制实现）**：默认给**静态目录**（配置文件写死映射，小团队、demo、CI 裸跑够用）；空配置时四方法返回 False/[]/None/{}。validate 对「图纸用了 role:/org: 组主体而目录为空配置」给**警告**（deploy 不拦，后果＝组待办无人能认领，宿主部署时已见过警告）；escalate 目标由图纸的 escalate_to 表达式给出（5.1），不再要求目录实现 manager_of。纯服务编排、agent 编排类宿主根本不用组主体，完全不需要实现目录。

铁律（CI 强制）：内核模块 import 任何插件包即红灯。

## 10. 领域事件

现有 14 个 ✓（process_started/completed/cancelled；node_entered/exited/cancelled/failed；task_created/completed/rejected/claimed/transferred；incident_raised；job_dead_lettered）。【v2】增补 14 个：process_paused / process_resumed / process_terminated / definition_deployed / definition_retired / definition_purged / task_delegated / task_resolved / task_escalated / incident_resolved / timer_fired / instance_deadline_reached【v2.2】 / message_received / message_correlated。【变量 I/O 批，2026-10-08】增补 variables_updated（仅外部变量更新命令发射，payload 含 actor 与逐键改前/改后值）。幂等部署（内容哈希相同）不发 definition_deployed（降噪）。

- **信封**：CloudEvents 1.0.2——type=事件名，id=outbox 行 id（UUID，消费端按 id 去重），source=`flowork://{engine_id}`，data=事件字段。
- **交付保证**：事件与业务变更**同一个数据库事务**写入 outbox（transactional outbox，库形态独有优势）；分发为至少一次，消费端需幂等。
- **不发什么**：引擎内部写变量不发事件（防刷屏——任务提交/钩子/映射的变量变更走 wf_variable_history 留痕表，见 14 章；血缘查询可得）；外部变量更新命令例外，发 variables_updated（低频高关注）；传阅已读不发（查询可得）。

## 11. Python API（进程内调用，四组）

| 组 | 方法 | 现状 |
|---|---|---|
| 定义 | `deploy / validate / dry_run / semantic_diff / verdict(预演) / get_definition / list_definitions / retire / purge / definition_drift` | ✓ 全部（P0-4/P1-6；get_definition 参数为 code/major/revision） |
| 实例 | `start_process(code, variables, start_user, business_key?, major?, revision?, idempotency_key?, reuse?)`；`pause / resume / terminate / get_instance`；`update_variables(instance_id, updates, *, actor, reason?)` / `variables_of(instance_id)` / `variable_history(instance_id, *, name?, limit, offset?)`【变量 I/O 批，2026-10-08】；**`simulate`【v2.3 第十二批 A10】（整图试跑→SimulationReport：条件边真求值、service_task 恒 stub、wait 冻结为卡点、事件不投 outbox、报告复用路由审计）** | ✓ 全部（幂等三策略 P0-5；pause/resume/terminate P1-3；分页为 offset 式非游标） |
| 任务 | `get_task / list_tasks / claim / unclaim / complete / reject / transfer / assist / complete_assist / add_countersigner / list_cc_tasks / mark_cc_read`；`anticipate(task_id, outcome) → {node_exit, instance_complete}` | ✓ 全部（anticipate 只读预判，P1） |
| 系统 | `signal / publish_message`【v2】；`activate_jobs / complete_job / fail_job`【v2】；`list_incidents / resolve_incident / move_token` | ✓ 全部（消息 P1-1；worker 租约 P1-2；resolve 五动作 P1-4） |

启动幂等三策略：`reject`（撞 key 报错，默认）/ `return_existing`（返回已有实例）/ `allow_duplicate`（明示放弃幂等）。任务完成侧幂等已由历史表 idempotency_key 支撑 ✓。

**anticipate（只读预判）【v2.2 新增】**：给定任务与假想 outcome，返回 `{node_exit, instance_complete}`——预判「照此提交后节点是否出、实例是否完」。纯读：不落库、不改任何状态（读 token 会签计数＋出口规则即时计算）。用途：UI 的「你是最后一个审批人」提示、AI 预审。主流引擎无等价 API（Camunda/Flowable/jBPM/Zeebe/Temporal 均未提供，源自 对标产品 的产品化创新），是 flowork 的 AI 原生差异点。

## 12. REST API（web 包，C8 v2 风格）【v2 大部分新增】

- 搜索：`POST /v2/{definitions|instances|tasks|jobs|incidents|messages|events}/search`，请求体 `{filter, sort:[{field,order}], page:{after?/before?, limit}}`（游标分页）；响应带 `next_cursor`。
- tasks 过滤维度：state × assignee × candidate × flow_code × instance_id × priority × due_before（后两项依赖 wf_task 新列，第 14 章）。
- 动作：`POST /v2/tasks/{id}/{claim|unclaim|complete|reject|transfer|assist|complete_assist}`；`POST /v2/instances/{id}/{pause|resume|cancel|terminate|signal}`；`POST /v2/messages/publish`；`POST /v2/jobs/activate`（长轮询：request_timeout 参数）；`POST /v2/jobs/{id}/{complete|fail}`；`POST /v2/definitions/deploy`；`POST /v2/definitions/{code}/retire`；`GET /v2/definitions/{code}?major=&revision=`（purge 仅 CLI，不出 REST）。
- 只读预判【v2.2】：`POST /v2/tasks/{id}/anticipate`（body 携带假想 outcome，返回 `{node_exit, instance_complete}`，不落库不改状态）。
- 变量读写【变量 I/O 批登记，随 /v2 批实施】：`GET /v2/instances/{id}/variables`（值全集）；`PATCH /v2/instances/{id}/variables`（body 为 `{name: value}` 合并写，校验三门槛：白名单/类型/非终态，语义见 docs/product/kernel-v2/variable-io-api.md）；`GET /v2/instances/{id}/variables/history`（游标分页，可按 name 过滤）。写需 scope `instances:variables`（engine-v1 02-权限模型）。
- 错误体统一 `{"error": {code, message, details}}`。

## 13. MCP 工具（M7 落地）

首批 18 个：validate_spec / dry_run / deploy / **definition_drift**（AI/CI 对账，6.5）/ list_definitions / start_instance / query_instance / signal_instance / publish_message / **update_instance_variables**（外部改值，随 M7 落地，入参出参复用 §11）/ list_my_tasks / claim_task / complete_task / reject_task / **anticipate_task**（AI 预判提交后果，只读，v2.2）/ pause_instance / list_incidents / resolve_incident。入参出参直接复用第 11 章的 pydantic DTO（tasks/models.py 已为 MCP 预留 typed DTO ✓）。

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
| wf_variable_history（新表，变量 I/O 批 2026-10-08） | instance_id, name, old_value, new_value, source, acted_by, created_at；追加只写，全写方覆盖（任务提交/钩子/外部命令），命令尾 diff 产生，值相等跳过 |

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
2. **§5.1 超时改 deadline 列表形态**（对齐 WS-HumanTask「多 deadline 各挂 escalation」）：`deadlines: [{after_minutes, action}]` 五动作（notify/reassign/escalate/auto_approve/auto_reject）；多 notify＝多级预警（对标产品 timeoutWarning1..5 的标准形态替代）；escalate＝**图纸给目标**（`escalate_to` CEL 表达式，必填；v2.3 第十四轮起引擎不再调 OrgDirectory.manager_of）→查无转故障单；现状 TimeoutPolicy 为退化形态，validate 兼容、semantic diff 等价。§2.5/§4.2/§3.3/C.3/C.5 联动。
3. **实例级 deadline 新增**（§3.1 字段/§3.3 示例/§3.4+§14 deadline_at/§7 语义/§10 事件）：Temporal 原生 Workflow Execution Timeout 同款；BPMN 系需子流程边界定时事件建模，flowork 取声明式字段。新事件 instance_deadline_reached（增补 13→14）。
4. **§5.1 驳回落点豁免 auto_pass 成文**：现状靠解析器 manual_participants 短路隐式实现（行为等价于 对标产品 doOnRejectedActivity），升格为语义保证，防「发起人驳回→重提交→又被跳过」打转及未来重构丢失。
5. **anticipate 只读 API 新增**（§11/§12/§13，MCP 首批 16→17 加 anticipate_task）：预判提交后果，主流引擎无等价物，定位 AI 原生差异点。
6. **§1 非目标 +2**：提交时自选下一节点（BPMN 系引擎均无此路由模式，输出变量＋条件边建模——推翻讨论初稿「加 allow_target_choice」的倾向，§2.4 落建模指引）；催办归宿主/通知插件。
7. **开工项 +3**：P0-7（汇聚放行）、P1-8（deadline 演进）、P1-9（anticipate）。

讨论过但**不采纳**：审批托付按流程限定作用域（delegation_of 加流程参）——与第六轮「OrgDirectory 瘦身为引擎必需四方法」定稿冲突，托付替换可在宿主目录/表达式层组合实现，记下不做。对拍确认不借的 对标产品 设计：驳回复活改写历史行（flowork 新任务行＋cause_token_id 更优）、Redis 分布式锁、引擎内线程池异步推进、connection 空节点、实例 bak 归档体系、单 int 版本号。


### 第八轮（2026-10-09，v2.2→v2.3，对标产品 对标两轮定稿）

背景：先以 对标产品 基准版本 行为基准源码逐文件对拍内核现状（产出 [对标明细.md](./对标明细.md) + [对标明细详设.md](./对标明细详设.md)，A 档 11 项候选），再以主流开源引擎源码级对拍校准（[delta-industry-benchmark.md](./delta-industry-benchmark.md)：Camunda 7.23/8、Flowable 7、jBPM 7.74、Temporal、SpiffWorkflow、Conductor）。用户拍板采纳综合建议。本轮落 spec 四项：

1. **动作票数语义定稿（§5.1 新增块）**：transfer＝票随人走（转办不增减应办总数、被转办人完成计一票）；add_countersigner＝加签人各占一票（item_count 随名单同步增加）；assist 不计票。「不占票的加签」用 assist/consult 表达，两路分流——对标产品 rootTaskId 组算一票的归并形态不采纳（主流引擎无票数概念无从对齐，且归并破坏 item_count 与任务行数对应）。
2. **驳回复活圈定「最近一轮」（§5.1 驳回条款）**：复活名单＝目标节点最近一个已完结令牌（token_no 最大）名下办理人，多轮驳回名单不膨胀；无完结令牌才回退全量历史。同时声明与主流的**刻意差异**：BPMN 系引擎重入时按定义重新解析办理人，flowork 复活原办理人（国内审批语义），原名单为空时兜底走表达式解析。
3. **前视式自动跳过列入非目标（§1）**：「他后面还会审、这关免审」（participateLater 形态）——主流引擎零实现（GitHub 全域 lookAheadParticipants 零命中）、需用未来变量决策、失效模式是审批链空洞且审计不可见；向后看三策略（auto_pass_originator/previous/participated）覆盖真实诉求，前视信息只做 anticipate 只读提示。
4. **WorkCalendar 表述诚实化（§5.1 超时）**：现状＝自然分钟直算（端口零消费，spec 原文「按 WorkCalendar 折算」与实现不符）；行业事实＝历史耗时全行业一律墙钟、零引擎按人算日历（C7 按人算的 Task 参数是死参数、Flowable 已删），**双日历口径明确不做**；端口收敛方向（删除或仅接 deadline 到期折算）随第二批拍板。

撤回下游守卫（撤回策略与取消范围的拍板④）由下游闸门自然覆盖，其 spec 条款随第二批 A2 实施落地。行业对标三大事实与四批实施排序见 delta 文档；实施纪律新增一条共识：**可见优于静默**（本轮所有引擎坑几乎都是静默形态——静默删子任务/默认不清理/静默截断人数/静默跳过校验）。

### 第九轮（2026-10-09，v2.3 续，对标第二批实施）

第八轮拍板后的第二批落地（依据 对标明细详设.md A1/A2/A5 修正版），落 spec 三项：

1. **撤回语义定稿（§5.1 新增块，拍板④落地）**：撤回＝取消当前全部未完令牌+原节点新建（"取消当前+新建上游"，不改写历史，对齐全行业"完成不可变"铁律）；**下游闸门**＝正向可达下游（图遍历不评条件）存在人工进展（非取消已办，或 user_task/call_activity 的已完结令牌）即拒绝并提示改用驳回，错误携带进展明细；穿透节点（branch/cc_task/start/end）的即时完结不拦（对齐 对标产品 穿透 CONNECTION/CIRCULATE；pilot leave 实证 branch 即刻完结曾误拦，首版实现即踩此坑后修正）。闸门生效后取消范围不再需要精细算法（拍板④由本闸门自然覆盖）。撤回归类**实例修改类**动作，授权归宿主开放面。行业零实现，flowork 首创。
2. **动作表与 available_actions（§5.1 新增块，A1 修正版落地）**：动作可用性＝引擎声明表按 (state × task_type × 认领态) 判定（tasks/actions.py），**不是**节点级配置；`TaskInfo.available_actions` 服务端算完下发——主流引擎均无此面（三引擎任务响应逐字段核实），AI/前端拿到任务即知能做什么。分组：work（complete/reject）/dispatch（transfer/assist/add_countersigner/claim/unclaim）。
3. **WorkCalendar 端口删除（§5.1 超时/§9，A5 收口）**：全仓零消费死端口；行业对拍定论＝历史耗时全行业一律墙钟、零引擎按人算日历（C7 按人算的 Task 参数是死参数、Flowable 已删），双日历口径不做；after_minutes 定稿为恒自然分钟。将来要工作日超时按 Flowable 形态重立（节点级具名日历+引擎级注册表+发布期校验），不复活本端口。公开面符号删除（api 快照测试同步）。

同批工程事实：实现中发现并修复首版闸门的穿透误拦（见 1）；并行会话的等待节点 WIP 同处工作区，全量套件的两例红（registry/world_catalog）属该 WIP 的未完成状态，与本批无关。

### 第十轮（2026-10-09，v2.3 续，对标第三批 A8 落地）

第三批启动（A8 先行实装；A11/A10 因并行「等待节点」WIP 占用 validator*/behaviors.py 落点，先出设计稿待其落地后动工——simulation-dryrun-design.md / deploy-validation-design.md）：

1. **consult 咨询实装（§5.1 动作全集/票数块/动作表联动）**：「先收集意见、原办理人最后拍板」＝派生 task_type=consult 意见任务（独立工作项：独立记录/意见/审计，不建 deadline、不进会签计数——**意见不占票**，outcome=agree/reject 由 type=consult 区分语义）；**主任务软挂起**＝同链（root_task_id 链，转办保持链）存在未完 consult → 主任务所有动作被拦（守卫挂 _claim_task_action 咽喉，覆盖 complete/reject/transfer/assist/加签，不引入新任务状态——意见全交回即解禁，复活零成本）；最后一条交回发 task_consult_finished 事件；主任务自身 deadline 时钟照走（咨询不豁免时限）。与 add_countersigner 分流：占票的加签/不占票的咨询。行业修正落地：不抄 C7 delegation 的 assignee 轮转。
2. **MRO 教训（工程）**：Mixin 桩模式要求提供方在 MRO 中先于消费方桩——TaskConsultMixin 位于 TaskOpsMixin 之后导致其守卫实现被桩遮蔽；守卫改为内联自含查询（task_actions.py），跨 Mixin 派发不再依赖基类顺序。

### 第十一轮（2026-10-09，v2.3 续，对标第三批 A11 落地）

部署期引用校验 L3+L4 实装（definitions/validator_deploy.py，由 deploy 在事务内调用——与纯设计时 validator*.py 分居：这两层要查库/查注册表/查目录）：

1. **L3 定义内引用**：call_activity 引入 `binding` 字段（§2.5）——deployment 绑定＝目标未发布 deploy 期 **ERROR**（Zeebe deployment binding 同义）；latest（缺省）＝**WARNING**（子流程可独立部署、运行期解析最新版，先子后父合法）；自引用（递归流程首版）合法跳过。service_task handler 未注册＝**WARNING**（实施修订：先 deploy 后 register_handler 是既有合法模式，测试即此用法）；strict 升 ERROR。
2. **L4 目录实体存在性**：取人位（assignee/assignees/candidate_users/recipients/reassign_to）CEL 表面量经 `subject_exists` 钩子（duck-typing 探测 org 丰富面，不动 SPI 四方法）；False＝WARNING、strict ERROR；None＝静默跳过（目录未实现该能力，零噪音——行业一致：组织实体引用部署期不校验，flowork 自持目录有能力做但默认不拦发布）。
3. **纪律落地**：`EngineConfig.strict_deploy_validation=False`（缺省）——全部 L3/L4 默认 WARNING 留痕（logger），可见优于静默；True 时升 ERROR 拦发布（fail-fast）。字面量提取 `string_literals`（lark AST，STRING_LIT token——宿主于 validator_deploy.py，cel.py 行数红线）。
4. A10（模拟试跑）设计稿就绪待下一批（simulation-dryrun-design.md；落点 behaviors.py 已随等待节点批释放）。

### 第十二轮（2026-10-09，v2.3 续，对标第三批 A10 落地——模拟试跑第二层）

模拟实例（simulated）实装（simulation-dryrun-design.md 第二层；第一层令牌模拟仍属 dry_run 域）：

1. **入口与标记**：`start_process(..., simulated=True)` 与 `RuntimeService.simulate(code, variables) -> SimulationReport`；实例行 `simulated` 列（迁移 0002）。条件边按初始变量**真求值**（与令牌模拟层的显式选择相反）。
2. **行为分支五点**（kind 知识住在行为里——`NodeBehavior.performs_external_work` 类属性声明外部副作用，引擎核心零 kind 分支，kind 守卫测试实证）：user_task 进入即完成（approval_exit=simulated，不建任务、不走 auto_pass 票数语义）；service_task 恒 **stub**（行业共识：模拟绝不执行真实副作用）；cc_task 不建传阅件；call_activity 递归传播 simulated（子实例同标记）；wait 冻结为卡点（不订阅、不建到时 Job——**卡点即报告发现**）。
3. **隔离（行业空白，flowork 一等公民化）**：事件不投 outbox（`CommandContext.suppress_events`——试跑不得惊动订阅方；路由审计行不受影响=报告数据源）；不建任何 Job（含实例时限）；`history.instances()` 默认排除模拟实例（`include_simulated=True` 可含）。
4. **报告**（engine/simulation.py，F2 现查模式不预存）：终态＋令牌时间线＋stub 清单（performs_external_work 节点到访）＋卡点（首个未完令牌所在）＋路由轨迹（wf_route_decision 现查——F3 资产天然数据源）。**报告诚实呈现覆盖度**：stub 与卡点都是发现。执行错（如公式错）按既有二分直接抛给试跑调用方（模拟失败=定义会挂， Caller 即诊断对象）。
5. 真实路径零影响（回归断言：真实启动仍建任务/发事件）；builder 产 FlowNode extra 袋的 getattr 惯例延续（同第十一轮）。

### 第十三轮（2026-10-09，v2.3 续，对标第四批 A9 落地——多实例子流程，对标系列完结）

Zeebe inputCollection/outputCollection 契约实装（multi-instance-call-design.md）：

1. **fan-out**：subjects 逐个解析（取人语义：查无传播→零子实例＝节点立即完成，skip 语义）＝每人一个子实例（`parent_index` 列记迭代序，迁移 0003）；输入映射按子求值并注入保留标识符 `subject`（catalog@mapping_input——`input_mappings={"dept": "subject"}` 每子拿自己的切片）。
2. **汇聚**：completion 公式（注入 `child_total/child_completed/child_failed`，catalog@completion 新位点）**只做提前收工**——命中即终止剩余 running 子（撤令牌与待办，对齐 terminate 形态；suspended/paused 属管理员域不动）；缺省 all＝全员结算；失败计数入 `child_failed`，公式可表达「一子失败即阻断」（completed==total）或「忽略失败」。**不做失败机制**（C7 教训：提前完成丢失「谁反对」）。
3. **聚合**：collect＝输出按 parent_index 对齐聚合 list（失败子位＝null 占位，下标稳定、完成序无关）；merge 仅限单子（validator ERROR 拦 MI+merge——多子逐子覆写即 last-wins，C7 公开痛点）。
4. **validator/catalog**：subjects 空/merge+MI/completion 空三规则（ERROR）；`subject`、三计数入目录（目录即版本承诺：加词=小版本）；completion 位接入 iter_expression_positions（表驱动静态检查）。
5. **工程**：subprocess_mi.py 拆分（行数红线）；builder extra 袋 dict→CallInstanceConfig 归一（`_mi_config`，同第十一轮 getattr 惯例）；迁移幂等化（基线 create_all 新库已含列则跳过——alembic 幂等测试实证 batch 多列追加的伪循环依赖，改 `op.add_column`+存在性守卫）。

**对标四批（A 档 8 项 + 修复 + 竞态窗）就此全部完结**：第一批修正确性、第二批立守卫、第三批上能力面、第四批 Zeebe 契约。行业修正版清单与拍板记录见 对标明细.md 三篇。

### 第十五轮（2026-10-09，v2.3 续，收口批：时钟统一＋终止级联）

来源：收口盘点两处已核实语义缺口（与 escalate 批同日并行会话撞号——本批提交信息中写作「第十四轮」，登记序以 spec 为准：escalate 批先入，本批为第十五轮；提交 541369c/de8bb03）。

1. **时钟统一**：全表达式位 `now` 与 worker Job 租约/退避恒取 `EngineConfig.now`。盘点发现 8 个装配位点（路由条件 evaluation_variables、取人 resolve_subjects、会签出口 exit_reached、超时改派 fire 位、子流程映射、MI completion、消息 correlation 写入/启动匹配）装配上下文时未传 `now`（`expression_context` 缺省墙钟），worker Job 三处（create run_at/领活租约/失败退避）直用 `utc_now`——FakeClock 下被测对象与测试时钟不同源（route_audit 挂账「审计时刻与任务时刻不一致」同根，一并收口）。修复形态：三函数增 `now` 参数、十四调用点显式穿针（含 Review 补漏的驳回复活路径 reject_flow 侧 disapprove_exit——办理侧与驳回侧同一节点出口同一时钟；不是缺省改时钟——引擎位点必须显式传，缺省墙钟保留给无引擎装配面并写入文档）；`tests/semantics/test_clock_unification.py` 每位点一个「假钟阈值」判例（实施前 8/8 红、实施后全绿——判别力依赖阈值定在假钟未来/墙钟过去的区间）。
2. **终止/取消＝砍树**（§7 新段）：terminate/cancel/实例时限 cancel/terminate 三入口共用 `terminate_cascade`——未结算子实例（含暂停/挂起中的）递归同置终态、待办撤销留痕、父事件带 `cascaded_instances` 清单/子事件带 `cascade_from`。翻转 `tests/suite/test_construct_subprocess` 曾钉住的「孤儿运行」开放问题钉子；`test_terminate_cascade.py` 六走查（整树/孙层/暂停子/已结算不动/时限策略/无子空清单）。

### 第十六轮（2026-10-10，v2.3 续，`agent:` 写法零兼容删除——主体统一裸 id）

来源：用户裁定「彻底把 `agent:` 删掉」（2026-10-10）。背景：第十四轮已定「人字段＝裸 id」，`agent:` 前缀在引擎内零解析、零语义（§5.4 明文「是不是 agent 走 acted_as_agent 标志与宿主目录，不走字符串装饰」），仅余目录参考实现两处剥离兼容与审计展示写法——纯装饰；保留它反而制造「同一主体两种写法」在非互认比较点静默失配的隐患（§5.4 诚实口径段同款问题）。

1. **删除范围**：① `DirectoryStore.manager_of` 与静态 `OrgDirectory.manager_of` 的 `agent:` 剥离（AI 代理与用户一样写裸 id；裸 id 找不到人时按 agent 表找 owner 的能力不变）；② 目录写审计 subject 裸化（`agent:{id}` → 裸 id，操作类型由审计 action 承载）；③ §3.2/§5.1/§5.4、docs/API.md 与端口 docstring 写法收口；④ 测试钉零兼容（旧写法查无返回 None，不静默命中）。
2. **零兼容**：`agent:{id}` 写法（含存量图纸）不再被解析——`manager_of("agent:x")` 人表、agent 表都查无返回 None；escalate 位这样写会在 fire 时出故障单（可见错误，不静默）。
3. **保留**：Agent 一等身份（目录 AgentRow/凭证/状态机/owner 问责）与「AI 可作 assignee」能力原样；`user:` 前缀互认保留（§5.4）。

### 第十七轮（2026-10-10，v2.3 续，Agent 域整体移除——引擎对 AI 无治理职能）

来源：用户裁定「对 agent 本身没有任何审核与治理的职能」（2026-10-10，承第十六轮的进一步收缩；推翻第十六轮第 3 条「保留 Agent 一等身份」）。判断依据：AI 与人在行为上无法区分（同一待办通道、同一办理 API），引擎不该也无法区分主体类型；AI 的身份、凭证、问责、治理一律归宿主身份系统。

1. **删除范围**：① 目录 Agent 域全量——org_agent/org_agent_credential 两表、注册/状态机（trial-active-suspended）/凭证签发吊销校验/owner 查询、manager_of 的「找不到人按 agent 找 owner」特例（manager_of 回到纯人语义：人→部门主管、`org:`→部门负责人、查无 None）；② 引擎归因链——complete/reject/update_variables 的 as_agent 参数、wf_task_history 与 wf_variable_history 的 acted_as_agent 布尔列、CLI --as-agent、variables_updated 事件的 as_agent 字段、TaskHistoryInfo/VariableChangeInfo 字段、CommandContext.write_as_agent；③ 迁移 0004 删表删列（幂等，downgrade 不提供）；④ 测试净删 17 例（agent 生命周期/凭证/owner 升级/AI 快照定格），覆盖矩阵 org.agent_* 条目同步。
2. **保留**：「AI 可作 assignee」能力不变——AI 是普通主体（裸 id 字符串），领待办/办理/驳回/候选认领与人同一条路。超时自动办的 actor=system:timeout 语义不变（原 as_agent=True 实为「系统代办」的混用，随删一并澄清）。`user:` 前缀互认保留（§5.4）。
3. **归因口径**：历史与变量流水的归因＝acted_by 字符串（谁就是谁）；「这一步是不是 AI 办的」不再是引擎可回答的问题——宿主如需人机区分，自管主体命名空间或外挂审计映射。engine-v1 01/02 的 Agent 身份与 agent_credential 设计随批废止（权限模型 L1 收为 user_token/api_key 两类，AI 经宿主凭证以普通 principal 接入）。

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

Subject：人字段＝**裸 id**（`u_zhang` / `bot1`，引擎不解析前缀）、组字段＝`role:{name}` | `org:{code}`【v2.3 第十四轮，见 5.4】；ParticipantSet 两槽；函数类型词汇（参数与返回共用，另含 CEL 原生标量）：one_user/users/groups/mixed/bool。

### C.3 状态枚举（全部封闭，validate 拒绝未知值）

实例 running/paused/completed/cancelled/terminated；定义 published/retired（**v2 取消 draft**）；任务活表 ready/completed/cancelled；已办 outcome agree/reject/transfer/auto_pass/withdrawn；令牌 unfinished/finished；Job scheduled/…/dead-letter（值开工时对齐现状）；故障单 open/resolved 及处置 resume/cancel【v2 扩 retry/skip/fail】；推进 any/all；路由 first_match/all_match；会签 parallel/sequential；deadline 动作 notify/reassign/escalate/auto_approve/auto_reject【v2.2 列表形态】；实例时限策略 mark/cancel/terminate【v2.2】；无人策略 raise/to_admins；幂等复用 reject/return_existing/allow_duplicate。

### C.4 表达式命名空间与内置函数签名【v2 定义】

| 命名空间 | 类型来源 |
|---|---|
| input.* | 按 input JSON Schema |
| payload.* | 消息上下文（仅 start_on_message 位，7.1）：消息载荷，形状消息侧才知（静态不查引用） |
| vars.* | 运行时（静态检查按声明，未声明按 any 降级为警告） |
| task.* | **位点相关快照**（静态检查：非可用位引用 task.* 即 ERROR task_unavailable）——超时改派位 ✓：{id, assignee, outcome:null, created_at, deadline_at}（fire 时注入，活任务无 outcome）；会签出口位 ✓：{item_count, approve_count, disapprove_count}（每次办理后注入）；条件求值/输出映射位注入为后续项（开放问题 C5） |
| initiator | subject |
| instance | 实例全局事实 {id, started_at, business_key}（全位点注入；started_at 为 datetime 与 now 同口径，可直接做时长运算；dry-run/公式试算面注入占位形状；2026-10-09 落地） |
| now | datetime（引擎统一时钟 naive UTC；一次装配钉一时刻，expression-dataface F1；**全引擎位点恒注入 `EngineConfig.now`【v2.3 第十五轮时钟统一】**——FakeClock 下条件求值、任务时限与路由审计同源；`expression_context` 缺省墙钟仅供无引擎装配面：dry-run 静态预检、web 公式试算） |
| prev_task | 上一站已完成任务快照 {assignee, outcome, comment, finished_at, used_time}（条件+取人位；查无不注入，F2；used_time 为自然时长口径——暂停剔除待定） |
| node("code") | 引擎注入函数 → 平铺 {assignee, outcome, comment, finished_at, used_time, runs, skipped, item_count, approve_count, disapprove_count}（runs=已结束轮数；票数取最近轮令牌三计数；查无＝固定形状：人物 null 计数 0，F2） |

contrib 签名（便利包，可选，纯基于 OrgDirectory 端口实现）：`manager_of(one_user)→one_user`（返回其上级；吃 `org:` 返回部门负责人；吃 `role:` 设计期报错）、`users_with_role(string)→users`、`has_role(one_user, string)→bool`。部门类词汇（org_of 等）数据源无端口支撑，由宿主注册。自定义函数注册必须带全签名（参数＋返回，词汇表见 5.5）；内核注册表出厂为空。词汇目录（标识符＋函数，含中文 label 与位点）权威在 `expr/catalog.py`。

### C.5 词法与单位规则【v2 定义】

- code：`^[a-z][a-z0-9-]{0,99}$`；**组引用**：`^(role|org):[A-Za-z0-9_@.\-+]+$`（人字段＝裸 id，不设前缀约束）；id 一律 UUID 字符串。
- searchable 变量仅限 string/number/boolean（object/array 标 searchable → validate 报错）。
- correlation_key 表达式必须求值为 string（否则设计期报错）。
- 会签出口＝CEL 布尔公式（4.2；缺省＝该侧全票），旧专用语法 BNF 已随位点统一退役。
- 单位：时长一律分钟（after_minutes），落盘秒数；金额无单位纯数值；时区全 UTC。
- deadlines：after_minutes 必须为正整数；同一节点上限 8 条；顶层实例时限 deadline 为单对象（非列表）。
- priority 取值 0–100（小→大＝低→高），默认 50。

### C.6 错误与 DTO

异常四族：定义类（DefinitionError：UnknownNodeKind / InvalidDefinitionData / 校验失败）／运行类（handler、求值异常）／冲突类（LockConflictError / DuplicateStartError / TaskAlreadyClaimedError）／插件类（插件自定义）。每族映射 REST `error.code`。对外 DTO：TaskInfo / TaskHistoryInfo / InstanceInfo / EventInfo（✓ 已有）＋【v2】JobInfo / IncidentInfo / DefinitionInfo / 搜索请求响应（filter+sort+cursor）。
