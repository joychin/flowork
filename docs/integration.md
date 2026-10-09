# flowork 集成与部署指南

## 安装与运行

```bash
uv sync                                   # Python >= 3.11
uv run pytest                             # 全量测试
uv run flowork init --db flowork.db       # 初始化 SQLite 库
uv run flowork deploy <template.json>     # 部署定义（版本判定/幂等去重/破坏性清单）
uv run flowork validate examples/01_simple_approval.json
uv run flowork demo-full                  # 全流程 DoD 演示（事件回放）
uv run flowork retire <code>              # 定义退役（purge 物理清除 / drift 文件对账）
uv run flowork instance vars <iid>        # 实例变量面（vars-set 写 / var-history 血缘）
```

嵌入使用：`pip install -e .` 后 `from flowork.engine.engine import ProcessEngine`。

## 表结构（SQLite，可平迁 PostgreSQL）

内核执行面（`flowork/persistence/orm.py`）：

| 表 | 内容 |
|---|---|
| wf_process_definition | 定义（code+major+revision、content_hash、发布后不可变） |
| wf_process_instance | 实例（definition_snapshot 快照；version 乐观锁；deadline_at/actor_context） |
| wf_token | 令牌（三计数：item/approve/disapprove；reject_flag） |
| wf_task / wf_task_history | 待办（活表）/ 已办（append-only，驳回复活不删不改历史） |
| wf_cc_task | 传阅件（含已读） |
| wf_identity_link | 候选关联（claim） |
| wf_event_outbox | 领域事件（消息/UI/MCP 统一消费入口，CloudEvents 信封） |
| wf_job / wf_incident | 超时与外部执行任务（重试/死信/租约）/ 流程异常 |

v2 扩展面（`flowork/persistence/orm_v2.py`）：

| 表 | 内容 |
|---|---|
| wf_designer_draft | 设计器草稿（独立于发布版） |
| wf_message_buffer / wf_message_subscription | 消息缓冲（TTL）/ 等待节点订阅 |
| wf_variable_index | 变量检索（searchable 值索引） |
| wf_variable_history | 变量血缘（全写方留痕：改前/改后+归因） |
| wf_route_decision | 路由决策审计（逐条件边 result、selected、求值快照） |

目录表（M-A，`flowork/directory/orm.py`）：org_unit / org_user / org_role /
org_role_assignment / org_agent / org_agent_credential / org_delegation（审批委托，
窗口式单跳替换）/ org_directory_audit（目录写审计，追加只写：action/entity/
detail/acted_by）。目录读面带进程内缓存（写操作全清失效）；admins＝org_role
的 admin 档持有人（to_admins 兜底名单同源）。

## 库迁移（Alembic）

基线迁移与引擎启动建表（`create_schema` 的 `create_all`）同源同构——两条路
殊途同归；增量变更走 Alembic（spec §14 尾注）：

```bash
FLOWORK_DB=/path/prod.db uv run alembic upgrade head    # 空库/增量升级（建全表）
FLOWORK_DB=/path/legacy.db uv run alembic stamp head     # 既有 create_all 库对齐版本标记（无 DDL）
FLOWORK_DB=/path/prod.db uv run alembic revision -m "..."  # 新增量迁移（autogenerate 对照共享元数据）
```

连接串解析：alembic 配置显式指定（测试注入）> `FLOWORK_DB`（与 web 同名
变量，路径）> 缺省 `flowork.db`。引擎启动建表保持幂等 create_all（开发/
测试零配置），生产升级走 alembic；两者并存不冲突（同一共享元数据）。

## 超时调度

生产环境三种形态任选：

1. **web 进程内置调度（默认）**：`uvicorn flowork.web.app:app` 启动时自动开启
   后台轮询（默认 30s，`FLOWORK_JOB_INTERVAL` 可调；`FLOWORK_JOBS=off` 关闭）。
   多副本部署安全：tick 内逐 Job CAS 认领（条件 UPDATE），同一 Job 只会被
   一个副本执行。
2. **独立 tick worker**：cron/systemd 周期调用 `flowork tick --db flowork.db`，
   与 web 进程解耦（同一认领机制保证不重复）。
3. **嵌入式**：`engine.jobs.start(interval_seconds=30)`（APScheduler），随进程
   `engine.jobs.stop()`。

测试/脚本环境直接 `engine.jobs.tick()`（时钟经 `EngineConfig.now` 注入，确定性）。
认领、策略执行与结果落库在同一事务：进程崩溃整体回滚（Job 回到 scheduled），
不存在滞留 running 的中间态；失败按 30s 起步指数退避重试，超限进 dead-letter。

## 并发模型

一句话声明：**同实例串行化靠实例行乐观锁；跨实例天然并行；Job 恰一次靠 CAS 认领。**
部署多副本（多个 web 进程 / 独立 tick worker / 多外部 worker）无需额外分布式锁组件。

- **同实例串行化**：每个写命令单事务执行，且统一触碰实例行（`CommandContext.touch`
  → `version` 乐观锁）。同实例的两个并发命令，后提交者在版本冲突时自动重试
  `EngineConfig.lock_retries`（默认 3）次，仍冲突则抛 `LockConflictError` 明确拒绝
  ——**绝不静默覆盖**。汇聚可达性判定与路由决策在同一事务内完成（读的是本事务
  一致性视图），不存在计数错位的窗口。
- **跨实例并行**：不同实例互不触碰对方行，天然并行；部署新版本经
  `definition_snapshot` 快照钉版，在途实例与并发部署零交互。
- **Job 恰一次**：内部调度 tick 逐 Job CAS 认领（条件 UPDATE 原子抢占），多副本
  同时 tick 同一 Job 只有一个副本执行；外部 worker 走租约协议
  （`activate_jobs` / `complete_job` / `fail_job`，同样是 CAS 认领 + 租约易主）。
- **启动幂等**：同 `idempotency_key` 并发到达，按 `reuse` 策略
  （reject / return_existing / allow_duplicate）收敛，数据库唯一索引兜底。

### 竞态测试矩阵（上述声明的证据索引）

| 竞态窗口 | 机制 | 证据测试 |
|---|---|---|
| 同任务并发 complete×2（单引擎/多副本） | 活表状态守卫 + 乐观锁 | `tests/edge/test_action_races.py` |
| complete vs reject | 活表状态守卫（后到者 `TaskNotAvailableError`） | 同上（真线程 + 双确定性交错） |
| 人工办理 vs 超时自动通过 | Job 执行时复查任务状态（skip） | 同上（恰一结局，不重试不死信） |
| 暂停 vs 推进 | 实例状态门（暂停拦截办理） | 同上（互斥生效，恢复后放行） |
| 多 worker 抢同一批 Job | 租约 CAS 认领互斥 | 同上（易主汇报 `JobStateError`） |
| 多副本并发 tick 同一 Job | CAS 认领 | `tests/edge/test_hardening.py` |
| 同 key 并发启动 | 幂等三策略 + 唯一索引 | `tests/semantics/test_start_idempotency.py` |
| 旧快照并发写（丢失更新） | 乐观锁 `StaleDataError` | `tests/edge/test_hardening.py` |

> SQLite 默认部署（WAL）下单写者进一步天然串行；生产多副本换 PostgreSQL 时，
> 上述机制不变，仅需关注 `touch` 冲突在所选隔离级别下的重试表现
> （Alembic 基线迁移随 /v2 批落地）。

## 与后续系统对接（二期入口已预留）

- **HTTP 服务**：`flowork/web` 已上线设计器面（定义草稿/校验/发布/试算，前缀 `/api`）；
  实例/任务/搜索 REST 面随 kernel-v2 §12（`/v2`）落地，四服务方法即路由一一映射。
- **消息推送**：订阅 `wf_event_outbox`（CloudEvents 信封，按 id 幂等；
  `flowork.events.consumer.IdempotentSink` 为参考实现；`EngineConfig.notifier` 端口保留给宿主定制）。
- **AI（MCP）**：MCP 工具面随 M7 落地（18 个工具，清单见 kernel-v2 spec §13）；
  当期已落实：全 typed、双语 docstring、幂等键、行动提示式返回、actor 区分人/agent。
- **UI 设计器**：`designer/`（React Flow）已可用——Spec 唯一事实源、schema 驱动表单、
  校验闭环；任务页/Console 随产品 v1 M-C。
- **组织系统**：替换 `EngineConfig.org`（实现 `OrgDirectory` 同形接口或适配现有库；
  持久化目录 `flowork.directory.DirectoryStore` 已起步，M-A）。

## 运维要点

- 实例异常自动挂起（`suspended`）并落 `wf_incident`；修复数据后 `resolve_incident`
  五动作择一：`resume` 恢复 / `cancel` 取消 / `retry` 重派该实例 dead-letter Job /
  `skip` 跳过故障节点推进 / `fail` 判失败。
- 死信 Job 人工重派：`engine.jobs.requeue(job_id)`（重置 scheduled、清零重试计数）。
- 死循环防护：单节点完成次数上限 `EngineConfig.max_node_loops`（默认 50）。
- 乐观锁（实例行 version）：所有写命令统一触碰实例行（`CommandContext.touch`），
  同实例并发写在提交时串行化；冲突自动重试 `lock_retries`（默认 3）次。
- 时间戳统一 naive UTC（`flowork.clock.utc_now`）：多副本时钟可比，
  各机器以 NTP 对齐；注入测试时钟走 `EngineConfig.now`。
- 历史分级 `history_level`：`none`（不落事件）/ `activity`（仅流程与节点）/
  `full`（默认；`audit` 为保留档，当前实现同 full 全记）。
- 完结实例归档：`history.archive_instance(id)` 导出 JSON 冷存。
