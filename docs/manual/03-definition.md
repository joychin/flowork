# 03 画流程：定义（完整参考）

本章是流程定义的**数据结构参考**：顶层字段、八种节点、连线、嵌套配置对象，
逐表给出字段含义与是否必填。定义的**发布与版本管理**（deploy 管线、破坏性
判定、更新策略）单独在第 4 章讲——本章只管「怎么把图画对」。

## 先说清楚：「必填」有三层

字段是否必填，引擎里分三层，表格里分别标注：

| 层 | 含义 | 缺了会怎样 |
|---|---|---|
| **装载必填** | pydantic 模型无默认值 | `load_definition` 直接抛错，模板根本读不进来 |
| **校验必填** | 设计期校验 ERROR | `deploy` 被拦，报规则编码与定位 |
| **事实必填** | WARNING 或运行期才爆 | 能发布，但跑起来会挂起出故障单，别依赖 |

另一个装载性质：**顶层与嵌套配置对象对未知字段报错**（拼写错误当场拦下），
但**节点对未知字段是收进扩展袋**（保留往返无损、引擎不解释）——所以把配置
写错位置（比如会签出口写在节点顶层）不会报错、只会静默不生效，校验器为此
专门设了 `user_task.exit.misplaced` 这类规则兜底。

## 两种写法，同一种东西

流程定义是一张**有向图**：节点（nodes）+ 连线（flows）。它有两种写法，产物完全同构：

- **JSON 模板**：一个 `.json` 文件就是一张图纸。适合放 git 管理、给设计器编辑、
  被 AI 生成。仓库 `examples/` 下有现成例子。
- **FlowBuilder**：Python 链式构建器。适合在代码里组装（尤其要参数化生成时），
  `build()` 出来就是 `ProcessDefinition`。

同一张图，两种写法对照（取自 `examples/01_simple_approval.json` 与等价的 Builder 代码）：

```json
{
  "code": "simple_approval",
  "name": "串行审批（示例 01）",
  "nodes": [
    { "code": "start",  "name": "发起",     "kind": "start" },
    { "code": "manager", "name": "经理审批", "kind": "user_task", "assignee": "'u_manager'" },
    { "code": "finance", "name": "财务复核", "kind": "user_task", "assignee": "'u_finance'" },
    { "code": "end",    "name": "结束",     "kind": "end" }
  ],
  "flows": [
    { "source": "start",   "target": "manager" },
    { "source": "manager", "target": "finance" },
    { "source": "finance", "target": "end" }
  ]
}
```

```python
from flowork.api import FlowBuilder

definition = (
    FlowBuilder("simple_approval", "串行审批")
    .start("start", "发起")
    .user_task("manager", "经理审批", assignee="'u_manager'")
    .user_task("finance", "财务复核", assignee="'u_finance'")
    .end("end", "结束")
    .connect("start", "manager")
    .connect("manager", "finance")
    .connect("finance", "end")
    .build()
)
```

注意 `assignee: "'u_manager'"` ——取人字段的值是 **CEL 表达式**，字面量的人名要带引号
（`'u_manager'` 是字符串字面量，`u_manager` 是变量引用）。第 5 章专门讲表达式。

两条装载路径：

```python
from flowork.api import create_registry, load_definition, load_definition_file

definition = load_definition_file("examples/01_simple_approval.json", create_registry())
definition = load_definition(json_dict_or_text, create_registry())   # 内存态 / CI 生成
```

装载时未知 kind 直接抛 `UnknownNodeKind`（设计期拦截）；`version` 字段被忽略
（版本由 deploy 判定，不靠手填）；老模板里的 `status: "draft"` 视同 published。

## 顶层字段全表（ProcessDefinition）

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `code` | str | **装载必填** | — | 定义代码，业务身份——版本与实例都挂在它下面 |
| `name` | str | 可选 | `""` | 显示名 |
| `major` / `revision` | int | 可选 | 1 / 1 | 版本号。**deploy 时自动覆写**，手填不算数 |
| `status` | str | 可选 | `published` | 只读状态（published / retired）；draft 已取消 |
| `input` | JSON Schema 对象 | 可选 | 无契约 | 输入契约，三边界把关（见下文） |
| `deadline` | 对象 | 可选 | 无时限 | 实例级时限（子字段见下表） |
| `start_on_message` | 列表 | 可选 | `[]` | 消息启动声明（子字段见下表；第 8 章细讲） |
| `nodes` | 节点列表 | 校验必填非空 | `[]` | 必须恰好一个 start（`structure.start.count`）且至少一个 end（`structure.end.missing`） |
| `flows` | 连线列表 | 可选 | `[]` | 连线（见下文「连线与路由」） |
| `variables` | 列表 | 可选 | `[]` | 流程变量声明（见下文「变量声明」） |
| `layout` | 映射 | 可选 | `{}` | 画布坐标 `{节点code: {x, y}}`——纯展示，引擎不消费，**不参与内容哈希**（挪画布不算新版本） |

三个嵌套配置对象的字段：

**实例级时限（`deadline`）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `after_minutes` | int | **装载必填**（>0） | — | 整单最大存活分钟数，发起时物化 deadline_at |
| `strategy` | str | 可选 | `mark` | 到点处置：`mark` 打标继续跑 / `cancel` 作废 / `terminate` 终止 |

**消息启动声明（`start_on_message` 的每一项）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `name` | str | **装载必填** | — | 消息名——发布同名消息即发起实例 |
| `correlation_key` | str | 可选 | `""` | 关联键（空＝消息名命中即启动；配了则发布方须带相同关联键，防串单） |
| `input_mappings` | 映射 | 可选 | `{}` | 载荷 → 启动变量（右侧表达式在 `payload.*` 上下文求值） |
| `start_user` | str | 可选 | `""` | 发起人表达式；缺省合成 `message:{name}` |
| `business_key` | str | 可选 | `""` | 业务键表达式 → 实例 business_key |

**流程变量声明（`variables` 的每一项）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `name` | str | **装载必填** | — | 变量名（不可与保留标识符重名） |
| `type` | str | 可选 | `"string"` | string / number / boolean / list |
| `label` | str | 可选 | `""` | 显示名 |
| `sample` | 任意 | 可选 | 无 | 示例值（dry-run 取样默认值，类型须与 type 一致） |
| `searchable` | bool | 可选 | `false` | 值同步进检索索引（**仅 string/number/boolean 可标**）；集合变化＝破坏性变更（R2） |

## 所有节点共有的字段（FlowNode 基类）

`nodes` 数组里的每个对象，无论什么 kind，都有这些基类字段：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `code` | str | **装载必填** | — | 节点代码，图内唯一——事件、任务、路由审计都用它定位 |
| `kind` | str | **装载必填** | — | 节点类型（八种内置或已注册的自定义类型），未知值装载即抛 `UnknownNodeKind` |
| `name` | str | 可选 | `""` | 显示名 |
| `advance_policy` | str | 可选 | `null`（行为等同 any） | 汇聚策略：`any`＝任一入边到达即激活（同批兄弟臂竞速取消）；`all`＝全部到齐才激活。只在多入边节点上有意义；`cc_task` 恒按 any |
| `hooks` | 对象 | 可选 | 空对象 | 声明式钩子 `{on_enter/on_exit/on_cancel/on_fail: {handler, params}}`（第 10 章）；`handler` 名必填 |
| `properties` | 映射 | 可选 | `{}` | 扩展袋——放 UI / 外部系统的附加配置，引擎不解释 |

## 八种内置节点逐个讲

### start / end：起终点

```json
{ "code": "start", "name": "发起", "kind": "start" }
```

没有专有字段。结构规则（都是**校验必填**级）：恰好一个 start（`structure.start.count`）、
start 不允许有入边、至少一个 end（`structure.end.missing`）、end 不允许有出边。
实例在**所有**令牌到终点后完成；`end` 多条入边时，它自己的 `advance_policy`
决定何时算「到了」。

### user_task：人工审批（最常用）

人工节点，进入时按取人字段生成待办。**四个取人槽位**（每项都是 CEL 表达式）：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `assignee` | str | 四槽**至少配一个**（全空＝WARNING `user_task.no_participant`，运行期按 `no_participant_policy` 兜底） | 无 | 单人办理（一个表达式，解析出多人直接报错） |
| `assignees` | 列表 | 同上 | `[]` | 多人列表（配合 `multi_instance` 做会签） |
| `candidate_users` | 列表 | 同上 | `[]` | 候选人池——求值出的人可 `claim` 认领（第 6 章） |
| `candidate_groups` | 列表 | 同上 | `[]` | 候选组池——组引用（如 `'role:finance'`）认领时按目录判归属 |

其余字段全表：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `multi_instance` | 对象 | 可选 | 无（＝单任务） | 会签配置（子字段见下表；第 6 章细讲） |
| `reject_target` | str | 可选 | 无（驳回回退到来源节点） | 驳回回退的目标节点 code |
| `auto_pass_originator` | bool | 可选 | `false` | 发起人自己是办理人时自动通过 |
| `auto_pass_previous` | bool | 可选 | `false` | 上一站办理人再次遇到时自动通过 |
| `auto_pass_participated` | bool | 可选 | `false` | 本节点已办过的人（重回合）自动通过 |
| `no_participant_policy` | str | 可选 | `"raise"` | 取人解析为空：`raise`（挂起+故障单）/ `to_admins`（目录 admin 兜底） |
| `deadlines` | 列表 | 可选 | `[]` | 超时动作（子字段见下表；上限 8 条，超出＝ERROR） |
| `timeout` | 对象 | 可选 | 无 | **旧形态**单条超时（`{due_minutes, strategy}`）——新图请用 `deadlines`；两处都配时 deadlines 优先 |

**会签配置（`multi_instance`）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `mode` | str | 可选 | `"parallel"` | `parallel` 并签（全员同时待办）/ `sequential` 串行（逐人依次办） |
| `approve_exit` | str | 可选 | 全员同意 | 通过口径：CEL 布尔公式，上下文注入 `task.approve_count` / `task.item_count` / `task.disapprove_count` 三计数 |
| `disapprove_exit` | str | 可选 | 全员否决 | 否决口径：同上 |

**超时动作（`deadlines` 的每一项）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `after_minutes` | int | **装载必填**（>0） | — | 进入节点多少分钟后触发（自进入起算，自然分钟） |
| `action` | str | 可选 | `"notify"` | 五枚举：notify / reassign / escalate / auto_approve / auto_reject |
| `reassign_to` | str | **action=reassign 时校验必填**（ERROR） | 无 | 改派目标，CEL 表达式（fire 时求值，`task.*` 可用） |
| `escalate_to` | str | **action=escalate 时校验必填**（ERROR） | 无 | 升级目标，CEL 表达式——引擎不含组织模型，科层组织写 `manager_of(task.assignee)` |

一个带代表性的完整写法（JSON）：

```json
{ "code": "directors", "name": "总监会签", "kind": "user_task",
  "assignees": ["'u_alice'", "'u_bob'"],
  "multi_instance": {
    "mode": "parallel",
    "approve_exit": "task.approve_count * 100 >= task.item_count * 51",
    "disapprove_exit": "task.disapprove_count >= 1"
  },
  "reject_target": "manager",
  "auto_pass_originator": true,
  "deadlines": [
    { "after_minutes": 25, "action": "notify" },
    { "after_minutes": 240, "action": "escalate", "escalate_to": "manager_of(task.assignee)" }
  ]
}
```

### service_task：系统节点

执行一个注册过的动作（handler）。

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `action` | str | **事实必填** | `""` | 动作名——`engine.register_handler(action, fn)` 注册的键。空/未注册：部署期 WARNING（先部署后注册合法），运行期解析不到＝技术故障挂起 |
| `params` | 映射 | 可选 | `{}` | 静态参数，原样传给 handler 的 `params` 参数 |

```json
{ "code": "payment", "name": "执行打款", "kind": "service_task",
  "action": "make_payment", "params": { "channel": "default" } }
```

### branch：条件分支

纯路由锚点，本身不做任何事——它的价值是给「按条件选路」一个明确的决策落点
（第 9 章的路由审计每行都锚在某个节点上）。

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `route_mode` | str | 可选 | `"first_match"` | `first_match`：按出边顺序取**第一条**条件为真的走；`all_match`：条件为真的出边**全部**激活。全不中走默认边 |

结构规则：最多一条默认边（`branch.default.duplicate`＝ERROR）；出边少于 2 条
给 WARNING（仅作透传，提醒你可能放错了节点）。

**同时进行（并行）不需要 branch**：任何节点有多条无条件出边即并行分流。

### cc_task：传阅

进入即给收件人每人生成一条传阅件，随即推进——传阅不阻塞流程。

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `recipients` | 列表 | 可选（空列表＝没人收到，通常非本意） | `[]` | 收件人 CEL 表达式列表（`initiator` / `'u_archive'` / 取人函数） |
| `advance_policy` | str | 固定 | `"any"` | 恒按 any 推进（多入边不等待），配置了也会被覆盖 |

```json
{ "code": "notify", "name": "结果传阅", "kind": "cc_task",
  "recipients": ["initiator", "'u_archive'"] }
```

### call_activity：子流程

调用另一个已发布的流程定义（第 8 章细讲行为）：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `process_code` | str | **事实必填** | `""` | 被调定义的 code。空/查无：部署期 L3 WARNING（binding=latest 时），运行期解析不到＝技术故障 |
| `binding` | str | 可选 | `"latest"` | `latest`：运行期解析最新已发布版；`deployment`：部署父时子必须已发布（L3 ERROR 硬拦） |
| `is_synchronous` | bool | 可选 | `true` | 同步等待子流程完成再推进 |
| `input_mappings` | 映射 | 可选 | `{}` | 进入子流程时求值（**父上下文**），结果注入子实例变量 |
| `output_mappings` | 映射 | 可选 | `{}` | 子流程完成时求值（**子上下文**），结果回写父实例变量 |
| `multi_instance` | 对象 | 可选 | 无（＝单次调用） | 多实例配置（子字段见下表） |

**多实例子流程配置（`multi_instance`）**

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `subjects` | 列表 | **事实必填**（空＝节点立即完成，等价跳过） | `[]` | 取人表达式（CEL）——每人解析出一个子实例；输入映射按子求值时注入 `subject` |
| `completion` | str | 可选 | `"all"` | 完成公式：`"all"`＝全员结算；CEL 布尔公式＝提前收工（注入 `child_total` / `child_completed` / `child_failed` 三计数） |
| `output_mode` | str | 可选 | `"collect"` | `collect`：输出按迭代序对齐聚合为列表（失败位补 null）；`merge`：逐子覆写——**仅限单子**，配了多实例 validator 直接拦 |

```json
{ "code": "sub_check", "name": "合规子流程", "kind": "call_activity",
  "process_code": "compliance_check",
  "input_mappings": { "amount": "amount" },
  "output_mappings": { "compliance_result": "compliance_result" } }
```

### wait：等待

流程在这里停住，等外部事件或时间到了再走（第 8 章细讲行为）：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `message` | 对象 | `message` 与 `timeout` **至少其一**（双缺＝ERROR `wait.mode.required`） | 无 | 等消息：`{name: 必填, correlation_key: 可选 CEL}`——关联键进入时求值一次定格 |
| `timeout` | 对象 | 同上 | 无 | 等时长：`{after_minutes: 必填(>0)}`——进入起算，暂停冻结恢复顺延 |
| `output_mappings` | 映射 | 可选 | `{}` | 载荷 → 变量（右侧在 `payload.*` 消息上下文求值）；**不声明＝全量合并**（payload 顶层键直接进变量） |

```json
{ "code": "gate", "name": "等合同回传", "kind": "wait",
  "message": { "name": "contract_signed" },
  "timeout": { "after_minutes": 1440 },
  "output_mappings": { "signed_at": "payload.signed_at" } }
```

## 连线与路由

连线（`flows` 数组里的每个对象）的完整字段：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `source` | str | **装载必填** | — | 起点节点 code（不存在＝ERROR `flow.endpoint.missing`） |
| `target` | str | **装载必填** | — | 止点节点 code（同上） |
| `name` | str | 可选 | `""` | 显示名（如「大额」） |
| `condition` | str | 可选 | 无（＝恒真） | CEL 布尔公式，≤2048 字符。**没有条件的边恒为真** |
| `is_default` | bool | 可选 | `false` | 默认边——其他条件边全不中时走它（一个节点的默认边至多一条） |
| `on_take` | 对象 | 可选 | 无 | 连线被走过时触发的声明式钩子 `{handler, params}`（第 10 章） |

结构规则：同源同目标两条连线＝ERROR（`structure.flow.duplicate`，平行重边禁止）。

路由的完整规则（引擎自动做，你只需要理解）：

1. 节点办完，引擎对它的**每条出边**求值条件；
2. 无条件出边全部激活（并行分流）；branch 节点按 `route_mode` 选路；
3. 目标节点多条入边时，它的 `advance_policy` 决定何时激活：
   `any`＝任一入边到达即激活（同批到达的兄弟臂竞速取消）、`all`＝到齐才激活。
   **汇聚语义写在目标节点上，不在连线上**。

一个典型组合——「金额分流，大额走总监会签，小额直接传阅」：

```python
.branch("route", "金额分流", route_mode=RouteMode.FIRST_MATCH)
.flow("route", "directors", condition="amount > 5000")
.flow("route", "notify", default=True)
```

三个容易想偏的点：

- **并行不需要网关**：多条无条件出边就是并行；想「二选一」必须用条件边；
- **默认边建议配**：branch 全不中且没有默认边时流程挂起等条件——除非你确实
  想「等到某条件成立」；
- **改图注意拓扑**：任何节点**入边集合**的变化都算破坏性变更（第 4 章 R4），
  往中间插一个节点会把前后两个节点的入边都改掉。

## 输入契约（input）

比变量声明更硬的一道门：JSON Schema（2020-12 子集），三处边界把关——

1. **发起边界**（start）：`start_process` 传的变量不合 schema，拒绝发起；
2. **提交边界**（complete）：`complete(..., variables=...)` 带的变量不合 schema，拒绝办结；
3. **输出边界**（output_mappings）：节点输出映射的结果不合 schema，按技术故障处理。

schema 本身的字段就是标准 JSON Schema：`type: "object"`、`properties`
（各字段 `type` / `enum` 等）、`required`（必填键列表）、`additionalProperties`
（默认容忍多余键，`false`＝拒收）。实测（`amount` 必填 number）：

```text
缺必填   -> FloworkError: input 契约校验失败（边界=start，位置=<root>）：'amount' is a required...
类型错   -> FloworkError: input 契约校验失败（边界=start，位置=amount）：'很多' is not of type...
多余键   -> 放行（additionalProperties 默认容忍）
```

```json
"input": {
  "type": "object",
  "properties": { "amount": { "type": "number" }, "title": { "type": "string" } },
  "required": ["amount"]
}
```

收紧契约（required 扩大、类型变严、enum 收窄、关闭多余字段容忍、契约从无到有）
都是**破坏性变更**（第 4 章 R1）——老调用方会突然被拒，所以强制升 major。

## 设计期校验规则总表

`validate_definition`（以及 deploy 内置的同款强校验）的常见规则速查：

| 规则编码 | 级别 | 拦什么 |
|---|---|---|
| `structure.start.count` / `structure.end.missing` | ERROR | start 不是恰好一个 / 一个 end 都没有 |
| `structure.start.incoming` / `structure.end.outgoing` | ERROR | start 有入边 / end 有出边 |
| `structure.flow.duplicate` | ERROR | 同源同目标平行重边 |
| `structure.node.deadend` | ERROR | 死端节点（有入边但没有出路的非 end 节点） |
| `structure.node.unreachable` | WARNING | 不可达节点（从 start 走不到——常是删线漏了半截） |
| `flow.endpoint.missing` | ERROR | 连线端点节点不存在 |
| `condition.variable.unknown` | ERROR | 条件引用未声明变量 |
| `participant.group_in_subjects` | ERROR | 人员位写了组引用 `role:` / `org:`——组只属于候选槽 |
| `user_task.exit.misplaced` | ERROR | 会签出口公式写在节点顶层（应写在 `multi_instance` 里） |
| `user_task.deadlines.too_many` | ERROR | 超时动作超过 8 条 |
| `user_task.deadline.reassign_to_missing` / `escalate_to_missing` | ERROR | reassign/escalate 动作没带目标 |
| `wait.mode.required` | ERROR | 等待节点既没 message 也没 timeout |
| `user_task.no_participant` | WARNING | 审批节点一个取人槽都没配 |
| `branch.single_outgoing` | WARNING | 分支节点出边少于 2 条（仅作透传） |
| `exit.bare_threshold` | WARNING | 会签出口写裸数字门槛（建议乘法形式） |
| `graph.subgraph.no_exit` / `join.arm.unreachable` | WARNING | 封闭子图 / all 汇聚死臂（图级 lint） |

部署期还有第二层「引用完整性」检查（L3：handler 注册与子流程可解析；L4：取人
字面量的目录存在性），见第 4 章。

## 本章小结

- 「必填」分三层：装载必填（缺了读不进来）、校验必填（ERROR 拦发布）、
  事实必填（WARNING/运行期才爆）——每张表都标了层。
- 顶层结构与嵌套配置对象对未知字段报错；节点未知字段进扩展袋（写错位置
  静默不生效，靠校验规则兜底）。
- 人工节点的取人四槽 + 会签 + 驳回目标 + 超时是配置密度最高的地方；
  wait 双缺、escalate 无目标、平行重边这些都是设计期硬拦。
- 并行靠多无条件出边；汇聚语义在目标节点的 advance_policy；入边集合变化＝
  破坏性变更（插节点要小心）。

图画对了，下一章讲怎么把它发出去、改起来、退役掉——deploy 管线与版本管理。
