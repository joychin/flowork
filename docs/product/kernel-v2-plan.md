# flowork 内核 v2 规划：内核内容与对外暴露面

> 2026-10-07 定稿讨论稿。依据：①对 Temporal / Zeebe(Camunda 8) / Camunda 7(Flowable) / LittleHorse 四引擎官方文档的内核概念与 API 调研；②flowork M0–M6 现有代码盘点；③已定架构决议（内核只管流转+真相、插件管业务形状、AI 站在注册表前、CEL/JSON Schema/SCIM 标准选型、快照原则）。

## 0. 一句话结论

内核 = **定义/实例/任务三层实体 + 等待唤醒原语 + 错误二分 + 治理五原语**；对外暴露 = **6 个 SPI 端口 + 一组 CloudEvents 事件 + 四原语 Python API + C8 风格 REST + MCP 工具面**。库形态的独有优势是**原生事务性 outbox**（状态变更与事件同事务）——四家主流引擎都做不到，我们要做成本命差异。

## 1. 设计原则（已定，不再论证）

1. 内核只管「流转 + 真相」：状态机、等待唤醒、存储 SPI、事件流、历史审计、版本化。审计永不出内核。
2. 业务形状全是插件（表单/权限/通知/designer），内置与第三方走同一机制（dogfood 铁律）。
3. 内核管语言（变量 schema、CEL 表达式、主体符号），插件管词典（解析实现）。
4. 快照原则：分支依赖的数据必须在边界时刻进入变量，网关永不实时查业务库。
5. 语法零自造：变量=JSON Schema 2020-12（pydantic 直出）；表达式=CEL（cel-python，待验证函数注册 API）；YAML 形态借 Open Workflow Spec 1.0.x；主体形状对齐 SCIM 2.0。

## 2. 内核概念清单

标注：✓ 已有 / ◐ 需强化 / ✗ 缺失（括号内为现有模块）。

### 2.1 定义侧（不可变蓝图）

| # | 概念 | 业界对标 | 现状 | 动作 |
|---|---|---|---|---|
| D1 | FlowSpec 不可变 + 版本化部署 | 四引擎一致；LH `major+revision`，存量实例钉启动版本 | ◐（definitions/definition+builder 有，无版本号语义） | 版本三元组 name+major+revision；**破坏性自动判定抄 LH：必填变量集合或 searchable 变量集合变化→major+1**（对表单字段演进关键） |
| D2 | 变量契约：input/output schema | LH typed vars（required/searchable 修饰符）；Zeebe JSON 值 | ◐（实例 JSON 列存变量，无 schema 边界） | input=JSON Schema；`.required()`/`.searchable()` 修饰符；提交/节点输出在边界处按 schema 校验拒收 |
| D3 | 表达式求值 | CEL（K8s/Envoy 事实标准，静态类型可设计时查错） | ◐（expr/conditions+formula 自研） | M7 前置：cel-python 接入 + 设计时 type-check 进 validator；备胎 C++/Rust 绑定 |
| D4 | 资源引用注册表 | Zeebe deployment 含 form/decision 资源 | ✗ | 插件注册资源类型（`form:`/`policy:`），内核校验引用存在不解语义 |
| D5 | dry-run / semantic_diff | Temporal 无此物（我们的 AI 护栏差异化） | ✓（definitions/dry_run+semantic_diff） | 保持，CI/MCP 都要用 |

### 2.2 实例侧

| # | 概念 | 业界对标 | 现状 | 动作 |
|---|---|---|---|---|
| I1 | 实例 + 启动幂等 | Temporal WorkflowId+ReusePolicy(4档)+ConflictPolicy 是最完整设计 | ◐（可启动，幂等未知） | `idempotency_key` + 复用策略（reject / use_existing / new_run） |
| I2 | Token/NodeRun 执行层级 + 子流程 + 会签 | C7 Execution 树；LH WfRun→ThreadRun→NodeRun | ✓（engine/kernel+branch+subprocess_support+cc_support/countersign） | 保持 |
| I3 | 变量快照 + 作用域树 | Zeebe 作用域树+同名遮蔽；LH 250KB 上限 | ◐ | 快照原则成文；子流程局部作用域；单实例 payload 上限（先定 1MB） |
| I4 | HumanTask 状态机 | C7 claim/delegate/resolve/complete；LH UNASSIGNED(组)→ASSIGNED(人)→DONE/CANCELLED | ◐（task_actions 有动作集） | 定型状态机：`created→(claimed|assigned)→completed|rejected|cancelled`，加 `delegated→resolved` 委派回路 + 转办/退回 |
| I5 | ServiceTask(Job) 租约模型 | Zeebe ActivateJobs 长轮询+可见性超时+FailJob(retries,backoff)→incident | ◐（jobs/ 有 CAS+dead letter） | 补：批量领取+租约超时重派（at-least-once，文档写明 worker 需幂等） |
| I6 | 等待原语三件套 | ①timer ②消息点对点（Zeebe name+correlationKey+TTL+messageId 幂等）③信号广播（C7 signal=ALL 等待者） | ◐（timer 有） | ✗ 消息相关性是最大缺口：correlationKey 订阅匹配 + TTL 缓冲（解决"消息先到"竞态）+ messageId 去重 + 消息启动实例 |
| I7 | 错误二分 | Temporal ApplicationError/ActivityFailure；LH EXCEPTION/ERROR；C7 BpmnError/死信 | ◐（incident_raised+job_dead_lettered 事件有） | 业务异常（可被流程边界捕获，如驳回）vs 技术故障（incident+人工解）；重试策略参数对齐 Temporal 默认（1s 起、退避×2、上限 100×、可设上限次数、non_retryable 类型表） |

### 2.3 治理五原语

| # | 原语 | 业界对标 | 现状 | 动作 |
|---|---|---|---|---|
| G1 | pause / resume | Temporal v1.30+（不派新任务、信号延后、取消待恢复后生效） | ✗ | 实例级暂停 |
| G2 | cancel（协作式）/ terminate（强杀） | Temporal 二档设计 | ◐ | 补 terminate |
| G3 | token 手术（modification） | Zeebe activate/terminate/move，事务式全有或全无，定位为例外修复 | ✗ | 后置（P2），先留 API 位 |
| G4 | 版本迁移（migration） | Zeebe mapping instructions（元素ID映射、不重估表达式） | ✗ | 后置（P2）：默认策略=存量钉版本+新实例跑最新（LH 语义） |
| G5 | incident 模型 | Zeebe：修复根因→resolve→继续；根因未除会复发 | ◐ | incident 实体+解决动作+复发语义 |

## 3. 对外暴露面

### 3.1 SPI 端口（内核只依赖 Protocol；现有 spi/ports.py）

| 端口 | 现状 | 说明 |
|---|---|---|
| Clock | ✓ | 确定性测试 |
| WorkCalendar | ✓ | 工时换算（审批 SLA 用） |
| OrgDirectory | ◐ 扩展 | 现有 manager_of/users_with_role；对齐 SCIM 形状（User/Group/manager 指针），加 roles、delegation |
| Notifier | 已删（2026-10-09 死端口收口） | notify 实际直接 record_event 落 outbox，事件即通知面，零消费删除 |
| **ExpressionFunctions**（新增） | ✗ | CEL 自定义函数注册：`manager_of()`/`role()` 等走这里→转 OrgDirectory。这是「语言与词典」的缝 |
| **ResourceProvider**（新增） | 已删（2026-10-09 死端口收口） | M-B 预声明零消费；落地时按实际形状重声明 |
| **EventSink / Exporter**（新增） | ✗ | 事件外发（CloudEvents 信封）；默认=outbox 轮询器 |
| Storage | 评估 | persistence/ 已有实现，评估是否升格 Protocol（多副本 PG 路线的需要） |

### 3.2 领域事件（对外发布）

现有 14 个（events/domain.py）：process_started/completed/cancelled、node_entered/exited/cancelled/failed、task_created/completed/rejected/claimed/transferred、incident_raised、job_dead_lettered。

增补（治理/消息/委派）：

| 新事件 | 触发 |
|---|---|
| process_paused / process_resumed | G1 |
| process_migrated | G4 |
| task_delegated / task_resolved | 委派回路 |
| task_escalated | 超时升级（WorkCalendar 配合） |
| task_returned | 退回上一步 |
| message_received / message_correlated | I6 消息到达/关联成功 |
| timer_fired | 定时器触发 |
| definition_deployed | D1 新版本部署 |
| incident_resolved | G5 |

**信封**：CloudEvents 1.0.2（CNCF 毕业级，Python SDK 现成）——`type`（沿用现有事件名）、`source`（engine 实例标识）、`id`（确定性：instance_id+seq）、`time`、`data`；扩展属性带 instance_id/flow/subject。**交付语义**：outbox 与状态变更同事务写入（库形态独有优势，四家引擎都无原生 outbox）→ at-least-once 分发 → 消费端幂等（文档写明）。变量级变化不发光（过程性最小集，防事件风暴）。

### 3.3 Python API（进程内，一等公民）

四原语对齐 Temporal 语义：`start()`（幂等，≈SignalWithStart）、`signal()`（异步写）、`query()`（同步只读）、`update()`（同步写+可先校验）。加：`deploy/complete_task/claim/delegate/transfer/reject/send_message/pause/resume/cancel/terminate/resolve_incident`。

### 3.4 HTTP API（web 包，抄 Camunda 8 v2 风格）

- 搜索：`POST /v2/{tasks|instances|definitions|incidents|messages|events}/search`，**keyset 分页**（`page.after/before + sort + limit`，非 offset）。
- 任务过滤维度（抄 C8 Tasklist+LH）：`state × assignee × candidate_group × flow_id × priority × due_before`。
- 动作端点：`POST /v2/tasks/{id}/{claim|complete|reject|transfer|delegate|resolve}`。
- 任务领取（给外部 worker/AI agent）：`POST /v2/jobs/activate`——长轮询（request_timeout）+ 批量 + 可见性超时（租约）；`POST /v2/jobs/{id}/fail`（带 retries+backoff）。语义抄 Zeebe，HTTP 实现。

### 3.5 MCP 工具面（M7 落地，工具=注册表自动映射）

首批工具清单：`validate_spec / deploy / list_definitions / start_instance / query_instance / signal / complete_task / claim_task / list_my_tasks / send_message / pause_resume_cancel / list_incidents / resolve_incident / dry_run / diff`。插件后续自动贡献（forms 插件→`render_form_spec/submit`）。

### 3.6 Webhook 出站（宿主回调，抄 Inngest/trigger.dev）

HMAC(raw body)+时间戳防重放、指数退避、重试上限、耗尽入终态死信。订阅模型抄 Prefect Automations（状态变更触发器→webhook），挂在 EventSink 端口上，默认关闭。

### 3.7 可观测

OTel span 约定：instance=trace、node=span（对齐 Temporal 的 OTLP 支持）；事件本身就是审计流。P2。

## 4. 差距清单与排序

**P0（M7 前置，动工前必须定型）**
1. cel-python 接入 PoC：函数注册 API（`manager_of`）、性能、类型检查能力 → 定/换绑定
2. 事件信封切 CloudEvents + outbox 消费器（Notifier 改造）
3. SPI 端口增补定稿（ExpressionFunctions/ResourceProvider/EventSink 签名）——插件 API 的地基
4. 启动幂等（idempotency_key + 复用策略）
5. 版本三元组 + LH 式破坏性自动判定（接进现有 semantic_diff）

**P1（M7 内/M8 前完成）**
6. 消息相关性：correlationKey+TTL+messageId（I6，最大语义缺口）
7. HumanTask 状态机定型（I4：委派/退回/升级 + 事件增补）
8. Job 长轮询领取+租约（I5）
9. pause/resume/terminate（G1/G2）
10. 错误二分成文 + 重试策略参数（I7）

**P2（后置）**
11. token 手术、版本迁移（G3/G4）
12. webhook 出站、OTel、searchable 变量索引
13. Storage 端口升格（与多副本 PG 路线合并考虑）

## 5. 与里程碑的映射

M7（MCP+AGENTS.md）吃 P0 全部 + MCP 工具面；M8（表单）依赖 D2/D4（资源注册表+schema 边界）；M9（权限）挂 task 动作生命周期钩子。多副本欠账（PG 实测/Alembic/outbox 消费）与 P2-13 合并排期。

## 6. 主要来源

Temporal（workflows/event-history/workflow-message-passing/failures/retry-policies/worker-versioning/workflow-pause）、Camunda 8（glossary/variables/messages/incidents/process-instance-modification/migration/job-workers/Orchestration REST v2/Tasklist）、Flowable（ch04-API/ch06-Deployment/ch17-Advanced）、LittleHorse（workflows/wfspec-versioning/variables/interrupts/exception-handling/external-events/correlated-events/user-tasks）、CloudEvents 1.0.2、Inngest SDK_SPEC、Prefect Automations。检索核实日期 2026-10-07。
