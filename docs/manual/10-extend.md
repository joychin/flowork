# 10 扩展内核

内置的八种节点覆盖不了所有需求时，有三层扩展，由轻到重：

1. **注册 handler**：service_task 的动作由你提供（最常用）；
2. **声明式钩子**：节点生命周期上挂小段逻辑（写变量、发通知）；
3. **自定义节点类型**：加一种全新的 kind（引擎核心一行不改）。

再加一节讲组织目录的 DB 形态。

## 注册 handler（service_task 的动作）

service_task 节点只声明「调哪个动作」，动作本体由宿主注册——引擎与业务代码
完全解耦：

```python
def make_payment(ctx, params):
    ctx.variables["paid"] = True          # 写流程变量
    ctx.emit("payment_executed", {...})   # 发自定义事件（名字不受枚举约束）

engine.register_handler("make_payment", make_payment)
```

图纸里 `"action": "make_payment"`、`"params": {...}` 即引用它。`params` 是
节点上声明的静态参数字典；`ctx` 是节点上下文（能读写变量、发事件、记日志——
但**不能改变路由**，路由只属于引擎+条件求值）。

注册与部署的先后随意：先部署后注册是合法顺序（部署期给 WARNING 提示没注册，
运行期还解析不到才出故障单）；handler 挂了换新的用
`register_handler(..., replace=True)`（第 7 章故障修复用过）。

## 声明式钩子

不想写自定义节点、只想在节点进出时干点小事（打标、留痕、发通知），用钩子。
图纸里声明、按名引用已注册的 handler：

```json
{ "code": "a", "kind": "user_task", "assignee": "'u_a'",
  "hooks": { "on_exit": { "handler": "stamp_entry", "params": {"key": "a_done"} } } }
```

四个时机：

| 钩子 | 触发时机 | 典型用途 |
|---|---|---|
| `on_enter` | 进入节点时（先于行为写变量，后续条件可用） | 打进入标记 |
| `on_exit` | 节点办完、令牌落定前 | 留痕、汇总 |
| `on_cancel` | 节点被取消时（撤回 / 终止级联） | 清理 |
| `on_fail` | 节点失败时（故障单事务内） | 告警 |

实测（on_enter 钩子往变量里写 `entered=True`，发起后变量里就有）：

```python
engine.register_handler("stamp_entry",
                        lambda ctx, params: ctx.variables.update(entered=True))
# 图纸 start 节点声明 hooks.on_enter = {"handler": "stamp_entry"}
# start_process 之后：variables_of(iid) -> {'entered': True}
```

语义口径：钩子**观察与写变量，不改路由**；handler 未注册或执行抛错＝技术故障
（挂起+故障单），不会静默吞掉。例外是 `on_fail` 位点——它本身就在故障单事务内
执行，钩子异常只记日志（故障单已经在，不再叠加挂起）。

## 自定义节点类型

内置类型不够用（比如要一种「调 AI 模型预审」的节点），三步加一种——
**引擎核心一行不改**，这是内核最重要的扩展承诺（完整可跑例子见
`examples/ai_review_demo.py`）：

```python
from flowork.api import (
    FlowNode, InMemoryNodeContext, NodeBehavior, ProcessEngine,
)

# 第 1 步：Spec——声明这种节点的专有字段（自动获得校验与 JSON 序列化）
class RiskProbeSpec(FlowNode):
    kind: str = "risk_probe"
    threshold: float = 0.8

# 第 2 步：Behavior——声明这种节点的行为
class RiskProbeBehavior(NodeBehavior):
    def on_enter(self, ctx: InMemoryNodeContext) -> None:
        amount = float(ctx.variables.get("amount", 0))
        ctx.variables["risk_score"] = 0.9 if amount > 10_000 else 0.1
        ctx.emit("risk_probed", {"node": self.spec.code})

    def try_complete(self, ctx: InMemoryNodeContext) -> bool:
        return True     # 进来即算完（自动节点）

# 第 3 步：注册（引擎构造前后都行）
engine = ProcessEngine()
engine.config.node_registry.register("risk_probe", RiskProbeSpec, RiskProbeBehavior)
```

之后图纸里就能用 `kind: "risk_probe"`，专属字段（`threshold`）随节点声明：

```python
.node("probe", kind="risk_probe", name="风控探测", threshold=0.8)
.branch("route", "按风险分流")
.flow("route", "manual", condition="risk_score >= 0.8")
.flow("route", "end", default=True)
```

实测：金额 20000 的实例被路由到人工复核（`risk_score=0.9`），金额 100 的
直达终点——自定义节点的行为产出像内置节点一样参与条件路由。

`NodeBehavior` 的关键方法（全部可选，按需覆写；这里列常用面，全量见
`flowork/runtime/behavior.py`）：

- `on_enter(ctx)`：进入节点时执行（写变量、发事件）；
- `try_complete(ctx) -> bool`：节点算不算完——返回 False 就停在 `unfinished`
  （未完）等外部条件（人工任务等办理、等待节点等消息，都是这个机制）；
- `on_exit(ctx)` / `on_cancel(ctx)` / `on_fail(ctx, error)`：离开 / 取消 /
  失败时机；
- `on_task_completed(ctx, task_id, outcome)`：本节点的任务办结时（串行会签
  推进下一棒用）；
- `get_outgoing(ctx)`：定制路由出口（branch 的 first_match 就这么实现）；
- 类属性契约：`performs_external_work`（外部副作用，模拟恒桩）、
  `advances_on_message`（等消息推进）、`owns_pending_tasks`（生成待办）。

**铁律**：生命周期钩子（on_enter / on_exit / on_cancel / on_fail）只能观察与
写变量，不得改变路由——路由决策属于引擎 + 条件求值 + `get_outgoing` 覆写点。
引擎核心不存在「按节点类型 if/else」的分支，节点语义全部经注册表进行为，
这就是自定义类型能做到「不改核心」的原因。

## 组织目录的两种形态

第 5 章用了 YAML 静态目录（`StaticOrgDirectory`），适合演示与固定组织。
两种替代：

**DB 版目录**（`DirectoryStore`）——目录数据落在引擎同一个库里，可编程增改：

```python
from flowork.api import DirectoryStore, FlowBuilder, ProcessEngine

engine = ProcessEngine()
store = DirectoryStore(engine.session_factory)   # 与引擎共用同一会话工厂
store.create_user("u_fin", name="财务")
store.create_role("finance", name="财务")
store.assign_role("u_fin", "finance")
engine.config.org = store                        # 挂上即 OrgDirectoryLike

# 候选池任务按目录判归属（实测：u_fin 可认领）
# .user_task("f", "财务复核", candidate_users=["'u_fin'"])
```

**自实现协议**（`OrgDirectoryLike`）——生产环境对接自己的组织系统 / HR 数据，
挂 `EngineConfig.org` 即可。**引擎内核消费四个方法**：`is_member`（候选组归属
判定）、`snapshot`（办理人身份定格）、`delegation_of`（取人期委托替换）、
`admins`（无人兜底的管理员名单）；`subjects_in` / `manager_of` 由第 5 章的
组织词汇函数消费。协议上还有一批丰富面方法（部门族查询、`user_exists` 等），
实现方可按需退化——内核四方法缺一会在对应路径直接失败。

目录不配置时：候选组任务认不了（提示配目录）、`to_admins` 兜底没人接、
组织词汇调不了——引擎照常跑，只是「按组织找人」的能力缺数据源。

## 本章小结

- 三层扩展：register_handler（动作）→ 声明式钩子（生命周期小逻辑）→
  自定义节点类型（全新 kind，不改核心）。
- 行为与钩子的共同铁律：观察与写变量可以，改路由不行。
- 组织目录三形态：YAML 静态 / DB 版 DirectoryStore / 自实现四方法协议。

下一章讲部署：三种库形态、迁移、并发模型，以及 CLI / REST / MCP 三面概览。
