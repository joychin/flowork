# REST /v2 与 MCP 插件设计（开放面第一刀）

> 状态：2026-10-12 定稿并随批实施（本文件即实施依据）。
> 范围权威：kernel-v2 spec §12（REST）、§13（MCP 工具，2026-10-13 收尾批起 19 个）。本设计只做「插件位怎么落、
> 契约怎么冻、缺口怎么补」的工程裁定，不改语义。

## 0. 定位与既定裁定

- 2026-10-09 用户裁定：REST/MCP = 插件层，内核收口优先。本设计即开放面开工。
- **spec §15 模块布局中的顶层 `mcp/` 位被本设计取代**——MCP 与 REST 一并进驻
  `flowork/contrib/`（与 web 同列）；§12 标题「web 包」同步修正为 contrib/rest。
- 三面关系：`contrib/web /api` = 设计器内部面（已冻结，C3）；`contrib/rest /v2` =
  **对外集成面**（本批冻结）；`contrib/mcp` = AI 工具面（本批冻结清单）。三者互不复用
  路由/工具代码，只共用 `flowork.api` 门面。

## 1. 目录结构（每文件 ≤300 行，行数红线测试自动辖至 contrib）

```
flowork/contrib/rest/
  __init__.py              create_app / register_rest_routes 再导出 + REQUIRES_FLOWORK_API
  app.py                   引擎装配、错误处理器、路由注册、调度开关（FLOWORK_JOBS 同 web 语义）
  _common.py               actor 头、游标分页、错误状态映射、payload 序列化
  routes_definitions.py    definitions 面四端点
  routes_instances.py      instances 面（发起/搜索/动作/signal/变量）
  routes_tasks.py          tasks 面七动作 + search + anticipate
  routes_system.py         messages/jobs/incidents/events
flowork/contrib/mcp/
  __init__.py
  catalog.py               ToolSpec + 19 工具装配 + dispatch（纯同步，可脱离传输测试）
  tools_definitions.py     validate_spec / dry_run / deploy / definition_drift / list_definitions
  tools_runtime.py         start/query/signal/pause/resume_instance + update_instance_variables
                           + publish_message + list_incidents + resolve_incident
  tools_tasks.py           list_my_tasks / claim / complete / reject / anticipate_task
  server.py                mcp lowlevel Server 传输壳 + stdio 入口（python -m flowork.contrib.mcp.server）
```

## 2. REST /v2 面

### 2.1 路由表（spec §12 全量映射 + 一处补缺）

| 端点 | 内核调用 |
|---|---|
| `POST /v2/definitions/search` | `repository.list_definitions(code_prefix, latest_only)` |
| `POST /v2/definitions/deploy` | `load_definition` → `repository.deploy(published_by, target_major, force_major_reason, allow_minor_reason)` |
| `GET /v2/definitions/{code}?major=&revision=` | `repository.get_definition_json` |
| `POST /v2/definitions/{code}/retire` | `repository.retire(actor, reason)` |
| `POST /v2/instances` | `runtime.start_process`（**补 §12 缺口**：spec 只列动作没列发起——对外集成面没有发起端点不可用；显式扩面，见 §2.1 尾注） |
| `POST /v2/instances/search` | `history.instances(state, definition_code)` |
| `POST /v2/instances/{id}/{pause\|resume\|cancel\|terminate}` | `runtime.*` |
| `POST /v2/instances/{id}/signal` | 见 §2.4（关联回执核验式映射） |
| `GET/PATCH /v2/instances/{id}/variables`、`GET .../variables/history` | runtime 变量面（三门槛语义在内核，REST 只透传） |
| `POST /v2/tasks/search` | `task.list_tasks(state, assignee, candidate, instance_id)`——state 词表 `ready\|null`（终态行即删入历史表，`completed/cancelled` 曾是恒空死档，已办走 history/search；2026-10-24 对打修复批） |
| `POST /v2/tasks/history/search` | `history.task_history(instance_id, assignee)`（已办查询，同上批补齐） |
| `GET /v2/instances/{id}` | `runtime.get_instance`（实例详情，同上批——对齐 §11，此前 404） |
| `POST /v2/tasks/{id}/{claim\|unclaim\|complete\|reject\|transfer\|assist\|complete_assist\|consult\|complete_consult\|add_countersigner\|withdraw}` | `task.*`（后四动作 2026-10-24 补齐三面同构欠账） |
| `POST /v2/tasks/{id}/anticipate` | `task.anticipate(outcome)` |
| `POST /v2/messages/publish` | `messages.publish_message` |
| `POST /v2/jobs/activate`（长轮询 request_timeout） | `worker_jobs.activate_jobs` |
| `POST /v2/jobs/{id}/{complete\|fail}` | `worker_jobs.complete_job / fail_job` |
| `POST /v2/{jobs\|incidents\|events}/search` | `jobs.list_jobs` / `runtime.list_incidents` / `history.events` |

purge 不出 REST（spec 明文，仅 CLI）。另含 `GET /v2/health`（探活，spec 外的
运维便利端点，快照清单含它）。

**§2.1 补缺尾注**：`POST /v2/instances` 是对 spec §12 的显式增补（清单快照含它），
理由如上；spec §12 后续修订应把它收编进正文。

### 2.2 分页（游标）

内核查询面为 offset 式（spec §11 明示）。游标 = base64(`"o:{offset}"`)；请求
`page.after` 解码为 offset，`page.before` 一律 422（明示不支持，不假装游标语义）；
响应 `next_cursor` 仅当本页取满才给。

### 2.3 错误体与状态映射

统一 `{"error": {"code", "message", "details"}}`，`code` = 异常类名（稳定标识）。
映射：isinstance 链 `NotFoundError→404`、`TaskNotAvailableError / DuplicateStartError /
JobStateError→409`、`DefinitionError→422`（`InvalidDefinition` 的 issues 进 details）、
`ConditionError→422`；类名兜底表 `LockConflictError→409`、
`ParticipantCountMismatchError / ParticipantLimitError / FormulaError→422`；其余
`FloworkError→500`。按名兜底是因为这些错误家族未在 `flowork.api` 导出——**不为错误
映射扩公开面**（§4 天花板原则）；宿主要类型化捕获时再走扩面动作。

### 2.4 actor 归因与实例级 signal

- actor：`X-Flowork-Actor` 头（缺省 `rest`），body `actor` 可覆盖；**鉴权不在范围**
  （engine-v1 02：权限归宿主，既定裁定）。**安全警示（2026-10-14 review 批）**：
  body.actor 优先于头——网关若以头为权威身份注入，必须同时剥离请求体里的
  `actor` 键，否则客户端可伪造归因。
- signal（实例级唤醒）：内核原语是名字域广播/关联，无实例域 signal——映射为「按本实例
  open 订阅的 (name, correlation_key) 做同步关联（`correlate_message`），回执
  `instance_id` 必须等于目标实例，不等或无回执 409；无订阅 404」。body 可显式给
  correlation_key / payload / message_id。**串单口径修正（2026-10-14 review 批）**：
  「回执核验防串单」原文不准——核验只防误报成功，防不住副作用（修前消息被全局
  FCFS 先到的他人实例消费并推进，signal 才报 409）。修后 `correlate_message` 增
  `instance_id` 实例定向：signal 面只消费本实例订阅，副作用不可能落错单；回执
  核验保留为并发兜底（message_id 已被他人消费的幂等回放仍 409）。MCP
  `signal_instance` 同此语义。

### 2.5 过滤维度现状（诚实清单）

| 面 | 内核直支 | 后置（待内核扩参，登记不排期） |
|---|---|---|
| tasks | state × assignee × candidate × instance_id | flow_code / priority / due_before（wf_task 列已存在，list_tasks 扩参小刀） |
| instances | state × definition_code | business_key |
| definitions | code_prefix × latest_only | status |
| jobs | state × job_type × instance_id（list_jobs 本批新增） | — |
| incidents / messages / events | instance_id / name×correlation_key / instance_id | — |

### 2.6 装配两形态

- **独立进程**：`create_app()`——`FLOWORK_DB` / `FLOWORK_ORG` / `FLOWORK_JOBS` /
  `FLOWORK_JOB_INTERVAL` 语义与 web 一致（启动建表、内置调度可关）。
- **挂载**：`register_rest_routes(app, engine)`——宿主自带引擎与鉴权中间件，把 /v2
  路由挂进宿主 FastAPI（引擎多路复用，不重复建调度）。

## 3. MCP 面

- **分层**：`catalog.py` 纯 dispatch（19 工具注册与执行，同步函数，可脱离 stdio 直接
  测试）+ `server.py` 传输壳（mcp SDK lowlevel Server：`list_tools` 回传目录、
  `call_tool` 转 dispatch 结果为 TextContent；`python -m flowork.contrib.mcp.server`
  走 stdio）。传输换壳不动工具层。
- **19 工具映射**（§13 → 内核）：validate_spec→validate_definition；dry_run→
  simulate_route（**完整模拟 simulate 不在清单内，后置**）；deploy→repository.deploy；
  definition_drift→repository.definition_drift（入参目录路径）；list_definitions→
  repository.list_definitions；start_instance→runtime.start_process；query_instance→
  get_instance + variables_of；signal_instance→§2.4 语义；publish_message→
  messages.publish_message；update_instance_variables→runtime.update_variables；
  list_my_tasks→task.list_tasks（assignee 必填，candidate 可选）；claim/complete/
  reject_task→task.*；anticipate_task→task.anticipate；pause_instance→
  runtime.pause_process；resume_instance→runtime.resume_process（2026-10-13 收尾批，
  拍板④对称性）；list_incidents→runtime.list_incidents；resolve_incident→
  runtime.resolve_incident。
- **DTO**：出参 pydantic 模型 `model_dump(mode="json")`；dataclass（VersionVerdict/
  JobClaim 等）手工转 dict；写工具 `actor` 必填入参（裸 id，AI 与人同路）。
- 入参 schema 手写 JSON Schema（type/properties/required），与快照测试同源。

## 4. 公开面扩展点与兼容声明

- **天花板原则**：插件能做什么 = `flowork.api` 有什么（纪律测试强制只准 import 门面）。
  新能力需求 = 先扩公开面（快照红=显式动作）再消费。
- 本批唯一扩面：`flowork.api.API_VERSION = "1"`（str 常量）。语义：增符号 = minor
  （快照更新显式动作）；删/改名 = major 破坏性（api docstring 已立法）。
- **插件兼容声明**：各插件包暴露 `REQUIRES_FLOWORK_API`，测试断言其 ≤ 当前
  `API_VERSION`（插件不可运行于更老的内核门面）。
- backlog（对应能力面落地时先扩公开面，本批不动）：
  `DirectoryStore`（目录管理 REST/MCP 面前置）、`IdempotentSink`/`OutboxConsumer`
  （事件推送面前置）。
- import-linter 契约二 forbidden 从 `["flowork.cli", "flowork.contrib.web"]` 扩为
  `["flowork.cli", "flowork.contrib"]`——api 不得 import **任何**插件（新插件进驻即受辖，
  不再逐个点名）。

## 5. 内核改动清单（本批唯一）

`JobService.list_jobs`（只读查询：state/job_type/instance_id 过滤、offset 分页，
返回 dict 列表）——`/v2/jobs/search` 的数据源；只读不触碰实例行，不进 CommandContext
写路径。

## 6. 明确不做

鉴权/RBAC（宿主职责）｜`before` 游标｜tasks 三过滤维（待内核扩参）｜目录管理面｜
事件流式推送｜purge REST（spec 明文）｜完整模拟 MCP 工具（18 清单外）。

## 7. 验收

快照三件：`tests/test_rest_api_surface.py`（/v2 路由表冻结）、
`tests/test_mcp_tool_surface.py`（19 工具名冻结）、公开面快照增 `API_VERSION`。
行为两件：`tests/test_rest_v2_api.py`（部署→发起→办理→变量→signal→jobs 全链）、
`tests/test_mcp_tools.py`（dispatch 逐工具）。五道门全绿。
