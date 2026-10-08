# AGENTS.md — 给编码代理（与人类协作者）的工程说明

> 本文件是仓库级约定入口。改代码前先读完本文件；与设计文档冲突时，以《本期建设边界》为准。

## 构建与测试命令

```bash
uv sync                                   # 安装依赖（Python >= 3.11，uv 管理）
uv run pytest                             # 全量测试（必须全绿才能提交）
uv run pytest --cov=flowork --cov-report=term-missing   # 覆盖率（内核包目标 >= 90%）
uv run ruff check flowork tests examples  # 静态检查
uv run ruff format flowork tests examples # 格式化（提交前）
uv run mypy flowork                       # 类型检查（strict）
uv run lint-imports                       # 分层契约门禁（内核禁止 import contrib）
uv run python examples/ai_review_demo.py  # 自定义节点 demo

# web API + 设计器前端（开发模式；详见 designer/README.md）
FLOWORK_DB=/tmp/f.db uv run uvicorn flowork.web.app:app --port 8901   # 后端，空库自动灌 examples/
cd designer && npm install && npm run dev   # 前端 http://localhost:5173，/api 代理到 8901
```

提交规范：conventional commits（`feat:` / `fix:` / `test:` / `docs:` / `chore:`），每任务一提交。
**五道门全绿才允许提交：pytest + ruff check + ruff format --check + mypy + lint-imports。**

## 包结构地图（一屏）

```
flowork/
├── definitions/   定义层：node(FlowNode基类) flow(连线/条件) builtin(内置Spec)
│                  definition(模型+JSON编解码) validator(设计时校验) builder(Python DSL)
├── runtime/       运行时：behavior(NodeBehavior+NodeContext) registry(注册表) lifecycle(状态枚举)
├── expr/          条件求值：conditions(结构化条件组) formula(asteval 公式沙箱)
├── spi/           端口协议：Clock/WorkCalendar/OrgDirectory/Notifier
├── events/        领域事件类型（M1 起随 Command 管线落 outbox）
├── tasks/ participants/ persistence/ engine/ jobs/ cli/   # 内核主体（engine 已按职责拆分多文件）
├── directory/     目录（M-A）：组织/角色/用户/Agent 映射（agents/roles/store/orm）
├── web/           薄 FastAPI 传输层（app.py，无业务逻辑，与 RepositoryService 一一映射）
├── contrib/       官方插件包——内核禁止 import 它（import-linter 契约强制）
├── clock.py       时间戳统一 naive UTC 的唯一出口
├── errors.py      异常体系
tests/             definitions/ runtime/ semantics/(内核语义逐项) suite/(黑盒验收+三个走查 demo)
                   edge/ pilot/ directory/ + 顶层（web_api/outbox/spi/conditions）
examples/          3 个模板 JSON + org.yaml + ai_review_demo.py + cel_poc.py + pilot/
designer/          流程设计器前端（React Flow + zustand + Vite；详见 designer/README.md：
                   Spec 唯一事实源、schema 驱动表单、registry 驱动调色板、校验闭环）
```

根目录 `designer.db*`（含 -shm/-wal/.bak-*）是 web/设计器本地运行产物，由 `FLOWORK_DB` 指定，不要提交。

## 硬约束（违反即打回）

1. **命名**：遵循《命名规范》（benchmark_refs/out/命名规范.md）。禁用 对标产品 名
   （Biz*/分词命名/错拼Service/*Impl 等）；标识符一律英文；类禁 Impl/Factory/Manager/Util 后缀。
2. **引擎核心不得出现节点 kind 的 if/else 分支**——节点语义只经 NodeRegistry → NodeBehavior。
3. **钩子/行为只观察与写变量，不得改变路由**；路由决策只属于引擎 + 条件求值。
4. **TaskHistory（已办）只追加**，驳回复活 = 新任务 + cause_token_id。
5. **所有操作带 actor 归因**（acted_by / acted_as_agent）。
6. 单文件 ≤ 300 行；测试先行于实现（会签/驳回/推进门等难块）。
7. 范围变化一律记入《本期建设边界》backlog 附录，不直接扩期。

## 设计文档（只读，改敏感区前先读）

- 仓库内：`docs/product/engine-v1/`（产品 v1 范围权威，11 篇）、`docs/product/kernel-v2/spec.md`（内核 v2 范围权威）、
  `docs/API.md`（四服务 API 参考）、`docs/integration.md`（集成部署：表结构/超时调度/运维）。
- 仓库外（同级目录 `../benchmark_refs/out/`，**不在本仓库内**）：边界（范围权威）→ 实施规划 → 命名规范 →
  节点抽象与扩展设计 → AI友好设计 → 主流流程引擎借鉴分析；
  语义参考 基准材料/（对标产品 行为基准源码，clean-room：只对齐行为，不搬代码/命名）。

## 当前里程碑

M0–M6 全部完成（headless 内核完结）：153 个测试、mypy strict 零错误、CLI demo-full 通过最终 DoD。
单实例强化（M6 后）：写命令统一 `CommandContext.touch`（乐观锁全覆盖）、Job 调度 CAS
原子认领（多副本 tick 恰一次）、web 内置调度（`FLOWORK_JOBS`/`FLOWORK_JOB_INTERVAL`）+
`flowork tick` 独立 worker、时间戳统一 naive UTC（`flowork.clock`）；236 个测试。
产品 v1（进行中，2026-10-03 定稿，范围权威 `docs/product/engine-v1/`，11 篇）：独立部署流程引擎——
REST+MCP 双开放、目录（组织/角色/用户/Agent 映射，M-A）与权限（凭证/scope/RBAC，M-A）两支柱、
三面同构（Console/REST+MCP/Skill+CLI）、运行时开放面与数据契约 business_object（M-B）、
任务页+CLI+docker（M-C）。原二期项（FastAPI 服务层、pyflow-mcp、组织适配器、消息推送、UI）
并入 v1 主线；M7 实例迁移与 AI 行为包顺延。
内核 v2（P0/P1 全部完成，2026-10-07，范围权威 `docs/product/kernel-v2/spec.md` v2.2）：
P0-1 CEL PoC（采用 cel-python，决策记录 p0-1-cel-poc.md；换绑 expr/cel.py 待 M 系 REST/MCP 前）｜
P0-2 SPI 定稿（OrgDirectory 四方法+contrib 词汇+import-linter 入门禁 `lint-imports`）｜
P0-3 CloudEvents 信封+outbox 消费器（IdempotentSink 按 id 幂等）｜P0-4 major/revision 版本判定
（deploy 即 publish、R1-R4 破坏性规则、幂等去重、VersionVerdict、设计器草稿独立 wf_designer_draft）｜
P0-5 启动幂等三策略（reject/return_existing/allow_duplicate，唯一索引兜底）｜P0-6 input 契约三边界｜
P0-7 all 汇聚可达性放行（engine/join_gate.py）｜P1：消息服务/Job 租约 worker 面/pause-resume-terminate/
错误二分（resolve 五动作）/wf_variable_index/定义生命周期（retire-purge-drift）/身份快照 actor_context/
deadline 列表+实例级时限+escalate/anticipate 只读预判。DoD 全链路（单 SQLite 文件零配置）已验证；
下一程：REST /v2 搜索面（§12）与 MCP 17 工具（§13，M7）、Alembic 基线迁移（§14 尾注）。
代码 REVIEW 修复批（2026-10-07 第二批提交）：dead_letter 统一/消息防重收窄 message_id/暂停全动作
拦截/驳回契约/时限终态事件/文件拆分（repository·runtime·incident·anticipate·orm_v2，engine 主文件回归
≤300 行）。残留超标（既有债务，非本轮引入）：kernel.py 534、task_actions.py 470、task_service.py 364、
validator.py 368——拆解随 M 系 REST/MCP 前的重构刀处理。

当前快照（2026-10-08）：**564 个测试全绿**；web API（`flowork/web`）与设计器前端（`designer/`）已可用；
黑盒验收套件 `tests/suite`（82 例，含驳回/异常走查 demo）落地。
**变量读写接口批（2026-10-08，spec §10/§11/§12/§13/§14 + variable-io-api.md，REST 随 /v2 批、MCP 随 M7 仅登记）**：
RuntimeService 变量面（update_variables 三门槛：白名单=声明∪input schema∪既有键、保留键拒、类型对位、
≤256KB、终态拒；variables_of 只读；variable_history 血缘）；wf_variable_history 全写方留痕表
（命令尾 diff，before 快照挂 touch 与 record_event 双咽喉点，值相等跳过）；variables_updated 事件仅外部
写入发射（§10 条款改「引擎内部写不发」）；CLI instance 子命令组（vars/vars-set/var-history）；
VariableStore 死端口零兼容删除（§9 四端口）。
**重构刀清账（2026-10-08）**：kernel.py 534→281（token_records/task_records Mixin）、task_actions.py 470→213
（task_admin/task_cc/task_rework）、task_service.py 364→199（tasks/reject_flow + contract.input_schema_of）、
validator.py 368→113（validator_rules/validator_conditions）——全仓单文件 ≤300 行达成。
**CEL 换绑批（2026-10-08，P0-1 决策落地）**：`expr/cel.py`（cel-python，四护栏：2KB/链项≤256/变量≤1MB/
异常归一 FormulaError）+ `collect_references` 静态薄层原料；`ExpressionFunctions` 端口接线
（FunctionSignature 签名注册、EngineConfig 字段、routing 透传、initiator 保留标识符注入）；
validator 设计时静态检查（未声明变量 ERROR/未注册函数有注册表 ERROR 无注册表 WARNING）；
`contrib.cel_vocabulary` 三取人词汇；asteval 依赖退役、`expr/formula.py` 删除；
路由期 CEL 求值错按错误二分包 EngineExecutionError（挂起+故障单），groups 支直抛语义不变。
公式语法＝CEL（`&&/||/true/false/null/in [..]/.size()`），上限 2048 单源 definitions/flow.py。
**条件单轨化（2026-10-08，spec §4.1 v2.3）**：结构化条件组 groups 退役，`SequenceFlow.condition`
直存 CEL 公式字符串（FlowCondition/ConditionItem/ConditionOperator 三类已删）；**条件求值错误语义统一为技术
故障**（FormulaError→EngineExecutionError→挂起+故障单，groups 支直抛回滚语义退役）；设计器
EdgeForm 单轨公式编辑。范围变更已记《本期建设边界》backlog 附录。
**CEL 位点统一批（2026-10-08，spec §4.2，零兼容）**：全表达式位单语法——取人（assignee/
assignees 每项一表达式）、传阅（recipients）、超时改派（reassign_to fire 时求值 + `task.*`
快照注入，spec C.4）、子流程映射值（输入父上下文/输出子上下文）全部换绑
`expr.cel.evaluate_formula_value`（值变体，BoolType 收敛原生 bool；中文变量名不毒化求值）；
上下文构造统一 `expr/context.py`（expression_context + expression_functions_of，叶子模块防环）。
**删除**：条件 loader shim（零兼容，存量已迁移）、取人迷你语法（participants/functions.py）、
简化解析（participants/simple.py）、`EngineConfig.participant_functions` 双注册面
（contrib.org_vocabulary 退役，cel_vocabulary 成唯一注册面）、originator 魔法词（→initiator）。
**语义配套**：求值错=技术故障全位点一致；查无传播（null/空贡献空，spec §5.5）；谓词误用取人位
发布期拦（cel.root_call 根调用 returns 对位）；validator 静态检查扩至全位点（iter_expression_positions，
连线位 condition.* / 节点位 expression.* 规则名；task.* 仅改派位可用；输出映射跳过父侧未声明变量检查）；
加签重排存具体人（resolver.resolve_subjects 共用件）。范围变更已记《本期建设边界》backlog。
**会签出口统一（2026-10-08 补刀，spec §4.1 收敛「一种写法」）**：approve_exit/disapprove_exit
专用语法（all/any/>=N/>=N%）退役→CEL 布尔公式（task.* 注入三计数快照，整数乘法精确等价
百分比 ceil 语义；缺省＝该侧全票）；countersign.py 重写为 exit_reached（side 参数承载缺省），
behaviors/anticipate/reject_flow 三点接线；validator 增 exit 位（task.* 在 reassign 与 exit
位可用）；behaviors 拆出 tasks/deadlines.py 守行数红线。解锁复合条件出口与业务变量法定人数。

