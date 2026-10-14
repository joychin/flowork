# 03 画流程：定义

本章讲怎么把业务流程写成引擎认识的定义（definition）：两种同构的写法（JSON 模板与
FlowBuilder）、八种内置节点各自干什么、连线与路由规则、变量声明与输入契约，
最后是校验与版本管理。

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
（`'u_manager'` 是字符串字面量，`u_manager` 是变量引用）。第 4 章专门讲表达式。

JSON 的装载与校验：

```python
from flowork.api import create_registry, load_definition_file, validate_definition

definition = load_definition_file("examples/01_simple_approval.json", create_registry())
issues = validate_definition(definition, create_registry())
```

## 八种内置节点

### start / end：起终点

每个定义恰好一个 `start`（发起入口）和至少一个 `end`（终点）。`end` 有多条入边时，
汇聚策略由它的 `advance_policy` 决定（见下文「连线与路由」）。实例在**所有**令牌到
终点后完成。

### user_task：人工审批（最常用）

人工节点，进入时按取人字段生成待办。四个取人槽位（每项都是 CEL 表达式）：

| 字段 | 语义 |
|---|---|
| `assignee` | 单人办理（一个表达式，解析出多人会直接报错） |
| `assignees` | 多人列表（配合 `multi_instance` 做会签） |
| `candidate_users` | 候选人池——求值出的人可 `claim` 认领（第 5 章） |
| `candidate_groups` | 候选组池——组引用（如 `'role:finance'`）认领时按目录判归属 |

其他关键字段：

- `multi_instance`：会签配置（并行/串行 + 出口公式，第 5 章细讲）；
- `reject_target`：驳回回退的目标节点 code（缺省则驳回到发起人的上一站）；
- `auto_pass_originator` / `auto_pass_previous` / `auto_pass_participated`：
  命中者自动通过（发起人自己 / 上一站办理人 / 本节点已办过的人）；
- `no_participant_policy`：取人解析为空时怎么办——`raise`（挂起+故障单，缺省）或
  `to_admins`（兜底给目录里的 admin 角色办）；
- `deadlines`：超时动作列表（上限 8 条，第 5 章细讲）。

### service_task：系统节点

执行一个注册过的动作（handler）。`action` 是动作名，`params` 是参数：

```json
{ "code": "payment", "name": "执行打款", "kind": "service_task",
  "action": "make_payment", "params": { "channel": "default" } }
```

动作由宿主注册（`engine.register_handler("make_payment", fn)`，第 9 章）。
未注册的动作在部署期给 WARNING（先部署后注册是合法顺序）、执行期抛故障。

### branch：条件分支

纯路由锚点，本身不做任何事——它的价值是给「按条件选路」一个明确的决策落点
（审计日志里能看到决策发生在哪）。出边挂条件，`route_mode` 决定匹配方式：

- `first_match`（缺省）：按出边顺序取**第一条**条件为真的走；全不中走默认边；
- `all_match`：条件为真的出边**全部**激活（一分为多）；全不中走默认边。

**同时进行（并行）不需要 branch**：任何节点有多条无条件出边即并行分流。

### cc_task：传阅

进入即给 `recipients`（CEL 表达式列表）每人生成一条传阅件，随即推进——传阅不阻塞
流程，收件人读不读都不影响走。查传阅件用 `task.list_cc_tasks`（第 5 章）。

### call_activity：子流程

调用另一个已发布的流程定义：`process_code` 指向被调定义，`input_mappings` /
`output_mappings` 做父子数据映射（CEL，第 7 章细讲）。可配 `multi_instance` 做多实例
（每人一个子实例）。

### wait：等待

流程在这里停住，等外部事件或时间到了再走。三种模式（第 7 章细讲）：等消息、
等时长、两者竞速先到者走。

## 连线与路由

连线（flows）的完整字段：

```json
{ "source": "route", "target": "directors",
  "name": "大额", "condition": "amount > 5000", "is_default": false }
```

- `condition`：CEL 布尔公式（≤2048 字符）。**没有条件的边恒为真**。
- `is_default`：默认边——其他条件边全不中时走它。建议给分支节点配一条默认边，
  否则全不中时流程会挂起等条件（对「兜底」有明确诉求时也可以不配）。

路由的完整规则（引擎自动做，你只需要理解）：

1. 节点办完，引擎对它的**每条出边**求值条件；
2. 无条件出边全部激活（并行分流）；branch 节点按 `route_mode` 选路；
3. 目标节点多条入边时，它的 `advance_policy` 决定何时激活：
   `any`＝任一入边到达即激活（同批到达的兄弟臂竞速取消）、`all`＝到齐才激活。
   汇聚语义写在**目标节点**上，不在连线上。

一个典型组合——「金额分流，大额走总监会签，小额直接传阅」：

```python
.branch("route", "金额分流", route_mode=RouteMode.FIRST_MATCH)
.flow("route", "directors", condition="amount > 5000")
.flow("route", "notify", default=True)
```

## 变量声明与输入契约

图纸顶层可以声明流程变量（`variables`），是设计器与校验器的元数据：

```json
"variables": [
  { "name": "amount", "type": "number", "label": "报销金额",
    "sample": 1000, "searchable": true }
]
```

声明不约束运行时传参，但条件里引用了既没声明也不是保留词的变量名，
设计期校验直接报 ERROR——这是「图纸自证」的第一道防线。
`searchable: true` 的变量值会同步进检索索引（按变量值搜实例）。

更进一步可以声明 **input 契约**（JSON Schema 2020-12 子集）：发起时传的变量
不满足 schema 就拒绝发起。契约在三处边界把关：发起（start）、任务提交（complete
带的变量）、节点输出映射。收紧契约（required 扩大、类型变严）属破坏性变更，
deploy 时会判 major。

## 校验与版本管理

### 设计期校验

```python
issues = validate_definition(definition, create_registry())
```

返回 `ValidateIssue` 列表（`level` ∈ error / warning、`rule` 规则编码、定位到节点或
连线）。常见 ERROR：未知节点 kind、连线指向不存在的节点、条件引用未声明变量、
单人节点配了会签以外的方式解析出多人、组引用写在人员位……`deploy` 前会再校验一次，
ERROR 级问题直接拒发。

### 部署即发布，版本自动判

```python
verdict = engine.repository.deploy(definition)
print(verdict.next_major, verdict.next_revision, verdict.breaking)
```

`deploy` 返回 `VersionVerdict` 判定报告（实测输出）：

```text
首次部署: 1 1 breaking = False          # 全新定义，major=1 revision=1
重复部署 identical_to: (1, 1)           # 内容没变 -> 幂等去重，不产生新版本
改名重发: 1 2                            # 无破坏性修改 -> revision 加一
```

破坏性规则（R1–R4）覆盖：删节点/改 kind、连线结构断路、input 契约收紧、
变量声明删除等。命中任一规则 `breaking=True`、major 加一；不想升 major 要显式带理由
（`deploy(..., force_major_reason="...")` 或 `allow_minor_reason="...")`）。
想先看后果不落库，用 `repository.verdict(definition)` 预演。

### 退役与清理

- `retire(code)`：全部已发布版本转 retired——在跑实例照常跑完，不能再发新实例；
- `purge(code)`：物理删除定义行（有实例引用时拒绝；CLI 才暴露，慎用）。

## 本章小结

- 定义=节点+连线的有向图；JSON 与 FlowBuilder 同构，取人/条件全是 CEL 表达式。
- 八种节点：start / end / user_task / service_task / branch / cc_task /
  call_activity / wait；并行靠多无条件出边，汇聚语义在目标节点的 advance_policy。
- 校验在设计期拦图纸错误；deploy 即发布，版本与破坏性自动判定，重复内容幂等去重。

下一章深入表达式与取人——CEL 怎么写、有哪些内置词汇、找人有哪些写法。
