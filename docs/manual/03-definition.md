# 03 画流程：定义（完整参考）

本章是流程定义的完整参考：顶层字段逐个讲、八种节点各自的字段表、连线与路由规则、
变量声明与输入契约、设计期校验。定义的**发布与版本管理**（deploy 管线、破坏性
判定、更新策略）是下一个大话题，单独在第 4 章讲——本章只管「怎么把图画对」。

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
（版本由 deploy 判定，不靠手填）。

## 顶层字段全表

`ProcessDefinition`（JSON 模板的顶层键）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | str | 定义代码，**身份标识**——版本与实例都挂在它下面（必填） |
| `name` | str | 显示名 |
| `major` / `revision` | int | 版本号。**deploy 时自动覆写**，模型默认值仅占位——手填不算数 |
| `status` | str | `published` / `retired`（deploy 即 publish；draft 已取消，编辑态在 git / 设计器草稿） |
| `input` | JSON Schema | 输入契约（见下文「输入契约」） |
| `deadline` | 对象 | 实例级时限：`{after_minutes, strategy}`——整单最大存活时间，到点按 strategy（`mark` 打标 / `cancel` 作废 / `terminate` 终止）处置 |
| `start_on_message` | 列表 | 消息启动声明（第 8 章细讲） |
| `nodes` | 列表 | 节点（见下文） |
| `flows` | 列表 | 连线（见下文「连线与路由」） |
| `variables` | 列表 | 流程变量声明（见下文「变量声明」） |
| `layout` | 映射 | 画布坐标 `{code: {x, y}}`——纯展示数据，引擎不消费，改坐标不算语义变更 |

实例级时限的 JSON 写法：

```json
{ "code": "flash_sale", "deadline": { "after_minutes": 120, "strategy": "cancel" },
  "nodes": [...], "flows": [...] }
```

## 所有节点共有的字段

每种节点（`nodes` 数组里的每个对象）都有这四个基类字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | str | 节点代码，图内唯一（必填）——事件、任务、路由审计都用它定位 |
| `name` | str | 显示名 |
| `kind` | str | 节点类型（八选一或自定义类型，必填） |
| `advance_policy` | str | 汇聚策略：`any`＝任一入边到达即激活（缺省，同批兄弟臂竞速取消）；`all`＝全部到齐才激活。只在多入边节点上有意义；`cc_task` 恒按 any |
| `hooks` | 对象 | 声明式钩子（第 10 章） |
| `properties` | 映射 | 扩展袋——放 UI / 外部系统的附加配置，引擎不解释 |

## 八种内置节点逐个讲

### start / end：起终点

```json
{ "code": "start", "name": "发起", "kind": "start" }
```

每个定义**恰好一个** `start`（发起入口，不允许有入边）和**至少一个** `end`
（终点，不允许有出边）。实例在**所有**令牌到终点后完成；`end` 多条入边时，
它自己的 `advance_policy` 决定何时算「到了」。

### user_task：人工审批（最常用）

人工节点，进入时按取人字段生成待办。**四个取人槽位**（每项都是 CEL 表达式，
两种写法只能选其一：单办 `assignee`，或者多人 `assignees` / 候选池二选一）：

| 字段 | 语义 |
|---|---|
| `assignee` | 单人办理（一个表达式，解析出多人会直接报错） |
| `assignees` | 多人列表（配合 `multi_instance` 做会签） |
| `candidate_users` | 候选人池——求值出的人可 `claim` 认领（第 6 章） |
| `candidate_groups` | 候选组池——组引用（如 `'role:finance'`）认领时按目录判归属 |

完整字段表：

| 字段 | 类型 | 说明 |
|---|---|---|
| 取人四槽 | 见上 | 详见第 5 章 |
| `multi_instance` | 对象 | 会签配置：`{mode: parallel\|sequential, approve_exit, disapprove_exit}`（第 6 章细讲） |
| `reject_target` | str | 驳回回退的目标节点 code（缺省回退到**来源节点**——令牌从哪来的回哪去） |
| `auto_pass_originator` | bool | 发起人自己是办理人时自动通过 |
| `auto_pass_previous` | bool | 上一站办理人再次遇到时自动通过 |
| `auto_pass_participated` | bool | 本节点已办过的人（重回合）自动通过 |
| `no_participant_policy` | str | 取人解析为空：`raise`（挂起+故障单，缺省）/ `to_admins`（目录 admin 兜底） |
| `deadlines` | 列表 | 超时动作（上限 8 条，第 6 章细讲）；旧版单条 `timeout` 字段仍兼容读取 |

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

执行一个注册过的动作（handler）。未注册的动作部署期给 WARNING
（先部署后注册是合法顺序）、执行期解析不到才出故障单。

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | str | 动作名——`engine.register_handler(action, fn)` 注册的键 |
| `params` | 映射 | 静态参数，原样传给 handler 的 `params` 参数 |

```json
{ "code": "payment", "name": "执行打款", "kind": "service_task",
  "action": "make_payment", "params": { "channel": "default" } }
```

### branch：条件分支

纯路由锚点，本身不做任何事——它的价值是给「按条件选路」一个明确的决策落点
（第 9 章的路由审计每行都锚在某个节点上）。出边挂条件，`route_mode` 决定匹配方式：

| 字段 | 取值 | 说明 |
|---|---|---|
| `route_mode` | `first_match`（缺省） | 按出边顺序取**第一条**条件为真的走；全不中走默认边 |
| | `all_match` | 条件为真的出边**全部**激活（一分为多）；全不中走默认边 |

**同时进行（并行）不需要 branch**：任何节点有多条无条件出边即并行分流。

### cc_task：传阅

进入即给 `recipients`（CEL 表达式列表）每人生成一条传阅件，随即推进——
传阅不阻塞流程。恒按 `any` 推进（多入边不等待）。

```json
{ "code": "notify", "name": "结果传阅", "kind": "cc_task",
  "recipients": ["initiator", "'u_archive'"] }
```

### call_activity：子流程

调用另一个已发布的流程定义，可带父子数据映射与多实例展开（第 8 章细讲）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `process_code` | str | 被调定义的 code |
| `binding` | str | `latest`（缺省，运行期解析最新已发布版）/ `deployment`（部署父时子必须已发布） |
| `is_synchronous` | bool | 同步等待子流程完成（缺省 true） |
| `input_mappings` | 映射 | 进入子流程时求值（**父上下文**）注入子实例变量 |
| `output_mappings` | 映射 | 子流程完成时求值（**子上下文**）回写父实例变量 |
| `multi_instance` | 对象 | 多实例：每人一个子实例（第 8 章） |

```json
{ "code": "sub_check", "name": "合规子流程", "kind": "call_activity",
  "process_code": "compliance_check",
  "input_mappings": { "amount": "amount" },
  "output_mappings": { "compliance_result": "compliance_result" } }
```

### wait：等待

流程在这里停住，等外部事件或时间到了再走。三种模式可组合（第 8 章细讲）：
等消息（`message`）/ 等时长（`timeout`）/ 两者竞速先到者走；结果词汇 `wait.*`
只能用在它的**直接出边**条件上。

```json
{ "code": "gate", "name": "等合同回传", "kind": "wait",
  "message": { "name": "contract_signed" },
  "timeout": { "after_minutes": 1440 },
  "output_mappings": { "signed_at": "payload.signed_at" } }
```

## 连线与路由

连线（`flows` 数组里的每个对象）的完整字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` / `target` | str | 起止节点 code（必填） |
| `name` | str | 显示名（如「大额」） |
| `condition` | str | CEL 布尔公式（≤2048 字符）。**没有条件的边恒为真** |
| `is_default` | bool | 默认边——其他条件边全不中时走它 |
| `on_take` | 对象 | 连线被走过时触发的声明式钩子 `{handler, params}`（第 10 章） |

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

## 变量声明与输入契约

### 变量声明（variables）

图纸顶层声明流程变量，是设计器与校验器的元数据：

```json
"variables": [
  { "name": "amount", "type": "number", "label": "报销金额",
    "sample": 1000, "searchable": true }
]
```

| 字段 | 说明 |
|---|---|
| `name` / `type` | 名字与类型（string / number / boolean / list） |
| `label` | 显示名 |
| `sample` | 示例值（dry-run 取样默认值） |
| `searchable` | true 时变量值同步进检索索引（**只有 string/number/boolean 可标**），宿主可按变量值搜实例 |

声明**不约束**运行时传参，但它是校验器的依据：条件里引用了既没声明也不是
保留词的变量名，设计期校验直接报 ERROR——图纸自证的第一道防线。
`searchable` 集合的变化是**破坏性变更**（第 4 章 R2：宿主的检索与报表依赖它）。

### 输入契约（input）

更硬的一道门：JSON Schema（2020-12 子集），三处边界把关——

1. **发起边界**（start）：`start_process` 传的变量不合 schema，拒绝发起；
2. **提交边界**（complete）：`complete(..., variables=...)` 带的变量不合 schema，拒绝办结；
3. **输出边界**（output_mappings）：节点输出映射的结果不合 schema，按技术故障处理。

实测（`amount` 必填 number）：

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

收紧契约（required 扩大、类型变严、关闭多余字段容忍、契约从无到有）都是
**破坏性变更**（第 4 章 R1）——老调用方会突然被拒，所以强制升 major。

## 设计期校验

```python
from flowork.api import create_registry, validate_definition

issues = validate_definition(definition, create_registry())
```

返回 `ValidateIssue` 列表（`level` ∈ error / warning、`rule` 规则编码、定位到
节点或连线）。常见 ERROR：

| 规则 | 拦什么 |
|---|---|
| 未知 kind / 连线指向不存在节点 | 结构错误 |
| `condition.variable.unknown` | 条件引用未声明变量 |
| `participant.group_in_subjects` | 人员位（assignee/assignees/recipients）写了组引用 `role:` / `org:`——组只属于候选槽 |
| 单人节点配多表达式 / 会签外的多人 | 取人结构错误 |
| `structure.start.incoming` 等 | start 有入边、end 有出边这类拓扑硬错 |
| `user_task.exit.misplaced` | 会签出口公式写在了节点顶层（应写在 `multi_instance` 里）——静默失效是重错误 |
| 平行重边 | 同源同目标两条连线（歧义，禁止） |

WARNING 值得看一眼（不拦发布）：`graph.subgraph.no_exit`（封闭子图——一段
永远到不了终点的结构）、`join.arm.unreachable`（all 汇聚的死臂）、
`exit.bare_threshold`（出口公式写裸数字门槛，建议乘法形式）。
`deploy` 前会再校验一次，ERROR 级直接拒发；部署期还有第二层「引用完整性」
检查（handler 注册、子流程可解析），见第 4 章。

## 本章小结

- 定义=节点+连线的有向图，顶层带版本占位、输入契约、实例时限、变量声明；
  JSON 与 FlowBuilder 同构，取人/条件全是 CEL 表达式。
- 八种节点各有字段表——人工节点的取人四槽 + 会签 + 驳回目标 + 超时是配置密度
  最高的地方。
- 并行靠多无条件出边；汇聚语义在目标节点的 advance_policy；入边集合变化＝
  破坏性变更（插节点要小心）。
- 变量声明喂校验器，输入契约是三边界的硬门；两处收紧都是 major 级变更。

图画对了，下一章讲怎么把它发出去、改起来、退役掉——deploy 管线与版本管理。
