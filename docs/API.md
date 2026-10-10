# flowork API 参考（v0.2 · kernel-v2 P0/P1 后）

> 入口：`ProcessEngine(config?)` → 四服务 `engine.repository / runtime / task / history` + `engine.jobs / engine.messages / engine.worker_jobs`。
> 所有写操作带 `actor` 归因（acted_by 落历史/流水，人/AI 一律裸 id——引擎不区分主体类型）。
> **公开面纪律**：宿主/插件只准 `from flowork.api import …`（符号快照冻结）；内部深路径随时可变，不构成兼容承诺。

## 装配

```python
from flowork.api import EngineConfig, ProcessEngine, StaticOrgDirectory

engine = ProcessEngine(EngineConfig(
    db_file="flowork.db",                     # 默认内存 SQLite
    org=StaticOrgDirectory.from_yaml("org.yaml"),  # 参与者体系（可选；取人词汇数据源）
    history_level="full",                     # none / activity / audit / full（audit 当前同 full 全记）
))
engine.register_handler("my_action", handler) # ServiceTask 动作 / 声明式钩子
engine.close()
```

## RepositoryService（定义，`engine.repository`）

| 方法 | 说明 |
|---|---|
| `deploy(definition, *, published_by="system", target_major=None, force_major_reason="", allow_minor_reason="") -> VersionVerdict` | 部署定义（新增与更新同一操作，强制校验，发布即 publish、内容不可变）；major/revision 破坏性判定自动推进，target_major 续发维护分支，覆盖判定必须带 reason（进事件） |
| `verdict(...)` | 破坏性判定预演（不落库） |
| `validate(definition) -> list[ValidateIssue]` | 设计时校验（结构/可达性/CEL 全位点静态检查/图级 lint） |
| `get_definition(code, major=None, revision=None)` / `get_definition_json(...)` | 取已发布定义（缺省最高 major 的最高 revision） |
| `list_definitions(*, code_prefix="", latest_only=True)` | 定义清单（默认每 code 只列最高发布版，含 retired 标记） |
| `retire(code, actor="admin", reason="") -> int` / `purge(code, actor="admin", *, force=False) -> int` | 定义退役 / 物理清除（retire 后不可新发起，在途实例不受影响） |
| `definition_drift(directory) -> dict` | 文件与库的漂移对账（配置即代码对齐） |
| `update_draft(definition) -> int` / `designer_draft(code)` / `deploy_draft(code, *, published_by="designer")` / `consume_draft(code)` | 设计器草稿面（独立 `wf_designer_draft` 表，存/取/发布/清） |

## RuntimeService（实例，`engine.runtime`）

| 方法 | 说明 |
|---|---|
| `start_process(code, *, start_user, variables=None, business_key=None, major=None, revision=None, idempotency_key=None, reuse="reject") -> str` | 发起（定义快照；幂等三策略 reject/return_existing/allow_duplicate，撞 key 按 reuse 定行为） |
| `pause_process(instance_id, actor, reason="")` / `resume_process(...)` | 暂停（timer 冻结）/恢复 |
| `terminate_process(instance_id, actor, reason="")` / `cancel_process(...)` | 终止 / 取消（撤销未完令牌与待办，终态事件区分） |
| `get_instance(instance_id) -> InstanceInfo` | 实例概要 |
| `list_incidents(instance_id=None)` | Incident 列表（open/resolved） |
| `resolve_incident(incident_id, action="resume"\|"cancel"\|"retry"\|"skip"\|"fail", actor="admin")` | 修复挂起实例（五动作：恢复/取消/重派 dead-letter Job/跳过节点推进/判失败） |
| `move_token(token_id, target_activity, actor="admin") -> str` | 管理员令牌直移 |
| `update_variables(instance_id, updates, *, actor, reason="") -> dict` | 外部受控改值（PATCH 合并；白名单=声明∪input schema∪既有键、保留键拒、类型对位、≤256KB、终态拒；发 variables_updated） |
| `variables_of(instance_id) -> dict` | 变量值全集（只读，含运行期键） |
| `variable_history(instance_id, *, name=None, limit=50, offset=0)` | 变量血缘（改前/改后+写方归因，倒序；引擎与外部全写方留痕） |

## TaskService（任务，`engine.task`）

| 方法 | 说明 |
|---|---|
| `list_tasks(*, instance_id=None, assignee=None, candidate=None, state="ready")` | 待办查询；`candidate`=与我相关的候选（**默认含已被他人认领的任务**——「仍在池内」须组合 `assignee=""`，spec §5.5/审计 D5 明示口径；组按目录展开） |
| `get_task(task_id)` | 单任务 |
| `complete(task_id, actor, *, comment, variables, idempotency_key)` | 同意提交（幂等） |
| `reject(task_id, actor, *, comment, target=None)` | 驳回回退（显式目标 > reject_target > 来源节点） |
| `claim(task_id, user)` / `unclaim(task_id, user)` | 候选任务认领/取消认领 |
| `transfer(task_id, to_user, actor, comment="")` | 转办 |
| `assist(task_id, users, actor)` / `complete_assist(task_id, actor)` | 协办（不推进流程） |
| `list_cc_tasks(*, recipient=None, instance_id=None)` / `mark_cc_read(cc_id, user)` | 传阅 |
| `add_countersigner(task_id, users, actor)` | 加签（item_count 联动） |
| `withdraw(task_id, actor)` | 撤回已办（取消下游、原人重办） |
| `activate_node(instance_id, activity_code, actor, participants=None)` / `cancel_node(...)` | 管理员激活/取消节点 |
| `finish_instance(instance_id, actor)` / `remove_instance(instance_id, actor)` | 强制完成 / 删除实例 |
| `anticipate(task_id, outcome="agree") -> {node_exit, instance_complete}` | 只读预判「照此提交后节点是否出、实例是否完」（不落库；主流引擎无等价 API） |

`complete/reject` 返回 `TaskCompleteResult{task_id, outcome, instance_state, next_tasks}`（行动提示式）。

## HistoryService（只读查询，`engine.history`）

| 方法 | 说明 |
|---|---|
| `events(instance_id=None, limit=200)` | 事件 outbox |
| `task_history(instance_id=None, assignee=None, limit=200)` | 已办（append-only） |
| `paged_tasks(*, assignee, instance_id, offset, limit) -> Page` | 已办分页 |
| `instances(*, state=None, definition_code=None, limit=100)` | 实例列表 |
| `tokens(instance_id)` | 令牌轨迹 |
| `route_decisions(instance_id)` | 路由决策审计（F3）：逐条件边 result、selected 激活连线、求值快照 |
| `trace_instance(instance_id) -> list[TraceEntry]` | 令牌+已办+事件合并时间线（审批轨迹页） |
| `archive_instance(instance_id) -> dict` | 完结实例归档导出（JSON） |

## Messages（等待与唤醒，`engine.messages`）

| 方法 | 说明 |
|---|---|
| `publish_message(name, correlation_key="", payload=None, *, message_id=None, ttl_seconds=...) -> bool` | 发布消息：命中 open 订阅立即关联（True）；否则入缓冲（False）；message_id 幂等防重。等待节点（wait kind）命中即推进（按节点 output_mappings 写载荷，缺省全量合并）；暂停实例的订阅不消费 |
| `correlate_message(name, correlation_key="", payload=None, *, message_id=None) -> dict \| None` | **同步关联（不缓冲，spec §7.2）**：命中回传签收凭证 `{matched, duplicate, instance_id, node_id, message_name, business_key, definition_code}` 并推进；无人在等返回 None（消息不留存）；重复 message_id 幂等回放首次结果（duplicate=True） |
| `subscribe(instance_id, token_id, node_id, name, correlation_key="") -> bool` | 登记订阅；命中缓冲未过期消息立即关联（先到竞态） |
| `signal(name) -> int` | 广播信号（唤醒全部同名订阅） |
| `waiting(name=None, correlation_key=None) -> list[dict]` | 等待实例反查（open 订阅）：实例/节点/消息名/关联键/流程/业务键/等待起时——运维面；CLI `flowork message waiting` |
| `close_subscriptions(instance_id, *, node_id=None) -> int` | 关闭订阅（节点离开等待态） |

等待节点（`kind="wait"`，spec §7.2）：`message`（{name, correlation_key?}，进入时求值快照）/ `output_mappings`（载荷→变量，缺省全量合并）/ `timeout`（{after_minutes}）三模式合一；结果词汇 `wait.result`（matched|elapsed）仅出边条件；到时发 `timer_fired`；周期触发不做（调度域）。

## Jobs（超时与外部执行）

`engine.jobs.tick()` 推进到期 Job（测试/外部定时器）；`start(interval_seconds)/stop()` APScheduler 后台轮询；`requeue(job_id)` 人工重派 dead-letter Job。
超时配置在节点上（v2.2 列表形态，同一节点最多 8 条）：`deadlines=[TaskDeadline(after_minutes=60, action="notify"|"reassign"|"escalate"|"auto_approve"|"auto_reject", reassign_to=..., escalate_to=...)]`。`reassign_to` 与 `escalate_to` 都是 **CEL 表达式**（fire 时求值，可用 `task.assignee`），且**都必填**——引擎不含「上级」概念（spec §5.5），科层组织写法 `escalate_to="manager_of(task.assignee)"`；escalate 额外记 `task_escalated` 事件。缺目标＝设计期 ERROR（不再拖到触发时出故障单）。

Worker 面（service_task mode=worker，`engine.worker_jobs`）：

| 方法 | 说明 |
|---|---|
| `create_worker_job(*, job_type, instance_id, payload=None, max_retries=3) -> str` | 引擎侧落外部执行 Job |
| `activate_jobs(job_types, *, worker_id, max=10, visibility_timeout_seconds=300) -> list[JobClaim]` | 批量领活（租约制：领到即租住，到期未交货可被重领） |
| `complete_job(job_id, *, worker_id, output)` / `fail_job(job_id, *, worker_id, error, retry_delay_seconds=...)` | 交货 / 失败（易主交货抛 JobStateError） |

## 扩展点

- **自定义节点**：`FlowNode` 子类（Spec）+ `NodeBehavior` 子类 + `config.node_registry.register(kind, spec, behavior)`。
- **声明式钩子**：节点 `hooks={"on_exit": {"handler": "name", "params": {...}}}` + `engine.register_handler("name", fn)`。
- **表达式函数（全位点 CEL 词汇）**：经 `ExpressionFunctions` 端口注册（必须带 FunctionSignature 签名，纯读闭包无 ctx）；一行挂官方词汇：`config.expression_functions = contrib.cel_vocabulary(org)`（manager_of/users_with_role/has_role——`manager_of` 对 AI 代理的 id 返回其负责人 owner）。取人/传阅/改派+升级/连线条件/子流程映射共用同一注册面。
- **判断面事实**：公式可用 `now`/`prev_task`/`node("code")`（引擎注入，词汇目录见 spec §4.3/C.4，权威在 `expr/catalog.py`）。
- **事件消费**：消费 `wf_event_outbox`（CloudEvents 信封，按 id 幂等；`flowork.events.consumer.IdempotentSink` 为参考实现）。
