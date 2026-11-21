<div align="center">

# flowork

**可嵌入 Python 的 BPM 流程引擎，也能独立部署**

面向开发者：作为库嵌进你的 Python 应用，`from flowork.api import ...` 一个入口
用全部能力；需要独立服务时，同一份内核经 RESTful API 与 MCP 插件面独立部署。
审批流画成一张图交给它——生成待办、按条件选路、数会签票、记审计、发事件，
全程留痕，随时回放。

[快速开始](#快速开始) · [内核手册](docs/manual/README.md) · [API 参考](docs/API.md) · [内核规格](docs/product/kernel-v2/spec.md)

Python 3.11+ · SQLite / PostgreSQL · REST /v2 · MCP · MIT License

</div>

---

## 它是什么

flowork 是一个**可嵌入 Python 的 BPM 流程引擎**：你把业务流程定义成一张有向图
（谁审批、条件怎么走、超时怎么办），交给它部署；之后每发起一笔业务，它就照着
图纸推进——生成待办、记录已办、按条件选路、到终点收尾，每个动作都带办理人归因，
全程可审计、可回放。

嵌入与独立部署用同一份内核：

1. **嵌入式 Python（首选）**：`ProcessEngine()` 直接嵌进宿主应用，`from flowork.api import ...` 一个入口。
2. **命令行**：无代码体验、流程发布、实例运维（`flowork demo` 起步）。
3. **独立部署**：RESTful API 与 MCP 把同一份内核包成 HTTP / AI 工具面。

扩展点全部以插件方式提供，不进内核、按需挂载：

- **RESTful API** 与 **MCP**（AI 工具面）——两层传输插件，可独立部署也可挂进宿主 FastAPI；
- **找人函数、流程分支条件函数**——经表达式函数插件注册（`ExpressionFunctions`），
  条件、取人、会签出口等所有公式位直接可用；
- **自定义节点类型**——节点注册表加 Spec + Behavior 即可，引擎核心零改动；
- **组织目录**——目录端口对接你现有的组织/角色数据。

它**不是** BPMN 的 Python 实现，刻意不暴露「网关」这类 BPMN 词汇。分支相关的
用户词汇只有三个：

| 用户词汇 | 配置位置 | 说明 |
|---|---|---|
| **条件分支**（kind=`branch`） | 出边挂条件 | `route_mode=first_match` 命中即走 / `all_match` 命中的都走 |
| **同时进行** | 多条无条件出边 | 无需专门节点，引擎默认全部激活 |
| **开始条件**（`advance_policy`） | 多入边节点 | `any` 任一前置到达即开始（缺省，竞速并取消兄弟挂起分支）/ `all` 等全部到齐 |

## 特性一览

| 领域 | 能力 |
|---|---|
| 流程定义 | JSON 单一事实源；八种内置节点；设计期校验（结构/可达性/表达式静态检查）+ 部署期引用校验 |
| 人工任务 | 认领 / 办理 / 驳回回退 / 转办 / 协办 / 咨询 / 加签 / 撤回 / 候选池；服务端下发 `available_actions` |
| 会签 | 并行/串行；出口公式（任一票、全票、严格过半、任意 CEL 布尔）；票随人走；表决行统一口径 |
| 超时 | 每任务多级 deadline，五种动作（notify / reassign / escalate / auto_approve / auto_reject）；实例级时限 |
| 等待 | wait 节点三模式合一：等消息 / 等时长 / 竞速（先到者赢，自动取消其余分支） |
| 子流程 | 同步调用 + 多实例子流程（按名单每人一实例，对齐 Zeebe inputCollection/outputCollection 契约） |
| 版本 | 发布不可变；major/revision 自动判定 + R1–R4 破坏性变更清单；幂等去重；在途实例钉住部署时的定义快照 |
| 表达式 | CEL 单语法贯穿全部位点（条件 / 取人 / 传阅 / 会签出口 / 超时改派 / 映射）；函数可插拔注册 |
| 审计 | 45 种领域事件 + outbox 幂等消费；路由决策逐边留痕；变量写入全留痕；已办/轨迹/令牌链查询 |
| 模拟 | 整图模拟试跑（决策扮演 / 桩返回值 / 虚拟时钟 / 等待假设）；场景回归脚本 + 断言 + 覆盖照明，CI 友好 |
| 组织 | 组织 / 角色 / 委托（时间窗）/ 候选池认领；「我可认领的待办」查询维度 |
| 开放面 | REST `/v2`（错误体/游标分页统一）+ MCP 19 工具 + CLI；AI 与人行为同构，归因统一 `actor` |
| 存储 | SQLite 零配置起步（内存/单文件），PostgreSQL 一行切换；Alembic 迁移；乐观锁 + Job CAS 认领支持多副本 |

## 快速开始

### 安装

```bash
git clone https://github.com/joychin/flowork.git
cd flowork
uv sync            # 安装依赖（Python >= 3.11，uv 管理）
```

### 30 秒体验（不写代码）

```bash
uv run flowork demo        # 示例 01：发起 → 经理审批 → 财务复核 → 结束，回放事件表
uv run flowork demo-full   # 全语义演示：条件分支 → 会签 → 驳回 → 重提 → 传阅 → 流转轨迹表
```

还有一个「流程演练台」——用剧本驱动一个真实实例逐步走查，每步打印一张步单
（路由怎么选的、变量怎么变的、发了什么事件）：

```bash
uv run flowork walkthrough examples/04_expense_claim.json --script examples/04_expense_walkthrough.json
```

### 嵌入式 Python：跑通第一个流程

完整可跑（仓库根目录执行 `uv run python`），对照 `examples/01_simple_approval.json`：

```python
from flowork.api import ProcessEngine, create_registry, load_definition_file

# 1. 建引擎：零配置，默认内存数据库，构造即建表，进程结束数据即消失
engine = ProcessEngine()

# 2. 部署定义：校验通过即发布，立即可发起
definition = load_definition_file("examples/01_simple_approval.json", create_registry())
engine.repository.deploy(definition)

# 3. 发起实例
instance_id = engine.runtime.start_process(
    "simple_approval", start_user="u_zhang", variables={"amount": 500}
)

# 4. 办理任务——每个动作都必须带办理人（actor），这是引擎的归因铁律
actors = {"manager": "u_manager", "finance": "u_finance"}
for _ in range(5):
    tasks = engine.task.list_tasks(instance_id=instance_id)
    if not tasks:
        break
    task = tasks[0]
    engine.task.complete(task.id, actor=actors.get(task.activity_code, task.assignee),
                         comment="同意")

# 5. 看结果与事件回放
print(engine.runtime.get_instance(instance_id).state)   # completed
for event in engine.history.events(instance_id):
    print(event.event_name, event.payload)
engine.close()
```

要持久化，把内存库换成文件或 PostgreSQL 只改构造参数（见[手册第 11 章](docs/manual/11-deploy.md)）。

### 作为独立服务运行（REST /v2）

```bash
# 文件库 + 内置调度（超时 Job 到点自动兑现）
FLOWORK_DB=/tmp/flowork.db uv run uvicorn flowork.contrib.rest.app:app --port 8900
```

```bash
# 发起一笔实例（actor 经请求头归因）
curl -X POST http://localhost:8900/v2/instances \
  -H "Content-Type: application/json" -H "X-Flowork-Actor: u_zhang" \
  -d '{"code": "simple_approval", "start_user": "u_zhang", "variables": {"amount": 500}}'
```

要点：统一错误体 `{"error": {code, message, details}}`；游标分页；actor 归因经
`X-Flowork-Actor` 头。也可以不独立部署，把 `/v2` 挂进你自己的 FastAPI：

```python
from flowork.contrib.rest import register_rest_routes
register_rest_routes(app, engine)   # 宿主 FastAPI + 宿主引擎
```

### MCP：给 AI 用

```bash
python -m flowork.contrib.mcp.server   # stdio 传输，19 个工具
```

工具覆盖从定义到办结的全生命周期：`deploy` / `validate_spec` / `definition_drift` /
`list_definitions` / `start_instance` / `query_instance` / `list_my_tasks` /
`claim_task` / `complete_task` / `reject_task` / `signal_instance` / `publish_message` /
`update_instance_variables` / `pause_instance` / `resume_instance` / `resolve_incident` /
`list_incidents` / `dry_run` / `anticipate_task`。

其中 `anticipate_task` 是**只读预判**——提交前先算「这一票投下去会发生什么」，
主流引擎没有等价物，是给 AI 办理审批的差异化能力。

### 流程设计器（Web UI）

React Flow 可视化界面，定位是**流程的信任面**：检视、调整编辑、模拟测试
（路由预演 / 整图模拟 / 场景回归 / 草稿与已发布版对比）。定义从本地文件读取
（`FLOWORK_DEFINITIONS_DIR`），引擎跑在内存库——设计器进程不碰你的业务数据库。

```bash
# 后端
FLOWORK_DEFINITIONS_DIR=./examples uv run uvicorn flowork.contrib.web.app:app --port 8901
# 前端
cd designer && npm install && npm run dev    # http://localhost:5173
```

也可作为 npm 库嵌入宿主页面（默认只读），或把设计器路由挂到宿主 FastAPI。
详见 [designer/README.md](designer/README.md)。

## 一个流程定义长什么样

摘自 [examples/04_expense_claim.json](examples/04_expense_claim.json)（金额分流 + 会签 + 驳回回退）：

```jsonc
{
  "code": "expense_claim",
  "variables": [
    { "name": "amount", "type": "number", "label": "报销金额" },
    { "name": "reason", "type": "string", "label": "报销事由" }
  ],
  "nodes": [
    { "code": "start",   "name": "发起",         "kind": "start" },
    { "code": "submit",  "name": "填写报销申请", "kind": "user_task", "assignee": "initiator" },
    { "code": "manager", "name": "部门经理审批", "kind": "user_task",
      "assignee": "'u_manager'", "reject_target": "submit" },
    { "code": "route",   "name": "金额分流",     "kind": "branch", "route_mode": "first_match" },
    { "code": "cfo",     "name": "总监会签",     "kind": "user_task",
      "assignees": ["'u_alice'", "'u_bob'"],
      "multi_instance": { "mode": "parallel",
                          "approve_exit": "task.approve_count * 100 > task.item_count * 50" } },
    { "code": "notify",  "name": "结果传阅",     "kind": "cc_task", "recipients": ["initiator"] },
    { "code": "end",     "name": "结束",         "kind": "end" }
  ],
  "flows": [
    { "source": "route", "target": "finance", "condition": "amount > 5000" },
    { "source": "route", "target": "notify",  "is_default": true }
  ]
}
```

- 取人表达式里的 `'u_manager'` 必须带 CEL 引号（字符串字面量）；`initiator` 是引擎注入的发起人保留词。
- 会签出口是 CEL 布尔公式：`task.approve_count * 100 > task.item_count * 50`＝严格过半
  （整数乘法精确等价百分比取整语义）。
- `reject_target` 让经理驳回时把流程拉回「填写报销申请」节点，原办理人复活待办。

完整字段参考见[手册第 3 章](docs/manual/03-definition.md)。

## 内置节点（八种）

| kind | 用途 | 关键能力 |
|---|---|---|
| `start` | 发起 | 支持 `start_on_message` 消息启动 |
| `end` | 结束 | 终态收尾 |
| `user_task` | 人工任务 | 认领/办理/驳回/会签/加签/协办/咨询/撤回/候选池/多级超时 |
| `service_task` | 服务任务 | 宿主注册 handler 执行外部动作；未注册＝模拟可跑、真实运行故障单 |
| `branch` | 条件分支 | `first_match` / `all_match` 两种路由模式 |
| `cc_task` | 传阅 | 通知性任务，不占审批票 |
| `call_activity` | 子流程 | 版本绑定（latest/deployment）+ 多实例（按名单每人一实例、对齐聚合、提前收工公式） |
| `wait` | 等待 | 等消息（关联键自动求值）/ 等时长 / 竞速，统一结果词汇 `wait.*` |

## 人工任务的完整动词面

审批场景是 flowork 的主场，任务动作在服务端按 `(状态 × 任务类型 × 认领态)` 声明，
`available_actions` 随任务下发——前端和 AI 拿到任务就知道能做什么：

- **认领制**：任务可配候选池（人/角色/部门），候选待办有专门的「我可认领」查询维度；
- **驳回**：可回退到指定节点并复活原办理人（多轮驳回名单不膨胀）；
- **转办**：票随人走，被转办人完成计一票，转办人不留「已参与」；
- **加签/协办/咨询**：加签人各占一票；协办不占票、随主任务同事务收口；咨询＝先收意见、
  原办理人最后拍板，意见未收齐主任务全动作拦截；
- **撤回**：下游有实质进展时拒绝撤回并提示改用驳回（错误带进展明细）；
- **超时**：每任务可挂多级 `deadlines`，动作 notify / reassign / escalate /
  auto_approve / auto_reject，到期由 Job 调度兑现（独立 worker 或内置调度均可）。

## 版本与发布

- `deploy` 即发布：校验 → 落库 → 立即可发起，自动给出 major/revision 版本判定
  与**破坏性变更清单**（input 收紧等 R1–R4 规则）；
- 同内容重复部署幂等去重；发布版本不可变，在途实例钉住部署时的定义快照——
  发新版不影响在跑的实例；
- `retire` 让定义停止接新单、在跑跑完；`purge` 物理清理（有实例引用则拒绝）；
  `drift` 对账 git 与库的差异（CI 发布管线的守门命令）。

## 审计与事件

- **45 种领域事件**（节点进出、任务创建/办结/转办/超时、路由、消息、实例终态……）
  统一经 outbox 落表，消费者按游标拉取、按事件 id 幂等去重；事件可排序即全序，可完整回放一笔业务；
- **路由决策逐边留痕**：每次选路记录命中了哪些条件边、公式结果、选了哪条边，
  快照当时的事实取值——「为什么走了这条线」永远可回答；
- **变量写入全留痕**：谁、什么时候、把哪个变量从什么改成什么；
- 已办、令牌链、轨迹时间线均有只读查询面（`history` 服务）。

## 模拟测试（发布前拿证据）

- **整图模拟**：引擎真跑一份草稿（不落生产数据）——你扮演办理人（`decisions`）、
  桩掉外部系统（`stubs`）、假设等待结果（`waits`）、推虚拟时钟（`clock`），
  输出终态/令牌时间线/路由轨迹/桩清单/停点说明；
- **场景回归**：一份脚本三类步骤（办理 / 等待 / 推钟）+ 六型断言，同一份脚本
  在设计器、CLI（`flowork scenario run`，退出码 CI 友好）、pytest 里都能跑；
  还支持**草稿与已发布版并排对比**——改了流程之后，老场景结论是否漂移一目了然；
- 超时改派这类「时间相关」语义在虚拟时钟下端到端可验证（不用真等一天）。

## 部署与运维

### 数据库与迁移

| 形态 | 说明 |
|---|---|
| 内存 SQLite | 零配置，试验/测试/设计器模拟用，进程结束即消失 |
| 文件 SQLite | 单文件即生产可用（WAL 模式），`FLOWORK_DB=/path/db.sqlite3` |
| PostgreSQL | 同一 URL 通道切换；多副本部署建议 PG |

```bash
uv run alembic upgrade head     # 建表/升级迁移（空库亦可直接由引擎建表）
```

### 并发与多副本

写命令乐观锁全覆盖（版本冲突＝可重试信号）；超时 Job 用 CAS 原子认领，
多副本同时跑 `flowork tick` 或共享内置调度也恰好执行一次；同步推进链路
零外部 I/O，SQLite WAL 下即可支撑单机多进程。

### 环境变量

| 变量 | 说明 |
|---|---|
| `FLOWORK_DB` | 数据库路径或 URL（嵌入式/CLI/REST） |
| `FLOWORK_JOBS` / `FLOWORK_JOB_INTERVAL` | web/REST 进程内置超时调度开关与轮询间隔 |
| `FLOWORK_ORG` | 组织目录 YAML（角色/委托/候选，取人词汇自动装配） |
| `FLOWORK_SETUP` | `模块名`：宿主三钩子（自定义节点 / 表达式函数 / handler）带进独立进程 |
| `FLOWORK_VOCABULARY` | `模块:工厂`：仅表达式词汇的便捷装配 |
| `FLOWORK_DEFINITIONS_DIR` | 设计器文件事实源目录 |
| `FLOWORK_CORS_ORIGINS` | 显式开启跨域（默认不挂） |

### 安全边界（部署必读）

引擎**不做鉴权**——这是刻意的设计边界：引擎只忠实记录「谁做了什么」
（每个动作必须带 `actor`，归因字段 `acted_by`），「这个人是不是真的是他、
能不能办这个任务」由宿主的身份与权限系统把关。对接 REST 时，`X-Flowork-Actor`
头必须由宿主网关设置并校验，不能让客户端自带。详见
[docs/integration.md](docs/integration.md)。

## 架构分层

```
宿主应用 / 前端 / AI Agent
        │
┌───────┴────────────────────────────────────────────────┐
│ 投影与插件层（只做翻译，无业务逻辑）                       │
│ CLI · REST /v2 · MCP · 设计器 web · 表达式词汇插件        │
└───────┬────────────────────────────────────────────────┘
        │ 只准 import flowork.api
════════ flowork.api 公开门面（96 符号冻结，快照测试守卫）════════
        │
┌───────┴────────────────────────────────────────────────┐
│ 内核                                                    │
│ definitions 定义+校验   runtime 注册表+节点行为           │
│ expr CEL 沙箱+词汇目录  engine 推进/路由/汇聚/钩子        │
│ tasks 任务动作/候选/表决 events 45 领域事件+outbox        │
│ directory 组织/角色/委托  jobs 超时调度                   │
└───────┬────────────────────────────────────────────────┘
        │ SPI 端口（可插拔）
        │ ExpressionFunctions 表达式函数 · OrgDirectoryLike 组织目录
        ▼
SQLAlchemy 2：SQLite / PostgreSQL · Alembic 迁移
```

分层纪律由 import-linter 强制：内核禁止 import 任何插件；插件与投影只准经
`flowork.api` 使用内核。**扩展一种节点类型不需要改引擎**——注册 Spec（数据）+
Behavior（行为）即可，引擎核心没有节点 kind 的 if/else 分支：

```python
from flowork.api import FlowNode, NodeBehavior

class MySpec(FlowNode):
    kind: str = "my_node"
    my_field: str = ""

class MyBehavior(NodeBehavior):
    def on_enter(self, ctx):
        ctx.variables["my_result"] = "done"
```

## 文档地图

| 文档 | 内容 |
|---|---|
| [docs/manual/](docs/manual/README.md) | **内核手册（使用者视角，12 章）**：五分钟跑通 → 概念 → 定义 → 发布 → 表达式 → 任务 → 实例 → 异步 → 观测 → 扩展 → 部署 → FAQ；示例全部实跑验证 |
| [docs/API.md](docs/API.md) | Python API 速查字典（四服务 + 消息/Job + 扩展点） |
| [docs/integration.md](docs/integration.md) | 集成部署：表结构、超时调度、并发模型、运维 |
| [docs/product/kernel-v2/spec.md](docs/product/kernel-v2/spec.md) | 内核语义与范围权威（语义细节的最终依据） |
| [docs/product/engine-v1/](docs/product/engine-v1/) | 产品 v1 范围（REST+MCP 开放、目录与权限、任务页等） |
| [AGENTS.md](AGENTS.md) | 工程约定：构建测试命令、包结构地图、硬约束（改代码必读） |
| [llms.txt](llms.txt) | 文档索引（AI 友好入口） |

## 开发

```bash
uv run pytest                        # 全量测试（必须全绿才能提交）
uv run pytest -m randomized          # 随机结构生成测试（显式开门）
uv run pytest -m needs_postgres      # PostgreSQL 冒烟（自动 docker 起删容器）
uv run ruff check flowork tests examples migrations
uv run ruff format flowork tests examples migrations
uv run mypy flowork                  # strict
uv run lint-imports                  # 分层契约门禁
```

提交纪律五道门全绿：pytest / ruff check / ruff format --check / mypy / lint-imports。

当前质量水位：**1298 个测试全绿**（另有随机结构、PG 冒烟等三档重测试显式开门），
mypy strict 零错误，全仓单文件 ≤300 行，覆盖率作照明指标不作门禁。引擎经历过
浸泡对打（多进程对同一文件库、账本逐实例核对）、kill -9 崩溃恢复、NTP 回拨、
PG 断连重连等混沌演练。

## 状态与路线

- **内核 v2（完成）**：CEL 单轨、SPI 端口、版本判定、消息/等待/子流程、错误二分、
  表达式数据面、候选认领制、多实例子流程、事件面全链路。
- **产品 v1（进行中）**：REST /v2 与 MCP 开放面、目录与候选认领、模拟测试套件、
  设计器审计插件已落地；任务页、docker 打包、business_object 数据契约在排期。
- 设计器定位为**开发者的流程信任面**（检视 / 调整 / 模拟），发布动作归 CI/CD 管线。

## License

[MIT](LICENSE)
