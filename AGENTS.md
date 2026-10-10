# AGENTS.md — 给编码代理（与人类协作者）的工程说明

> 本文件是仓库级约定入口。改代码前先读完本文件；与设计文档冲突时，以《本期建设边界》为准。

## 构建与测试命令

```bash
uv sync                                   # 安装依赖（Python >= 3.11，uv 管理）
uv run pytest                             # 全量测试（必须全绿才能提交）
uv run pytest --cov=flowork --cov-report=term-missing   # 覆盖率（照明指标，不作提交门禁——对标 Temporal/Airflow/Argo）
uv run pytest -m randomized               # 随机结构生成测试（默认排除，显式开门）
uv run pytest -m needs_postgres           # PostgreSQL 冒烟套件（默认排除；未设 FLOWORK_TEST_PG_URL 时自动 docker 起删容器）
uv run ruff check flowork tests examples migrations  # 静态检查
uv run ruff format flowork tests examples migrations # 格式化（提交前）
uv run mypy flowork                       # 类型检查（strict）
uv run lint-imports                       # 分层契约门禁（内核禁止 import contrib）
uv run python examples/ai_review_demo.py  # 自定义节点 demo

# web API + 设计器前端（开发模式；详见 designer/README.md）
FLOWORK_DB=/tmp/f.db uv run uvicorn flowork.contrib.web.app:app --port 8901   # 后端，空库自动灌 examples/
cd designer && npm install && npm run dev   # 前端 http://localhost:5173，/api 代理到 8901
```

提交规范：conventional commits（`feat:` / `fix:` / `test:` / `docs:` / `chore:`），每任务一提交。
**五道门全绿才允许提交：pytest + ruff check + ruff format --check + mypy + lint-imports。**
回归测试（修 bug 配套的测试）命名或注释须带来源（issue 号/批次号/走查发现）——让测试携带「我为什么存在」。

## 包结构地图（一屏）

```
flowork/
├── api.py         公开门面（唯一稳定 API 面）：宿主/投影/插件只准 import 这里——
│                  符号冻结（快照测试守卫）；内核重排（spec §15）只准动内部件
├── definitions/   定义层：node(FlowNode基类) flow(连线/条件) builtin(内置Spec)
│                  definition(模型+JSON编解码) validator(设计时校验) builder(Python DSL)
├── runtime/       运行时：behavior(NodeBehavior+NodeContext) registry(注册表) lifecycle(状态枚举)
├── expr/          表达式层：cel(CEL 沙箱+静态检查) catalog(词汇目录) context(上下文装配)
│                  conditions(条件求值薄层)
├── spi/           端口协议：Clock/WorkCalendar/OrgDirectory/ExpressionFunctions/EventSink
├── events/        领域事件类型（M1 起随 Command 管线落 outbox）
├── tasks/ participants/ persistence/ engine/ jobs/   # 内核主体（engine 已按职责拆分多文件）
├── directory/     目录（M-A）：组织/角色/用户/Agent/委托（agents/roles/delegation/
│                  queries 读缓存/audit 写审计/store/orm）
├── cli/           CLI 投影面（运维+用户面）——只准走 flowork.api（静态纪律测试守卫）
├── contrib/       官方插件包：词汇插件(cel/numeric) + web 传输面(web/，rest/mcp 进驻后同列）
│                  ——内核禁止 import 它；插件只准经 flowork.api 用内核（双向铁律）
├── clock.py       时间戳统一 naive UTC 的唯一出口
├── errors.py      异常体系
tests/             definitions/ runtime/ semantics/(内核语义逐项) suite/(黑盒验收+三个走查 demo)
                   edge/ pilot/ directory/ + 顶层（web_api/outbox/spi/conditions/
                   api_surface+projection_surface 结构守卫）
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
下一程：REST /v2 搜索面（§12）与 MCP 18 工具（§13，M7）、Alembic 基线迁移（§14 尾注）。
代码 REVIEW 修复批（2026-10-07 第二批提交）：dead_letter 统一/消息防重收窄 message_id/暂停全动作
拦截/驳回契约/时限终态事件/文件拆分（repository·runtime·incident·anticipate·orm_v2，engine 主文件回归
≤300 行）。残留超标（既有债务，非本轮引入）：kernel.py 534、task_actions.py 470、task_service.py 364、
validator.py 368——拆解随 M 系 REST/MCP 前的重构刀处理。

当前快照（2026-10-08）：**625 个测试全绿**；web API（`flowork/contrib/web` 插件位）与设计器前端（`designer/`）已可用；
黑盒验收套件 `tests/suite`（含驳回/异常走查 demo）落地。
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
**表达式数据面 F0+F1（2026-10-08，设计=docs/product/kernel-v2/expression-dataface.md，已批）**：
判断面=精选事实目录（与 variable-io 读写面互补）。F0：`expr/catalog.py` 词汇目录
（initiator/now/task 条目：类型/中文标签/可用位点/备注）成为保留词与位点白名单的权威，
validator_conditions 硬编码 frozenset 退役（RESERVED_POSITIONS 表驱动，规则名不变）；
FunctionSignature 补 `label`（函数目录下发原料）+ contrib.cel_vocabulary 三函数标注。
F1：`now` 求值时刻全位点注入（expr/context.py 唯一装配点，统一时钟 naive UTC，
一次装配钉一时刻——routing/resolver 组内多公式同刻；dry-run 模拟面与 web 公式试算面
同步注入）。575 测试全绿。
**表达式数据面 F2（2026-10-08 同日，核心批）**：`tasks/facts.py` 节点事实现查聚合
（wf_task_history+wf_token；used_time 取 cost_seconds 派生 timedelta）。`prev_task`
上一站（=最近一条已完成任务，条件+取人位；查无不注入，`references_identifier`
静态预检——条件跳过/取人贡献空，温和不出故障单）；`node("code")` 引擎注入函数
（runs=已结束轮数/skipped/最近轮五字段/最近轮票数；查无=固定形状：人物 null
计数 0，返回值过 json_to_cel 才可字段选择）；接线七点（routing/behavior/branch/
incident/anticipate/cc/rework，resolver._evaluate_expressions 为取人咽喉）；validator
泛化 `*.{name}_unavailable` 位点规则 + 引擎函数目录对照（arity/位点，无注册表不误报）；
改派位 task.* 补 created_at/deadline_at。设计文档 B2 口径修正（重报后查驳回须用
node()，prev_task 因果链语义由 D 组承担）；C5 exit assignees 记开放问题。
**表达式数据面 F3（2026-10-08 同日，路由审计=合并 P2 遗留「路由决策无记录」）**：
`wf_route_decision` 表（orm_v2）一行一次路由——edges 逐条件边 {flow,formula,result
∈true/false/no_fact}、selected 激活连线、snapshot={now,prev_task,node 取值}（datetime
→ISO/timedelta→秒）；`engine/route_audit.py` RoutingAudit 收集器（routing 旁路填充，
attach_node 的 node_calls 回填 node() 读值）；三真实入口持久化（behavior/branch/
incident skip 同事务），无条件路由也落行；anticipate/dry-run 只读预演不审计；求值
失败不落行（故障单已含公式与错误）。查询面 HistoryService.route_decisions。
594 测试全绿。剩余 F4 设计器目录下发/F5 spec 收口。

**能力补足 P0 批（2026-10-08，依据 docs_research 能力基准调研的差距清单）**：
① `test(edge)` 用户动作并发竞态套件（8 例：complete×2 单引擎/双副本、complete vs
reject 真线程+双确定性交错、submit vs timer fire、pause vs 推进、多 worker 抢批+
易主 JobStateError；tests/edge/conftest.py 收敛文件库引擎工厂——同名即同库）。
② `contrib/numeric.py` numeric_vocabulary 六散件（split/abs/max/min/sort/sum，
签名+中文 label；max/min 空列表 null、sum 空列表 0、类型错→FloworkError 技术故障；
注册全局 min 与 celpy [.].min() 共存；contrib/_registry.py 抽共享 FixedFunctions）；
`expr/cel.py` CEL_BUILTIN_FUNCTIONS 修正 startsWith/endsWith 真名+补
contains/bool/list/null_type（此前全局调用误报未注册 ERROR）。
③ `definitions/graph.py` 图遍历共享件（upstream_basin 自 join_gate 下沉 +
forward_reachable/can_reach_end）+ `validator_graph.py` 图级 lint 两规则（全 WARNING
不拦发布）：graph.subgraph.no_exit 封闭子图、join.arm.unreachable all 汇聚死臂；
判据与运行时放行门同源（join_gate 改 import 复用，语义零变化），条件未命中臂
不告警（不复活已移除的 advance.all_conditional_arm）。④ docs/integration.md 新增
「并发模型」声明节（一句话串行化模型 + 竞态测试矩阵证据索引）。624 测试全绿。

**卫生批+文档收口（2026-10-08，能力补足盘点后续；表达式数据面 F5 顺带完成）**：
① designer EdgeForm datalist 死代码块删除（datalist 挂不了 textarea 且无消费）。
② 三个贴线/超标文件拆分守 300 行红线（Mixin 组合、入口类不变）：history_service
329→283（archive_instance→history_archive.py）、validator_conditions 309→168
（check_variable_references→validator_references.py 规则组三）、scheduler 305→155
（策略执行四方法→jobs/strategies.py）；`tests/suite/test_file_size_contract.py`
行数守卫测试（flowork/*.py ≤300 行，超标即红）。
③ 过期注释清理：loader shim/asteval/变量端口表述（flow.py/conditions.py/
expr/__init__/spi/__init__/cel_poc.py）+ 包地图 expr/spi 两行对齐现役结构。
④ **F5 spec 收口**：spec §2.2 condition 字段表 FlowCondition→str(CEL)、§2.5 会签
出口字段去掉退役专用语法、§4.3 补判断面事实目录（now/prev_task/node()，
instance.* 标未实现后续项）、§11 状态列对齐 P1 实况+签名对齐（offset 分页）、
C.4 补 now/prev_task/node() 三行（形状对齐 tasks/facts.py）；expression-dataface.md
状态行标 F5 完成。
⑤ 文档现状收口：docs/API.md（save_definition/publish→deploy/update_draft 死方法名
修正+补 MessageService/JobService worker 面/pause 族/retire 族/route_decisions/
anticipate/幂等三策略）；README.md 里程碑刷新+去 headless 矛盾；llms.txt 补
docs/product/ 权威索引；integration.md 补 v2 六表+目录表+CLI 清单+resolve 五动作
+history_level audit 口径+对接面实况；designer/README API 表补 4 条+目录补 6 文件。
⑥ MCP 工具数统一为 **18**（kernel-v2 spec §13 权威；AGENTS.md 17→18；engine-v1
01 §6/00/04 加注——8 个为 M-B 首发子集，纪律不变）。625 测试全绿。

**内核收口批（2026-10-09，六提交 99b8131→6845068；用户裁定 REST/MCP=插件层后置、
先完成内核本体，范围变更由用户记《本期建设边界》backlog）**：
① SPI 死端口收口——Notifier/ResourceProvider 零消费删除（notify 实际直接
record_event 落 outbox，事件即通知面；EngineConfig.notifier 死字段一并摘除），
spec §9/engine-v1 两处/kernel-v2-plan 表述同步。
② instance.* 判断面词汇——id/started_at/business_key 全位点注入（catalog 入册/
expression_context 增参/九点接线：routing·resolver·cc·rework·子流程映射·超时
改派·会签出口；route_audit 快照钉取值；dry-run 与 web 试算面注入占位形状；
business_key 入册为本批新增决策——招标单号场景）；spec §4.3/C.4 摘「后续项」标注。
③ CLI deploy 命令（VersionVerdict 报告：major/revision/破坏性清单/幂等去重提示）
+ CLI app 级包装层测试（tests/test_cli_app.py 七例，补测试盲区）。
④ start_on_message 消息启动（spec §7.1 新节；设计提交 0e19605 **待用户复核**五点
拍板：不回溯缓冲/correlation=载荷求值键==发布键防串单/start_user 缺省合成
message:{name}/启动消费定格 matched 订阅行进 message_id 防重并集/声明表达式
求值错=publish 技术故障）——StartMessageDecl 定义字段 + engine/message_start.py
第三优先级匹配分支（在途节点订阅优先→启动匹配→缓冲；启动订阅=定义派生零新表）
+ validator 规则（node() 在消息上下文即 ERROR）+ 九例。
⑤ M-A 收口半边——org_delegation 委托表（[since,until] 窗口、单跳不递归、撤销
留痕；resolver 委托替换真正生效）+ admins=users_with_role("admin")（to_admins
兜底同源）+ 目录读缓存（ReadDirectoryMixin 进程内缓存，写操作全清）+
org_directory_audit 写审计（全部写方法带 keyword-only acted_by，缺省 system）；
store 拆 queries/delegation/audit 守 300 行；SPI 四方法定稿不动（丰富面
OrgDirectoryLike 本就承载 users_with_role/delegation_of/admins/agent_owner）。
⑥ Alembic 基线（migrations/0001_baseline=共享元数据 create_all 同源同构；
FLOWORK_DB 解析；既有 create_all 库 stamp head 对齐；create_all 与 upgrade head
并存不冲突）。**663 测试全绿**。开放面（REST /v2、MCP 18、F4 设计器目录下发）
按插件层排期，待用户启动；挂账开放问题（三契约合一/事件脱敏归属/used_time
剔除/变量历史清理/C5 出口 assignees）不阻塞主线。

**等待节点批（2026-10-09，spec §7.2 实施；设计=wait-node.md v3 六拍板点收口；行业依据=docs_research 六引擎调研）**：
内置第八种 kind=wait——三模式合一（等消息/等时长/竞速，Conductor WAIT 先例）：message（correlation_key
进入求值快照）+output_mappings（裸投影缺键 None/缺省全量合并，input 契约把关）+timeout{after_minutes}；
完成三路统一 finish_wait（matched/elapsed/signal），结果词汇 wait.* 仅出边条件不落变量（route_audit 钉快照）；
命中推进经 NodeBehavior.advances_on_message 标志中介（宿主纯订阅仅关联）；到时=timer_fired 兑现+订阅置
elapsed 留痕；on_cancel 钩子全仓首接线（cancel_token→关订阅删 Job，竞速收尾/终止统一覆盖）；暂停=全动作
拦截（命中落缓冲、恢复 retro_poll 补消费）；配套面 correlate_message（同步签收凭证/幂等回放/不缓冲）+
waiting 反查+CLI message waiting；零新表零列。周期触发明确不做（调度域）。拆分：wait_node.py/tasks/wait.py/
message_flow.py+message_query.py 守 300 行红线。多事件竞速=并行+any 汇聚图组合（无事件网关节点）。
测试 21 例（semantics/test_wait_node.py）；suite 占位批时钟闭包修复后转绿。

**公开面+插件位结构批（2026-10-09，结构评审落地三件套：立公开面/插件位/反向纪律）**：
① `flowork/api.py` 唯一公开门面（66 符号冻结：引擎四服务+消息/Job worker 面/定义
DSL/校验/试算/CEL 值求值/词汇目录/SPI 端口/错误体系/返回模型/OrgDirectoryLike）；
快照契约 `tests/test_api_surface.py` 守卫（增删=显式动作）；内核重排（spec §15）
只准动内部件，公开面路径与符号名冻结——宿主与 AI 的稳定记忆锚点。
② web 迁入插件位 `flowork/contrib/web`（contrib 升格=官方插件包：词汇插件+web
传输面，rest/mcp 进驻后同列）；uvicorn 入口/全部文档引用同步；启动命令
`uvicorn flowork.contrib.web.app:app`。
③ 反向纪律双保险：import-linter 新契约「内核不得 import 投影/插件宿主面（cli、
contrib.web）」+ 静态纪律测试 `tests/test_projection_surface.py`「投影/插件对内核
的直接 import 只准经 flowork.api」（forbidden 契约按传递闭包判定、api re-export
出边必然穿透，正向规则只能静态扫描）；cli/web/examples/contrib 全量换绑 api
（含 cli demo 与 web 试算两处函数内隐藏深路径）；契约一 source 补漏 directory。
目录残留债不动：engine 巨包拆分/tasks 改名/validator 子包化/orm_v2 收口——
随 Alembic 后重排刀（spec §15）一批处理。

**测试方案落地批 A·地基（2026-10-09，依据 docs_research/flowork测试方案-2026-10.md；
与 对标产品-delta 批并行，工作区分离）**：
① pytest marker 体系（slow/quarantined/randomized/needs_http 注册 + --strict-markers，
addopts 默认排除后三者——重档测试显式开门，防红灯失去含义）。
② 覆盖审计第四重「引擎→矩阵」反向（test_node_features_cover_engine_kinds：
registry.kinds() 每个 kind 必须有 node.<kind> 条目；test_builtin_kinds_registered
改 KIND_* 常量集合相等——新增 kind 不入册即红灯）。
③ pilot 走查层并入矩阵：审计扫描扩至 suite+pilot 两目录，30 个 pilot 测试补
@covers（新增 subprocess.{input,output}_mapping/child_reject/child_recover、
fault.admin_move_token、version.instance_snapshot 六条目），
pilot 三处白名单外 import 合规化（ProcessInstanceRow→runtime.variables_of）。
④ 悬空检查 helper `tests/suite/invariants.py::assert_no_dangling`（显式调用不
autouse，exclude 豁免有意留运行态的实例），接入场景随机两测试。
⑤ 覆盖率口径改「照明指标，不作提交门禁」+ 回归测试带来源纪律入提交规范。
矩阵自此覆盖 tests/suite 与 tests/pilot 全部走查（678 测试全绿）。

**对标产品 对标两轮+第一批落地（2026-10-09，spec v2.3 第八轮）**：
① 对标三篇入 docs/product/kernel-v2/：对标明细.md（A/B/C 三档速览+四拍板项，行业列
✅⚠❌）、对标明细详设.md（A1–A11 逐项详设，行业修正版）、delta-industry-benchmark.md
（Camunda 7.23/8、Flowable 7、jBPM 7.74、Temporal、SpiffWorkflow、Conductor 源码级对拍）。
**行业对拍撤销 A7 前视跳过**（全行业零实现+语义不可判定+失效=审批链空洞，列 spec 非目标）、
**推翻 A5 双日历**（全行业历史耗时一律墙钟、零引擎按人算）、修正 A1（state×task_type 动作表
而非节点级配置）/A8（advisory 子任务而非 assignee 轮转）/A11（默认 WARNING+strict 可选+补 L3
同单元引用层）；A9 契约按 Zeebe 定稿、A10 拆令牌模拟/打桩两层。**四语义拍板落 spec §5.1**：
加签各占一票（不占票走 assist/consult）/转办票随人走/驳回复活圈定最近一轮（与主流"重入重解析"
的刻意差异同步声明）/撤回取消范围随 A2 闸门。范围变更记《本期建设边界》backlog。
② 第一批实施（A3+A4+A6 护栏+竞态窗修复）：驳回复活按 token_no 最大已完结令牌圈定（多轮驳回
名单不膨胀）；协办随主任务 complete/reject 同事务收口（outcome=assist_cancel+事件）+ assist/
加签 root_task_id 断链修复（此前仅 transfer 写入）；EngineConfig.max_participants=1000 护栏
（解析位=ParticipantLimitError 挂起+故障单、加签位=请求拒绝整笔回滚，绝不截断）；**双 outcome
竞态窗修复**（tests/edge submit vs timer 实证的双 agree：pysqlite SELECT 自动提交语义下「读任务
→读实例」间隙对手提交会绕过版本守卫——新增 _claim_task_action 咽喉：touch+flush 立即以版本
守卫抢占实例行，再 expire 重读任务行复验，ObjectDeletedError 归一 TaskNotAvailableError；接线
_assert_operable/_complete/_reject 三点）。678 测试全绿。第二批（A2 撤回守卫+A1 动作表+A5 端口
收口）待启动。

**对标第二批落地（2026-10-09，spec v2.3 第九轮；与等待节点 WIP 并行会话同工作区，文件互不重叠）**：
① A2 撤回下游闸门：`downstream_basin`（graph.py，upstream_basin 对偶）界定本节点正向可达
下游；人工进展＝非取消已办 ∪ 工作节点（user_task/call_activity）已完结令牌——穿透节点
（branch/cc_task）即时完结不拦（对齐 对标产品 穿透 CONNECTION/CIRCULATE；**实现即踩坑**：
首版把 branch 的即时完结当进展，pilot leave 撤回被误拦，修正后判据只认工作节点）。下游有
进展→拒绝并提示改用驳回（错误带进展明细）；闸门生效后"取消全部未完令牌"与"只取消下游"
等价（拍板④自然覆盖）。withdraw 归类实例修改类动作（授权归宿主）。
② A1 动作表+available_actions：`tasks/actions.py` 按 (state×task_type×认领态) 声明动作可用性
（jBPM 操作表形态，非节点级配置）；TaskInfo.available_actions 服务端算完下发（主流引擎均无
此面——AI/前端拿到任务即知能做什么）；入口守卫接线 _assert_operable(action=...)/_complete/
_reject；分组 work/dispatch（Camunda 8 动词切分）。
③ A5 WorkCalendar 端口删除（死端口收口第三例）：spi/api/快照测试三处同步；spec §5.1 定稿
after_minutes 恒自然分钟、§9 端口清单 3→2、§3.1/附录 C 残留引用清理；将来要工作日超时按
Flowable 形态重立（节点级具名日历+注册表+发布期校验）。
测试：tests/semantics/test_delta_batch2.py 9 例。699 passed（剔除并行 WIP 的 2 例 stale
registry/world_catalog 与其 3 个未完成测试文件——builtin 已注册 wait kind 而常量清单未更新，
属该会话待办，与本批无关）。五道门（范围内）全绿。

**测试方案落地批 B·构造矩阵（2026-10-09，批次 A 同日续）**：
① 子流程构造族（suite_call_matrix/child/grand 八维度）+ 引擎修复：sub_process_finished
原只挡 running，paused/suspended 子被当已结算——改终态集合判定（当前唤醒机制下原缺陷
不可达，防御性对齐 docstring）。
② 分流构造族（suite_branch_matrix 七情形+混合票）+ `sequence_audit.py` 留痕序列断言
帮手（route 逐边 result/variable 血缘/event 序列；形状实测钉死：edges 只含条件边、
默认边不进 edges、无条件边落空行）。
③ 会签×驳回×时限耦合世界（suite_countersign_knot 六走查）+ 引擎修复：转办/改派
克隆任务行后未触发 timer Job 丢失（被移交人无限时窗）——carry_deadline_jobs 档位
跟随等待实体重绑（run_at 不变，原 Job done+carried_to）；改派走查改钉新语义
（若产品要「改派重置时限」回退 f917669 即可）。
④ 运行形态参数化（tests/edge/test_shapes.py 四形态：内存/文件/跨副本触发/双 tick CAS）。
⑤ wait kind 入册（node.wait + suite_wait_duration + xfail 占位，行为接线后摘除）；
test_builtin_kinds_registered 显式补 KIND_WAIT。
发现挂账：route_audit snapshot.now 取 utc_now 而非 EngineConfig.now（FakeClock 下
审计时刻与任务时刻不一致，待修）；父 terminate 不级联子实例（孤儿运行，开放问题
已在 test_construct_subprocess 钉住）。

**测试方案落地批 C·随机结构（2026-10-09，方案三批完结）**：
`tests/suite/random_gen.py` 随机流程结构生成器（Zeebe RandomProcessGenerator 的
flowork 版）：有界随机（序列≤5 块/2 条件臂+默认边），两段式「先决策结构再发射」；
执行计划=(结构,n) 纯函数，驳回注入=换 reject+补 start 重报+全量重放（只落单人
多任务）；生成闸 assert_valid（非法=生成器 bug）。`test_random_structure.py`
（randomized marker 默认排除，FLOWORK_RANDOM_ITER 默认 30）：跨结构不变量
（无悬空/历史无重复/条件路由行必有择道/子实例一致/映射回传）+失败三段日志
（种子+定义 JSON+计划卡点，generate(seed) 即复现）。DoD 实验：临时改坏
join_gate → seed=20261014 抓到活跃令牌未收口并可复现，还原复绿；100 迭代全绿。
至此三批 12 提交全部落地：构造层穷尽（矩阵双向锁定）+组合层随机（结构+执行
双随机可复现）+过程层可审计（三留痕表序列断言）+多形态覆盖（四形态参数化）。

**对标第三批 A8 落地+A10/A11 设计稿（2026-10-09，spec v2.3 第十轮；A11/A10 主落点被并行
「等待节点」WIP 占用——validator*/behaviors.py，先出设计稿待其落地后动工）**：
① A8 consult 咨询实装（engine/task_consult.py，Mixin 模式同 task_cc）：「先收集意见、原办理人
最后拍板」两轮形态＝派生 task_type=consult 意见任务（独立工作项，不建 deadline/不进会签计数
——**意见不占票**，outcome=agree/reject 由 type 区分）；**主任务软挂起**＝同链（root 链，转办
保持）存在未完 consult → 主任务全动作被拦（守卫内联 _claim_task_action 咽喉，覆盖
complete/reject/transfer/assist/加签；**MRO 教训**：Mixin 桩模式要求提供方先于消费方桩，
TaskConsultMixin 在后其守卫被桩遮蔽——守卫改内联自含查询，不跨 Mixin 派发）；意见全交回自动
解禁+task_consult_finished 事件；主任务 deadline 时钟照走。动作表：已认领+consult、
consult 任务→complete_consult。行业修正落地：不抄 C7 delegation assignee 轮转。
② A10 设计稿 simulation-dryrun-design.md（拆令牌模拟/打桩真执行两层；service_task 必须 stub；
模拟实例一等公民=行业空白；报告复用 route_audit+stub 清单）。A11 设计稿
deploy-validation-design.md（四层分级：L3 同发布单元引用+handler 注册=ERROR 新增、L4 目录
实体存在性=默认 WARNING+strict 可选；subject_exists 能力参数沿用 org_configured 先例；
validator 端口做成部署期扩展点）。测试 test_delta_batch3.py 5 例。

**对标第三批 A11 落地（2026-10-09，spec v2.3 第十一轮；等待节点批已释放 validator 落点）**：
部署期引用校验 L3+L4（definitions/validator_deploy.py——与纯设计时 validator*.py 分居，
这两层要查库/查注册表/查目录，只由 deploy 事务内调用）：① L3 call_activity 引入 `binding`
字段（latest 缺省=运行期解析最新版、目标未发布仅 WARNING，先子后父合法；deployment=部署时
必须已发布 ERROR，Zeebe bindingType 同义；自引用合法跳过）；service_task handler 未注册=
WARNING（**实施修订**：先 deploy 后 register 是既有合法模式，strict 升 ERROR）。② L4 取人位
CEL 字面量经 subject_exists 钩子（duck-typing 探测 org 丰富面，不动 SPI 四方法）：False=
WARNING、strict ERROR、None=零噪音跳过。③ EngineConfig.strict_deploy_validation=False 缺省
——L3/L4 默认 WARNING 留痕（可见优于静默），True 升 ERROR 拦发布；string_literals（lark
STRING_LIT）宿于 validator_deploy.py（cel.py 行数红线）。字面量提取踩坑入档：builder 产的
节点是 FlowNode extra 袋（未传字段不在袋），一切取人位访问须 getattr（同 iter_expression_
positions 惯例）。A10 设计稿就绪待下一批（落点已释放）。测试 9 例；范围内 738 全绿。

**对标第三批 A10 落地（2026-10-09，spec v2.3 第十二轮——模拟试跑收口，第三批完结）**：
模拟实例（simulated）第二层实装：① start_process(simulated=)/RuntimeService.simulate()→
SimulationReport（终态+令牌时间线+stub 清单+卡点+路由轨迹）；实例行 simulated 列（Alembic
0002）。② 行为分支五点——**kind 知识住行为**：NodeBehavior.performs_external_work 类属性
声明外部副作用（kind 守卫测试曾拦下 simulation.py 的 kind 分支，改为行为契约）；user_task
进入即完成（approval_exit=simulated，不走 auto_pass 票数）、service_task 恒 stub（绝不执行
handler）、cc 不建传阅、call 递归传播、wait 冻结为卡点（不订阅不建 Job）。③ 隔离（行业
空白一等公民化）：CommandContext.suppress_events——事件不投 outbox（试跑不惊动订阅方；
route_decision 行不受影响=报告数据源）；不建任何 Job；history.instances() 默认排除模拟。
④ 报告 F2 现查模式（不预存）；执行错直抛试跑调用方（模拟失败=定义会挂）。真实路径零影响
（回归断言）。测试 6 例；范围内 744 全绿。**第三批（A8/A10/A11）就此完结**；剩第四批 A9
多实例子流程（大件）。

**对标第四批 A9 落地（2026-10-09，spec v2.3 第十三轮——多实例子流程，对标系列完结）**：
Zeebe inputCollection/outputCollection 契约（multi-instance-call-design.md）：① CallActivitySpec
.multi_instance（definitions/call_instance.py 拆分守行数）——subjects 取人表达式每人一子实例
（parent_index 列记迭代序，迁移 0003 幂等化：基线 create_all 新库已含列则跳过，batch 多列
追加的伪循环依赖实证后改 op.add_column+存在性守卫）；输入映射按子求值注入 `subject`
（catalog@mapping_input）。② completion 公式（child_total/completed/failed 三计数，
catalog@completion 新位点+iter_expression_positions 接入）只做提前收工——命中终止剩余
running 子（撤令牌待办对齐 terminate）；child_failed 表达「一子失败即阻断/忽略失败」；
不做失败机制（C7 教训）。③ collect=index 对齐聚合（失败位 null 占位、完成序无关）；
merge 仅单子（validator 拦 MI+merge=last-wins C7 痛点）。④ subprocess_mi.py 拆分；
builder extra 袋 dict 归一 _mi_config。测试 11 例；范围内 766 全绿。**对标四批全部完结**。

**收口批：时钟统一+终止级联（2026-10-09，spec v2.3 第十五轮；提交 541369c/de8bb03）**：
① 时钟统一——盘点发现 8 个表达式装配位点（路由/取人/会签出口/改派/子流程映射/MI completion/
消息 correlation 两处）不传 now（expression_context 缺省墙钟）+ worker Job 三处（create run_at/
领活租约/失败退避）直用 utc_now：FakeClock 下被测对象与测试时钟不同源（route_audit 挂账
「审计时刻与任务时刻不一致」同根）。修复＝evaluation_variables/resolve_subjects/exit_reached
增 now 参数、十三调用点显式穿针 config.now（缺省墙钟保留给无引擎装配面：dry-run/web 试算）；
test_clock_unification.py 八位点「假钟阈值」判例（实施前 8/8 红）。② 终止/取消＝砍树——
terminate/cancel/实例时限 cancel/terminate 三入口共用 terminate_cascade：整树撤未完令牌→
未结算子实例（含暂停/挂起中）子先父后落终态（顺序契约：先撤光令牌再 complete，子终态不误
唤醒中间层父）；父事件带 cascaded_instances、子事件带 cascade_from；MI 提前收工不走此语义
（完成侧提前下班，暂停子属管理员域）。翻转 test_construct_subprocess 的孤儿钉子；
test_terminate_cascade.py 六走查。**与 escalate 批（ab0b6de）同日并行会话共享工作区**：
本批提交信息误写「第十四轮」（escalate 批先入登记），spec 附录已归正为第十五轮；共享区
协作纪律＝提交前用 write-tree 临时 worktree 验证暂存集（主树常混并行 WIP）。

**候选认领制收口批（2026-10-09，A 批三缺口修复 + 候选槽表达式对齐 B 批；spec §5.5）**：
候选制此前是半成品——① 配了组织目录即不可用（取人解析兜底先抛错，认领分支不可达，
生产必踩）；② 候选组 role:/org: 引用认领时不展开（is_member 端口零引擎调用方）；
③ 「我可认领的待办」查询维度缺失（候选关联不经任何公开面暴露）。
**A 批三修**：resolve_participants 改三段判定（指派命中直派→候选槽求值→求值空才回兜底，
候选槽声明即不走 to_admins）；新建 `tasks/candidates.py`（candidate_hit 两式判定＝
人 key 对齐（user: 前缀互认）∪ 目录 is_member 组归属；require_candidate 认领守卫
（缺目录且候选含组引用时点明原因）；candidate_task_ids 查询维度；认领与查询共用防漂移）；
`list_tasks(candidate=)` + CLI `flowork task claimable <user> --org <yaml>`（CLI 无目录时
组候选恒不命中，--org 是唯一接入途径；不给则如实报错）；`assignee=""` 收紧为显式
未认领语义（原空串假值不筛）。**组引用保留不展开（spec §5.5 三处明文）**——决定性测试
test_group_reference_stays_live_after_join（DirectoryStore 建任务后 assign_role 入组→可认领；
「建任务时展开定格」方案在此必红）。**B 批（候选槽接入 CEL，另一会话实施、本批合提交）**：
candidate_users/candidate_groups 每项=公式（resolve_candidate_subjects 与取人位同一求值面；
裸名→设计期 CEL 错，零兼容）；ParticipantResolution 增 candidates 槽；位点拆出
validator_positions.py（iter_expression_positions 收录 candidates kind、lookup_kind 归一
subjects 防 catalog 误报；validator_conditions 守 300 行）；returns 白名单按 kind 分
（候选槽多接受 groups/mixed＝「混合集自动拆槽」）；validator_rules 的组前缀判据改经
string_literals（CEL 化后裸前缀失配）；测试字面量迁移（m3/m4/delta2/edge/spi 六处加引号）。
**测试**：tests/semantics/test_candidate_claim.py 14 例（配目录可用/组展开/前缀互认/
无目录提示/查询维度/入组活性）+ suite test_candidates.py + CLI 2 例；docs/API.md 与
integration.md 的 list_tasks/task claimable 行随 escalate 批先行入库。860 测试全绿。
