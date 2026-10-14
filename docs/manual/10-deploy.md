# 10 部署与三种用法

本章讲上线相关的事：数据库形态怎么选、什么时候要迁移、多副本并发怎么理解，
以及 CLI / REST / MCP 三面的概览。部署与运维的完整细节（表结构、超时调度、
运维清单）在 `docs/integration.md`，本章只给你决策需要的部分。

## 三种数据库形态

```python
ProcessEngine()                                        # 1. 内存 SQLite：测试与试验
ProcessEngine(EngineConfig(db_file="flowork.db"))      # 2. 文件 SQLite：单机持久化
ProcessEngine(EngineConfig(
    db_file="postgresql+psycopg://user:pw@host/flowork"))  # 3. PostgreSQL：生产部署
```

规则很简单：`db_file` 是完整连接串（含 `://`）就原样透传，否则当 SQLite 文件
路径包装。`db_url` 字段是等价的直接写法。环境变量 `FLOWORK_DB` 走同一个解析
规则，CLI 与 REST / MCP 服务形态都认它：

```bash
FLOWORK_DB=/data/flowork.db uv run flowork demo
FLOWORK_DB=postgresql+psycopg://user:pw@host/flowork uv run uvicorn flowork.contrib.rest.app:app
```

引擎构造时自动建表（`create_all`）——单机 / 嵌入式形态**完全不用管迁移**。
只有「独立部署 + 库结构要随版本演进」才需要 Alembic（`migrations/`）：
`alembic upgrade head` 与引擎自动建表同源同构，不冲突；既有 create_all 的
老库先 `alembic stamp head` 对齐基线再升级。

## 并发模型（一段话版）

**同一实例的操作靠实例行乐观锁串行化；不同实例天然并行；Job 靠 CAS 原子认领
恰一次执行。**因此部署多副本（多个 web 进程、独立 tick worker、多外部 worker）
**不需要任何分布式锁组件**。并发语义的测试证据矩阵与细节见
`docs/integration.md` 的「并发模型」节。

超时驱动记得配：单进程用 `engine.jobs.start()`（web 形态默认开），
多副本部署跑一个独立 worker（`uv run flowork tick` 挂 cron / systemd）。

## CLI 速查

无代码操作与运维面（`uv run flowork <命令>`，全部认 `--db` / `FLOWORK_DB`）：

| 命令 | 干什么 |
|---|---|
| `demo` / `demo-full` | 跑示例（第 1 章用过） |
| `walkthrough <模板> [--script 剧本]` | 演练台：真实实例逐步走查，每步打印步单 |
| `validate <模板>` | 校验模板 |
| `deploy <模板>` | 部署（版本判定报告 + 幂等去重 + 破坏性清单） |
| `init` | 建表 |
| `tick` | 执行到期超时 Job（外部定时器形态） |
| `retire <code>` / `purge <code> --force` | 定义退役 / 物理删除 |
| `drift <目录>` | git 模板与库对账（未部署 / 库多余 / 库落后三张清单） |
| `instance vars / vars-set / var-history` | 实例变量读 / 写 / 血缘 |
| `task claimable <user> --org org.yaml` | 我可认领的候选待办 |
| `message waiting` | 等待中的订阅反查 |

`drift` 值得一提：模板文件放 git 管理、部署进库，「两边谁多谁少」用它对账——
CI 里跑一条，防止图纸漂移。

## REST 与 MCP：两种独立服务形态

内核之外有两层官方插件把同一套能力包成开放面（都是 `flowork/contrib/` 插件，
内核代码不含任何 HTTP / 协议逻辑）：

**REST `/v2`**（`flowork.contrib.rest`）——对外集成面。两种装配：

```bash
# 形态 A：独立进程（读 FLOWORK_DB / FLOWORK_ORG 环境变量）
uv run uvicorn flowork.contrib.rest.app:app --port 8902

# 形态 B：挂进宿主自己的 FastAPI（宿主自带引擎与鉴权）
register_rest_routes(app, engine)
```

覆盖内核的查询与操作面：实例搜索与生命周期动作、任务办理、变量读写、
消息、Job、故障单等三十余端点（清单见 `docs/API.md` 与 spec §12）。
鉴权归宿主（`X-Flowork-Actor` 头带操作人身份）。

**MCP**（`flowork.contrib.mcp`）——AI 工具面，19 个工具让 AI 客户端驱动
完整生命周期（部署、发起、办任务、暂停恢复、修故障）：

```bash
python -m flowork.contrib.mcp.server    # stdio 形态；FLOWORK_DB 同约定
```

工具分四域：定义域（校验 / 试跑 / 部署 / 对账）、实例域（发起 / 查询 /
信号 / 变量）、任务域（待办 / 认领 / 办结 / 驳回）、故障域（故障单 / 处置）。
写类工具的 actor 参数必填——AI 操作同样要归因。

另有 web 内部面（`flowork.contrib.web`，流程设计器的后端）与前端设计器
（`designer/`），那是开发与审计工具，不属于运行面——详见 `designer/README.md`。

## 本章小结

- 三库形态一句话：内存试验、文件单机、PostgreSQL 生产；`FLOWORK_DB`
  全系约定；嵌入式形态不用管迁移。
- 并发模型一句话：乐观锁串行化同实例、跨实例天然并行、Job CAS 恰一次——
  多副本不需要分布式锁。
- CLI 覆盖运维与无代码体验；REST / MCP 是内核之上的插件层开放面，
  能力同构、鉴权归宿主。
