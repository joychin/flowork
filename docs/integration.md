# flowork 集成与部署指南

## 安装与运行

```bash
uv sync                                   # Python >= 3.11
uv run pytest                             # 全量测试
uv run flowork init --db flowork.db       # 初始化 SQLite 库
uv run flowork validate examples/01_simple_approval.json
uv run flowork demo-full                  # 全流程 DoD 演示（事件回放）
```

嵌入使用：`pip install -e .` 后 `from flowork.engine.engine import ProcessEngine`。

## 表结构（SQLite，可平迁 PostgreSQL）

| 表 | 内容 |
|---|---|
| wf_process_definition | 定义（content=模板 JSON；发布后不可变） |
| wf_process_instance | 实例（definition_snapshot 快照；version 乐观锁） |
| wf_token | 令牌（三计数：item/approve/disapprove；reject_flag） |
| wf_task / wf_task_history | 待办（活表）/ 已办（append-only，驳回复活不删不改历史） |
| wf_cc_task | 传阅件（含已读） |
| wf_identity_link | 候选关联（claim） |
| wf_event_outbox | 领域事件（消息/UI/MCP 统一消费入口） |
| wf_job / wf_incident | 超时任务（重试/死信）/ 流程异常 |

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

## 与后续系统对接（二期入口已预留）

- **HTTP 服务**：四服务方法即 FastAPI 路由（一一映射，无需改动内核）。
- **消息推送**：订阅 `wf_event_outbox`（或替换 `EngineConfig.notifier` 端口）。
- **AI（MCP）**：`pyflow-mcp` 二期包把四服务映射为 ~10 个合并型工具；
  当期已落实：全 typed、双语 docstring、幂等键、行动提示式返回、actor 区分人/agent。
- **UI 设计器**：模板即 JSON（pydantic JSON Schema 可导出）；bpmn-js 路线见《命名规范》§7 预留。
- **组织系统**：替换 `EngineConfig.org`（实现 `OrgDirectory` 同形接口或适配现有库）。

## 运维要点

- 实例异常自动挂起（`suspended`）并落 `wf_incident`；修复数据后
  `resolve_incident(id, "resume")` 恢复，或 `"cancel"` 终止。
- 死循环防护：单节点完成次数上限 `EngineConfig.max_node_loops`（默认 50）。
- 乐观锁（实例行 version）：所有写命令统一触碰实例行（`CommandContext.touch`），
  同实例并发写在提交时串行化；冲突自动重试 `lock_retries`（默认 3）次。
- 时间戳统一 naive UTC（`flowork.clock.utc_now`）：多副本时钟可比，
  各机器以 NTP 对齐；注入测试时钟走 `EngineConfig.now`。
- 历史分级 `history_level`：`none`（不落事件）/ `activity`（仅流程与节点）/ `full`（默认）。
- 完结实例归档：`history.archive_instance(id)` 导出 JSON 冷存。
