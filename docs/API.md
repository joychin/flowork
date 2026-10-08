# flowork API 参考（v0.1 · M6）

> 入口：`ProcessEngine(config?)` → 四服务 `engine.repository / runtime / task / history` + `engine.jobs`。
> 所有写操作带 `actor` 归因；AI 代办传 `as_agent=True`（审计区分）。

## 装配

```python
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.participants.org_directory import OrgDirectory

engine = ProcessEngine(EngineConfig(
    db_file="flowork.db",                     # 默认内存 SQLite
    org=OrgDirectory.from_yaml("org.yaml"),   # M4 参与者体系（可选）
    history_level="full",                     # none / activity / audit / full
))
engine.register_handler("my_action", handler) # ServiceTask 动作 / 声明式钩子
engine.close()
```

## RepositoryService（定义）

| 方法 | 说明 |
|---|---|
| `save_definition(definition) -> int` | 存草稿（同 code 版本自增），返回版本号 |
| `publish(code, version=None) -> int` | 发布（强制校验；发布后内容不可变） |
| `get_definition(code, version=None)` | 取已发布定义（默认最高版本） |
| `validate(definition) -> list[ValidateIssue]` | 设计时校验（结构/可达性/条件分支/开始条件/kind） |

## RuntimeService（实例）

| 方法 | 说明 |
|---|---|
| `start_process(code, *, start_user, variables=None, business_key=None) -> str` | 发起（使用定义快照） |
| `cancel_process(instance_id, actor, reason="")` | 取消（撤销未完令牌与待办） |
| `get_instance(instance_id) -> InstanceInfo` | 实例概要 |
| `list_incidents(instance_id=None)` | Incident 列表（open/resolved） |
| `resolve_incident(incident_id, action="resume"\|"cancel", actor="admin")` | 修复挂起实例 |
| `move_token(token_id, target_activity, actor="admin") -> str` | 管理员令牌直移 |
| `update_variables(instance_id, updates, *, actor, as_agent=False, reason="") -> dict` | 外部受控改值（PATCH 合并；白名单=声明∪input schema∪既有键、保留键拒、类型对位、≤256KB、终态拒；发 variables_updated） |
| `variables_of(instance_id) -> dict` | 变量值全集（只读，含运行期键） |
| `variable_history(instance_id, *, name=None, limit=50, offset=0)` | 变量血缘（改前/改后+写方归因，倒序；全写方覆盖） |

## TaskService（任务）

| 方法 | 说明 |
|---|---|
| `list_tasks(*, instance_id=None, assignee=None, state="ready")` | 待办查询 |
| `get_task(task_id)` | 单任务 |
| `complete(task_id, actor, *, comment, variables, idempotency_key, as_agent)` | 同意提交（幂等） |
| `reject(task_id, actor, *, comment, target=None, as_agent)` | 驳回回退（显式目标 > reject_target > 来源节点） |
| `claim(task_id, user)` / `unclaim(task_id, user)` | 候选任务认领/取消认领 |
| `transfer(task_id, to_user, actor, comment="")` | 转办 |
| `assist(task_id, users, actor)` / `complete_assist(task_id, actor)` | 协办（不推进流程） |
| `list_cc_tasks(*, recipient=None, instance_id=None)` / `mark_cc_read(cc_id, user)` | 传阅 |
| `add_countersigner(task_id, users, actor)` | 加签（item_count 联动） |
| `withdraw(task_id, actor)` | 撤回已办（取消下游、原人重办） |
| `activate_node(instance_id, activity_code, actor, participants=None)` | 管理员激活节点 |
| `cancel_node(instance_id, activity_code, actor)` | 管理员取消节点 |
| `finish_instance(instance_id, actor)` / `remove_instance(instance_id, actor)` | 强制完成 / 删除实例 |

`complete/reject` 返回 `TaskCompleteResult{task_id, outcome, instance_state, next_tasks}`（行动提示式）。

## HistoryService（只读查询）

| 方法 | 说明 |
|---|---|
| `events(instance_id=None, limit=200)` | 事件 outbox |
| `task_history(instance_id=None, assignee=None, limit=200)` | 已办（append-only） |
| `paged_tasks(*, assignee, instance_id, offset, limit) -> Page` | 已办分页 |
| `instances(*, state=None, definition_code=None, limit=100)` | 实例列表 |
| `tokens(instance_id)` | 令牌轨迹 |
| `trace_instance(instance_id) -> list[TraceEntry]` | 令牌+已办+事件合并时间线（审批轨迹页） |
| `archive_instance(instance_id) -> dict` | 完结实例归档导出（JSON） |

## Jobs（超时）

`engine.jobs.tick()` 推进到期 Job（测试/外部定时器）；`start(interval_seconds)/stop()` APScheduler 后台轮询。
超时配置在节点上（v2.2 列表形态，同一节点最多 8 条）：`deadlines=[TaskDeadline(after_minutes=60, action="notify"|"reassign"|"escalate"|"auto_approve"|"auto_reject", reassign_to=...)]`；`reassign_to` 为 CEL 表达式（fire 时求值，可用 `task.assignee`）。

## 扩展点

- **自定义节点**：`FlowNode` 子类（Spec）+ `NodeBehavior` 子类 + `config.node_registry.register(kind, spec, behavior)`。
- **声明式钩子**：节点 `hooks={"on_exit": {"handler": "name", "params": {...}}}` + `engine.register_handler("name", fn)`。
- **表达式函数（全位点 CEL 词汇）**：经 `ExpressionFunctions` 端口注册（必须带 FunctionSignature 签名，纯读闭包无 ctx）；一行挂官方词汇：`config.expression_functions = contrib.cel_vocabulary(org)`（manager_of/users_with_role/has_role）。取人/传阅/改派/连线条件/子流程映射共用同一注册面。
- **Listener SPI**（M6 后增强）：消费 `wf_event_outbox` 事件表即可（Notifier 端口二期接消息推送）。
