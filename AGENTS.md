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
├── spi/           端口协议：ExpressionFunctions（表达式函数注册）；schema 数据类
│                  （FunctionSignature/ExpressionFunction）；目录面在 participants
├── events/        领域事件类型（M1 起随 Command 管线落 outbox）
├── tasks/ participants/ persistence/ engine/ jobs/   # 内核主体（engine 已按职责拆分多文件）
├── directory/     目录（M-A）：组织/角色/用户/Agent/委托（agents/roles/delegation/
│                  queries 读缓存/audit 写审计/store/orm）
├── cli/           CLI 投影面（运维+用户面）——只准走 flowork.api（静态纪律测试守卫）
├── contrib/       官方插件包：词汇插件(cel/numeric) + 三传输面（web/ 设计器内部面、
│                  rest/ 对外 /v2 集成面、mcp/ AI 19 工具面）——全经 flowork.api
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

1. **命名**：禁用对标产品遗留命名模式
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
- 仓库外（同级目录，**不在本仓库内**）：边界（范围权威）→ 实施规划 → 命名规范 →
  节点抽象与扩展设计 → AI友好设计 → 主流流程引擎借鉴分析；
  语义参考为行为基准基准源码 基准材料/（clean-room：只对齐行为，不搬代码/命名）。

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
与对标批并行，工作区分离）**：
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

**行业对标两轮+第一批落地（2026-10-09，spec v2.3 第八轮）**：
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
（branch/cc_task）即时完结不拦（对齐行业穿透节点不拦口径；**实现即踩坑**：
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
增 now 参数、十四调用点显式穿针 config.now（含 Review 补漏的驳回侧 disapprove_exit；
缺省墙钟保留给无引擎装配面：dry-run/web 试算）；
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

**B 批收口（2026-10-10，e586fb2 补漏——设计稿 candidate-expression-design.md 全项落地）**：
① validator_conditions 删本地 `iter_expression_positions` 副本改导入 validator_positions
（唯一清单）——合提交时本地旧清单漏掉候选位**语法检查**（裸 role:legal 不报错），两份
位点清单必然漂移，去重即修复；② L4 补 candidate_groups 进取人位清单且**组引用跳过**
存在性检查（role:/org: 前缀与候选组字段字面量都是组语义，问「是不是人」必误报）——
设计稿 ④ 兑现；③ 新增 tests/semantics/test_candidate_expression.py 14 例（函数候选定格/
组引用保留/混合集同池/变量参与/空结果兜底 raise+to_admins/求值错=挂起+故障单（两段式
流程——首节点发起即失败整笔回滚不落实例，既有命令边界语义）/规模护栏/裸名语法错/
词汇位点归一（prev_task·node() 可用、task.* 不可用）/returns 白名单两翼/L4 组跳过）；
④ 配套：catalog docstring 补 candidates 位点、设计稿状态改「已实施」+三处实施修订、
设计器标签改「候选用户(公式)」、字段 description 下发写法占位提示。875 测试全绿，
五道门全过。

**Agent 域整体移除（2026-10-10，spec v2.3 第十六+十七轮，提交 322b47c+后续）**：
用户裁定「引擎对 agent 没有任何审核与治理的职能」——AI 与人行为同构，引擎不该也
无法区分主体类型。第十六轮先删 `agent:` 前缀写法（主体统一裸 id）；第十七轮连根移除：
① 目录 Agent 域全量（org_agent/org_agent_credential 两表、注册/状态机/凭证/owner、
manager_of 的 agent→owner 特例——manager_of 回纯人语义）；② 引擎归因链（complete/
reject/update_variables 的 as_agent 参数、两表 acted_as_agent 列、CLI --as-agent、
variables_updated 事件字段）；③ 迁移 0004 删表删列（downgrade 不提供）；④ 测试净删
17 例。**保留**：AI 仍是合法主体（裸 id 可作 assignee/候选人，领办与人同路）；user:
前缀互认保留。**归因口径**：只有 acted_by 字符串——「这步是不是 AI 办的」不再是引擎
可回答的问题，归宿主身份系统。engine-v1 01/02 的 Agent 身份与 agent_credential 设计
随批废止（权限模型 L1 收为 user_token/api_key 两类）。858 测试全绿。

**取人语义收口批（2026-10-10，spec v2.3 第十八轮；四问走查裁定的落地）**：
① 单人节点多人＝错误——未声明 `multi_instance` 而解析出多名 todo →
`ParticipantCountMismatchError`（kernel.py 错误族第四例；守卫住 `Kernel.resolve_participants`
咽喉，task_records.py；挂起+故障单、首节点整笔回滚；宽松并行退役，skipped 不计多人，
串行重解析不受影响）。② 人员位组引用＝设计期 ERROR——validator 规则
`participant.group_in_subjects`（validator_rules.py，iter_expression_positions 遍历
subjects/reassign 位 + string_literals 字面量提取）：`role:`/`org:` 字面量发布期拦，
候选槽合法、`user:`/裸 id 不拦；运行期不做前缀特判（裸 id 铁律）——兑现 §5.5
规范化条款既有承诺（此前代码未实现）。③ contrib 补 `users_in_org(string)→users`
（subjects_in("org:…") 部门直接成员；取人位按部门展开唯一写法；四词汇）。
④ `max_participants` 缺省 1000→None（内核不替宿主选数值；连带修出 task_rework
`>` 直比较的 None 实缺陷）。组引用路线经行业对拍定案：无引擎在 assignee 位隐式
展开（BPMN=候选池认领制、SaaS=创建时展开），取「设计期报错」+错误消息指向两条
正路（candidate_groups / users_with_role·users_in_org）。**测试**：
`tests/semantics/test_participant_strict.py` 13 例 + 宽松夹具 13 文件补显式
`multi_instance`（world_flows_mix 阶梯/路由世界、lifecycle、m3、m4、delta_batch1、
numeric、edge_stress、roles、catalog/spi 签名断言）。871 测试全绿。

**表决行统一口径批（2026-10-10，spec v2.3 第十九轮；转办通知排查走查扩成全库审计的落地）**：
「谁真正办过这一票」谓词全库缺失——六个名单类消费方清一色 `outcome != "cancelled"`，
转办/协办/咨询/自动通过行渗入名单（六例实测：单人节点挂起/会签应办数膨胀违背票随人走/
prev_task 读到协办收口行**改变路由**/auto_pass 策略**静默代投**/串行会签**无声死锁**/撤回误拦）。
① §5.1 表决行条款：表决行＝审批类任务+agree/reject；轮次消耗行（串行会签专用）＝审批类且
已终结（transfer/auto_pass 算消耗不重派）。两裁定（用户拍板「都收紧」）：auto_pass 不算表决
（一次代投不滚终身代投）；转办人不算已参与（票由被转办人代行，多问一次人优于替人投一票）。
② `flowork/tasks/vote.py` 单一谓词（vote_criteria/consumed_turn_criteria，SQL 下推）统接六位点：
驳回复活名单（reject_flow）、prev_task·node() 判断面（facts）、auto_pass_participated/previous
（resolver 两收集器）、串行会签下一棒（task_records.handled_assignees——轮次消耗口径）、
撤回进展判据①（task_rework）。③ validator 新规则 `user_task.exit.misplaced`（走查顺带发现
的不同族问题）：approve_exit/disapprove_exit 写节点顶层被 extra 袋静默收下、行为只读
multi_instance 内字段——出口公式零告警失效静默回退全票，设计期 ERROR 指路。
④ 安全面确认：会签票数三计数（TokenRow）不受污染；wf_route_decision 历史 snapshot 不迁移
（如实记录当时计算值）。**测试**：`tests/semantics/test_outcome_semantics.py` 16 例
（行种类×消费动作矩阵+对照组防收紧过头+无声死锁钉子）+ test_validator 2 例错位字段。
**891 测试全绿，五道门全过。**

**初学者问答审计+第二十轮修订批（2026-10-10，D1–D6 拍板落地；四笔提交 78dbdf9→ab97289+收口）**：
审计方法＝31 个基础语义问题只从代码作答（四路并行探查+五处人工复核），四分类：
17 安心/9 缺条款/5 未定义（D1–D5）/3 矛盾（C1/C3 已修 90e1954）；审计表在
docs_research/语义问答审计-2026-10.md（详版供排版）。修订四笔：
① D1 迟到到达吸收——any 竞速收尾扩为 graph.racing_basin（直接前驱恒计入、
途经其他多入边节点即止）；旧「直接前驱集合」对两跳长臂漏取消，完结节点被
迟到到达二次激活重跑。全量回归逼出两轮判定修正（结构可达误杀环回等待臂/
剪直接前驱丢旧收尾语义）；tests/semantics/test_late_arrival.py 3 例。
② D2 暂停拦截面——finish/move-to-end 拒 paused（终态只从运行态走）、
update_variables 拒 paused（冻结快照；修数据＝resume→改→再 pause）、管理手术
明文允许不改暂停态；cancel/terminate 不拦；test_pause_admin_surface.py 5×矩阵。
③ D4 出口不可达兜底——advance_after_task 尾部守卫（迁 TaskRecordsMixin）：
全部待办办结而两侧出口未达成→挂起+故障单（绝对门槛/混合票不再静默卡死）；
WARNING exit.bare_threshold（lark AST 判裸数字门槛，树形 relation(2子)=
[左包装,右操作数]）；D3 加签现时名单条款化+钉子。**顺带修竞态残留窗**：
对手在 claim-flush 后提交删行→ObjectDeletedError 命令边界归一
（ops._run_once）；根目录 designer.db* 残留（带 WAL）会让全量套件竞态测试
时红——同样代码移走即绿（环境干扰非代码问题）。
④ D6-b 收紧——validator_references 删空声明豁免（零声明图纸引用非保留
标识符即 ERROR）；盲区实证＝ai_review_demo 裸 assignee 藏豁免后；examples
七模板+13 测试文件补声明（纯增量）；m4 裸 id 夹具升级为设计期 InvalidDefinition；
wait correlation 的 payload.* 维持设计期 ERROR（审计期误判已回退）。D6-a
binding 不锁版本消歧（文案+§2.5）；D5 candidate 默认含已认领（API.md）；
ProcessVariable 补入 api 公开面（快照同步）。
spec：§2.3 竞速臂判定/§7 暂停拦截范围/§4.1 出口不可达/§5.1 加签现时名单/
§2.5 binding/C.4 空声明口径+附录第二十轮（含乙类 B1–B9 九条随批条款化）。
**905 测试全绿（+14 净增），五道门全过。**

**设计器插件计划+审计数据面批 A（2026-10-11，计划=docs/product/designer-plugin-plan.md）**：
设计器定位拍板＝**开发人员的查看与审计插件**（npm 可嵌入、宿主默认只读、编辑留开发模式；
产品分工=设计器审计面×任务页办理面；范围变更记边界 backlog）。四批主线 A 审计数据面→
B 审计呈现→C 插件化→D 支撑面；review 修订五处（模拟限发布版=顺序矛盾→拍板翻转、A3 大半
已存在、300 行、readonly 取数洞、A2 对齐 HistoryService 方法表+命名抄 spec §12）。
批 A 落地：① 内核草稿直通——RuntimeService.simulate 增 spec 入参（不查库不落定义表、
版本列草稿占位 (0,0)、隔离语义全沿用；start_process 公开面**不**开放 spec——真实实例
仍只能来自已发布定义），**证据先于发布**（用户拍板「只对已发布流程模拟没意义」）；
② web 新模块 contrib/web/audit_routes.py（preview_routes 先例，app.py 231 行守红线）
九端点：simulate（body 带 spec 缺省取已保存草稿/模拟前强制设计期校验 ERROR 拦 422/
?major=&revision= 发布版对照）/instances 列表（默认隔离模拟实例——写明防误报 bug）·
详情（实例+令牌+变量）·timeline（trace_instance）·route-decisions·variable-history·
tasks（paged_tasks）/versions 列表+按版本取发布 spec（spec §12 同义，C2 readonly 数据
前提）；③ publish 响应补 triggered_rules（破坏性明细——breaking 只是布尔）+identical_to
（幂等去重）；④ designer_drafts 增 published_versions（新→旧）。测试 +14（内核 5+web 9）。
**966 测试全绿，五道门全过。**

**设计器插件批 B/C/D 落地（2026-10-11 同日续，审计呈现+插件化+支撑面收官）**：
**批 B 审计呈现**：试运行双 Tab（路由预演保留+完整模拟——引擎真跑草稿，终态/时间线/
桩清单/卡点/路由轨迹，主场景=当前草稿含未保存编辑）；发布确认呈现判定全貌
（triggered_rules 明细+identical_to 幂等去重提示实测）；实例审计视图（顶栏「实例」→
列表按状态过滤+模拟隔离勾选 → 详情概要+令牌链+三面板时间线/路由决策/变量历史）；
发布编排移入对话框本地（useStore.setState 直改，不触 store 并行在途改动）。
**批 C 插件化**：C3=`tests/test_web_api_surface.py` 契约冻结快照（只冻 /api 面——
/designer-demo 宿主便利面不入契约）；C4=迁移 0005 草稿表独立 layout 列+读写拆合
（content 只存语义/旧行惰性迁移/发布合回既有行为）+**顺带修 import 副作用**（web 默认
应用改模块 `__getattr__` 惰性创建——app.py 底部裸 `app = create_app()` 在旧库缺新列时
炸 collect 的教训：模块级建引擎=import 时触库）；C2=`src/config.ts` 配置面
（apiBase/mode/definitionCode）+只读模式（编辑动作/调色板/属性面板隐藏、画布
不可拖连、快捷键守卫）；C1=vite lib 构建（**React CJS 包裹层 process.env.NODE_ENV
必须显式 define——lib 模式不隐式替换，浏览器裸 process ReferenceError**）+embed.tsx
挂载入口+引擎可选托管（dist-lib 存在即挂 /designer-lib+示例页 /designer-demo）。
**批 D 支撑面**：`GET /api/handlers`+`GET /api/directory/summary`（duck-typing 探测
静态目录，未配置降级；`FLOWORK_ORG=org.yaml` 装配）+前端 options 建议缓存
（用户建议带 CEL 引号+部门/角色翻译；candidate_groups 建议 role:/org: 合法组引用）
+SchemaForm 取人/动作字段 datalist。**D3 公式中文投影未做**（计划既定可选降级项）。
**971 测试全绿，五道门全过**；批 B/只读嵌入页浏览器实测（模拟/实例/发布判定/
readonly demo 四场景）。

**公开面死符号收口批（2026-10-12）**：盘点 testsuite 对 flowork.api 全部符号的消费
（AST 绑定感知扫描，区分经门面/深路径/文本提及），发现 5 个全仓零引用符号：
① `Clock`/`EventSink` 两死协议删除（时钟实际由 `EngineConfig.now` 裸 Callable 承载、
全仓 FakeClock 十二处一致注入；outbox 消费者接收裸 sink 回调）；② `NodeLifecycle`
枚举删除（生命周期阶段表达为 on_enter/try_complete 方法调用序列，枚举零引用）；
③ `InstanceState` 保留并**接线**——内核全部实例状态字符串字面量改枚举成员，并抽出
四个语义常量（`TERMINAL_INSTANCE_STATES`/`FROZEN_INSTANCE_STATES`/
`UNSETTLED_INSTANCE_STATES`/`FAILED_INSTANCE_STATES`，与既有 `_CHILD_SETTLED_STATES`
等本地集合归一），StrEnum 与字符串/Pydantic/SQL 全互通，零行为变化；
④ `spi.ports.OrgDirectory` 四方法协议删除（与 `participants.OrgDirectoryLike` 概念
重复且零消费——引擎消费面只有 OrgDirectoryLike，公开面同名歧义消除）。
公开面 66→62。新增守卫 `tests/test_api_dead_symbols.py`：公开面符号全仓零引用即
红灯，两类豁免须显式登记理由（内部构造返回模型 FunctionEntry/JobClaim/NotFoundError、
门面契约常量）——与死端口收口（Notifier/ResourceProvider/WorkCalendar/VariableStore）
同口径的第五批。spec §9/kernel-v2-plan/integration.md/包地图同步。


**开放面插件批（2026-10-12，REST /v2 + MCP 18 工具落地；设计=openface-plugin-design.md）**：
① `flowork/contrib/rest`（/v2 对外集成面，spec §12 全量+一处补缺）：七搜索面（游标=offset
base64；before 明示 422）+ 实例四态动作 + 七任务动作 + 变量三端点 + anticipate +
messages/jobs（activate 长轮询）+ **实例级 signal=回执核验式关联**（内核无实例域 signal，
按本实例 open 订阅 (name,key) correlate、回执 instance_id 必须相等，409 防串单）；
错误体 {"error":{code,message,details}}，状态映射 isinstance 链+类名兜底表（未导出错误家族
按名映射，不为错误扩公开面）；actor=X-Flowork-Actor 头（鉴权归宿主，既定裁定）；装配两形态
create_app()/register_rest_routes(app, engine)。② `flowork/contrib/mcp`（18 工具，spec §13）：
catalog 纯 dispatch（传输无关可直测）+ lowlevel Server 传输壳 + stdio 入口
（`python -m flowork.contrib.mcp.server`；依赖钉 mcp>=1.10.1,<2——2.x 换构造器注册 API，
本批取 1.x 稳定线）。③ 公开面扩一点：`flowork.api.API_VERSION="1"`（门面契约版本；
插件 REQUIRES_FLOWORK_API ≤ 当前值，兼容声明测试钉住）；import-linter 契约二 forbidden
扩为全量 contrib（api 不 import 任何插件）。④ 内核唯一改动：`JobService.list_jobs`
（只读查询，/v2/jobs/search 数据源）。快照三件（REST 路由表/MCP 工具名/公开面）+ 行为两件
（REST 全链 14 例/MCP dispatch 10 例）。**1020 测试全绿，五道门全过。**
挂账：tasks 三过滤维（flow_code/priority/due_before，待 list_tasks 扩参）、完整模拟 MCP 工具。
（DirectoryStore 与事件消费帮手的公开面扩面已随 2026-10-13 收尾批 5 落地销账。）

**内核收尾批（2026-10-13，《内核完善与删减评估》七批落地；四拍板：钩子错误语义＝部署期拦+运行期挂故障单/On_fail 挂账/事件三空头处置/MCP 加 resume）**：
① 死符号清理（8 符号：is_vote_row/ACTION_GROUPS/memory_url/register_breaking_rule/_BINARY_OPERATORS/_start_child/instance_tokens/normal_flows——register_breaking_rule 未上公开面＝合规宿主不可达，按死端口先例删）。
② 声明式钩子接线——评估实证 ``FlowNode.hooks`` 被 pydantic 收下但引擎零读取（静默失效，设计器 FORM_EXCLUDE 还引导用户到 JSON Tab 手写）：新模块 ``engine/declared_hooks.py`` 统一咽喉（模拟跳过；未注册与 handler 异常＝EngineExecutionError 挂起+故障单）；三时机接线（on_enter 先于行为写变量供消费、on_exit 后于行为先于令牌落定、on_cancel 先于行为）；**on_take 一并归一**——此前裸 resolve 直调：未注册＝UnknownHandler 冒泡零留痕、模拟试跑真执行宿主 handler（A10 隔离漏）；L3 新规则 node.hooks/flow.on_take.handler_unknown（WARNING、strict 升 ERROR，先部署后注册仍合法）；spec §2.6 接线语义成文；tests/semantics/test_declared_hooks.py 10 例。
③ 深链递归归一——实证直通链 200 节点 start_process 抛裸 RecursionError（约 7 帧/节点）：ops._run_once 增 RecursionError 分支归一 EngineExecutionError（挂起+故障单；start 阶段回滚同「首节点失败」语义＝孤儿故障单留痕），迭代化根治仍挂重排刀。**环境怪癖入档：celpy.CELPy.__init__ 会把进程递归上限抬到 2500**——全量套件里 celpy 先加载则 300 节点压不爆，测试显式收紧上限确定性触发（test_deep_chain_guard.py）。
④ 事件枚举收编——评估实证 12+ 事件以裸字符串实发不在 DomainEvent（订阅方按官方枚举编程静默漏收咨询/撤回/加签/超时预警/管理员手术通知）：14 成员入册（26→40，含补发 incident_resolved），17 发射点换枚举（StrEnum 值不变零红）；resolve_incident 补发 incident_resolved（拍板③）；task_delegated/task_resolved 从 spec §10 划掉（与 task_transferred、assist/consult 事件语义重复）；record_event 签名面 DomainEvent|str——**实施修正：运行期不做值域拒绝**，ctx.emit 端口承载宿主自定义事件名（tests/pilot 的 payment_executed 实证），枚举只承诺引擎自发事件全集；守卫 tests/test_event_surface.py（AST 扫 flowork/ 的 record_event/emit 字符串实参必须∈枚举值集+值唯一）。
⑤ 公开面扩点 63→67（minor）：DirectoryStore/DirectoryError/OutboxConsumer/IdempotentSink——DB 版目录（directory/ 八文件）与事件消费帮手此前生产零消费、宿主走门面无路；冒烟钉 DirectoryStore(engine.session_factory)→engine.config.org 装配→候选认领跑通；REQUIRES_FLOWORK_API 补齐 contrib/web、contrib 词汇包、numeric 三处＋tests/test_plugin_compat.py 五断言；销 openface §4 挂账。
⑥ REST ``POST /v2/messages/search``（spec §12 搜索族最后族级缺口；buffer∪subscription 两源合并时间倒序，message_query.list_messages，_first_open_subscription 下沉换 message_service 300 行余量）＋ MCP **resume_instance 第 19 工具**（拍板④：pause 无 resume 不对称，AI 暂停后需人兜底）；快照/文案/spec §13/openface 六处同步。
⑦ 文档收口：spec §2.1「七种」→「八种」+kind 清单补 wait、§7 标题去过时「当前最大缺口」、§11 任务组补 consult/complete_consult/withdraw、§14 表补 wf_route_decision/wf_designer_draft、TimeoutPolicy 定为「零兼容哲学唯一明文例外」成文（extra=allow 下删字段会更糟）；engine-v1 八处过时表述加注（/api/v1→/v2、ParticipantFunctions 内置、agent_owner、8 工具）；benchmark 三篇结项注；**《engine-v1 空头承诺处置建议》草稿落 docs_research/**（business_object/审计哈希链/任务页/docker/webhook/CLI apply 六项各附量级估计与建议，待拍板记边界 backlog）。
新增挂账：on_fail 接线（含 NodeBehavior.on_fail 全仓零调用、TokenState.FAILED 零写入——需错误携带节点上下文，错误归因专题）。1039 测试全绿，五道门全过。

**事件面修复批（2026-10-13，事件 review 全项落地；四提交 1b57159/e3bad0b/214ee0d/本批，用户四拍板：全四批+received 三路+取消族全套+默认永久保留）**：
① **全序与时钟（review A1/A2）**——事件 id 从随机 UUID 改可排序串 `{usec:016x}{nonce:06x}{counter:010x}`（时间前缀+引擎 nonce+进程内计数，ProcessEngine.next_event_id），`ORDER BY (occurred_at, id)` 即事件全序；实证修前同命令微秒平局随机定序（start 节点「先退后进」，两种读取口径结果还互相不一致）；occurred_at 统一取 config.now（命令时刻，修前列默认墙钟——时钟分叉第三处落地）；history.events 补 id 次键；零迁移（id 语义仍唯一不透明）。tests/test_event_ordering.py 四判例（含冻结钟全序）。
② **信号目录补全（A3–A7）**——message_received 三路统一先发（立即关联/消息启动/入缓冲/correlate 入口，载荷带 message_id；修前仅缓冲分支发）；cancel_token 统一咽喉：cause 参数+逐任务 task_cancelled+节点级 node_cancelled{by=原因}（撤回/终态级联/竞速/驳回/MI 收工/故障跳过/管理员七路——修前撤回下游取消对订阅方完全失明）；auto_pass 四处归档统一发 task_auto_passed{reason}（strategy/exit_reached/node_rejected/admin_finish，新 tasks/auto_pass.py 咽喉）+create_task(emit_event=False) 抑制幻影 task_created（待办通知不再误报——修前策略命中者建了即归档还照发）；补 task_unclaimed/task_assist_finished；删 node_failed 死成员（全仓零发射，spec §10 首行清单同步）。枚举 40→43。tests/semantics/test_event_cancel_signals.py 15 例。
③ **载荷契约（B1–B4）+守卫（C5）**——task 族统一 activity_code/task_type（同名同形）；task_completed/rejected 带 comment（驳回理由进事件）；incident_raised 带 incident_id（与 resolved 可配对）；node_entered/exited 四发射点带 token_id；process_started 带 major/revision；sub_process_started 带 child_definition_code/parent_node；timeout_warned 带 task_id/activity_code；emit_message_event 形参 Any→DomainEvent（mypy 兜动态位点）+终态候选静态钉子。契约 tests/test_event_payload_contract.py 8 例钉死。死循环守卫自 kernel 迁 token_records（kernel 299 守红线）。
④ **口径（C1/C2/C3/C4/C6）**——purge_delivered 缺省 None=不清理（事件表默认即审计历史，显式传 retention_days 才删；修前默认 90 天静默删审计）；history_level=activity 白名单加 incident_ 前缀（故障告警不再被吞）；API.md/integration.md 记 remove_instance 删未投递事件（至少一次的明示例外）与消费并发口径（无 SKIP LOCKED，多副本去重业务侧兜底）；DomainEvent 上公开面（67→68，按枚举编程的订阅方入口）。spec §10 事件面修复批段成文。1008 测试全绿（默认套件实测），五道门全过。

**事件面复核批（2026-10-13 18:00 定时任务，review 复核+续修；九提交 fd5d8da→7381e84）**：
复核时点并行批已落地（e3bad0b/214ee0d/aa024a1），14 项发现 3 项半已被并行修掉，剩 11 项分六提交修完，
用户令「继续修复」后再三提交清挂账——**1085 测试全绿，五道门全过，三批红绿验证**：
① 载荷收尾（fd5d8da）：cancel_token 咽喉带 actor（task_cancelled/node_cancelled 与已办 acted_by 归因发起人，
修前一律 system——撤回/终止连带的下游取消归因丢失，六路调用方穿透）；克隆 task_created actor=发起人
（修前误记被指派人）+主路径补 actor=None 键集对齐；assist/consult/countersigner/escalated 补 activity_code；
withdrawn 两分支键集统一。**教训：Mixin 桩签名（_clone_task×4）必须随实现同步改**。
② finish_instance 三件套（06f78d5）：终态守卫（防假 process_finished_by_admin）+强制完成令牌补 node_exited。
③ incident resume/retry 补推进（dc59d5c）：advance_unfinished_tokens 三处共用（pause 恢复 P0-2 同症不同路）；
**挂起测试载具＝永假条件分支上的 service 节点（注册即炸 handler）——复发节点在主路径上会把 resume 命令一并炸回滚**。
④ MI 收工整树级联+by 标签统一（27abe04）：cascade_terminate 复用（孙实例不再孤儿）；by 统一语义标签
（级联缺省 instance_terminated/cancelled，MI 传 multi_instance_completion）。**RuntimeNodeContext 无 engine/events，
级联要传 kernel.engine+kernel.command_ctx**。
⑤ 声明式 on_exit 三处补齐（50a0b09）：user_task _finish_now/wait finish_wait/reject_flow；驳回侧只补声明式
（行为 on_exit 已被 node_rejected 归档等价实现，加行为钩子会双归档）。
⑥ remove_instance（a66fa83）：outbox 只删已送达行（未投递保留待消费）+运行时数据按全树 id 删；
旧测试钉的恰是被判缺陷的整删行为，随语义更新。
⑦ 错误归因（bf4b90b）：EngineExecutionError.activity_code——节点作用域四帧经 attribute_node_error 标注
（只补不覆盖），incident_raised 带节点定位；错误族迁 engine/errors.py（kernel __all__ 再导出兼容，
kernel 317→288 守红线）。on_fail 接线仍挂账（错误归因专题余项）。
⑧ 两级事件语义成文（4f2edeb）：状态层（终态契约三件套）vs 动作层（*_by_admin）——终态唯一化会破坏
「终态 ∈ 三件套」订阅契约，不做；process_finished_by_admin 补 end_status；domain.py 管理员事件段注释成文。
⑨ 缺位信号（7381e84）：sub_process_completed（父侧子完成信号，先落事件再判唤醒——挂起期唤醒落空也留痕；
MI 逐子与级联终止经 complete_instance 同点覆盖）；process_suspended（与 process_paused 对称）；
process_resumed 补到 incident resume/retry/skip 三路（incident_resolved 保留为故障单层——两级语义）；
_skip_failed_nodes 拆 incident_skip.py 守红线；枚举 45。spec §10 复核批段成文+remove_instance 口径改口。

**on_fail 接线终章（2026-10-13 晚，89888d6）**：错误归因批（bf4b90b）解决了挂账前置——本批把最后
一个零调用钩子接上。engine/fail_hooks.py：故障单事务内按 activity_code 定位失败现场，行为
on_fail(ctx, error)+声明式 hooks.on_fail（错误经 RuntimeNodeContext.failure 新字段，签名统一
(ctx, params)）。**语义口径（成文 lifecycle 契约图修订+spec §2.6）**：钩子观察回滚后持久态；
ctx.token 可为 None——「节点在失败命令内激活后整笔回滚」是最常见失败形态（实证：service on_enter
即炸的令牌查不到，首版按持久令牌严格匹配直接漏发，红绿测试逼出）；TokenState.FAILED 维持零写入
（FAILED 态涉汇聚/复活语义属重设计）；钩子异常不吞故障单；模拟实例不执行（A10）——顺带修掉
「模拟实例失败产孤儿故障单」旧洞（_record_incident 补模拟守卫）。1088 全绿，五道门全过。
**至此事件 review 全链路（review→复核→续修→终章，13+1 提交）彻底闭环，账面零挂账。**

**全仓 review 修复批（2026-10-13/14，四份报告 docs_research/*review-2026-10.md；14 提交 36d3984→本批，1130 全绿五道门全过）**：
上次全内核 review（2026-10-10）之后新落的代码（REST/MCP 开放面、designer+web 审计面、事件修复批自身）经四批深 review
（6 P0+15 P1+~40 P2，全部实验实证+主会话亲读复核），P0/P1 全修复：
① **内核**：resolve(resume/retry) 终态/暂停守卫+terminate/cancel 级联联动关 open 故障单（终态实例复活根除，
`instance_{end_status}` 联动标签，resolve 自身 cancel/fail 传 close_incidents=False 防双发）｜withdraw 重复撤回守卫
（按「本人在办」不按节点——并行会签撤票不拦）｜wait route/reject on_exit 归因入帧（activity_code=None 三面失效根除，
红绿验证）｜correlate_message 增 instance_id 实例定向——signal 防串单（修前全局 FCFS 会把消息消费给先到的他人实例，
副作用落错单；publish 路径零变化）｜事件复合索引 ix_event_order（迁移 0006）/route_decision 可排序 id（修前路由轨迹
顺序随机）/实例已办列表 id 次键。② **REST**：instances/events 分页 +1 探测（next_cursor 恒 None 二页不可达根除）｜
InputContractError 422｜NaN 死循环（DoS）/transfer 孤儿任务/数值字段裸 500/统一错误体/终态 409 五件输入校验组。③ **MCP**：
isError=True 统一错误 JSON｜McpToolError/UnknownToolError/McpConflictError 三分类｜**写工具 actor 必填**（兑现 openface
承诺）｜三查询工具分页（返回 items/total）｜state 白名单/参数预检组｜dispatch WeakKeyDictionary 缓存。④ **designer**：
缺省 mode 改 readonly（原 edit 与拍板相反）/store.mutate 写咽喉守卫+adapter 逐节点硬编码删除（readonly 架空根除）/
新建入口隐藏/saveNow 防重/Ctrl+Z 让位输入框/bootstrap 吞错呈现。⑤ **web+内核**：simulate 非 dict spec 422（修前静默
测旧草稿）/子资源统一 404/limit 界/404 映射/assert 竞态回退/迁移提示；cel.py FormulaError 消息截断 300 字符（修前
celpy 环境转储数 KB 泄漏变量值）。⑥ **文档**：API.md 补 simulate/consult/list_messages/list_jobs+manager_of 纯人
语义+19 工具；spec §10 补 9 成员点名/§14 列名修正/附录第二十一轮；openface §2.4 串单口径修正+actor 剥离警示；
integration.md actor_context 等列修正；18→19 计数漂移五处。
**遗留（有意后置）**：designer 样式作用域收口（npm 嵌入形态级，需容器前缀化+视觉走查）/npm 包声明面（peerDependencies
决策）/readonly 数据源取已发布版（C2 漂移，需产品拍板）/REST response_model 声明/竞速取消 actor 穿透（随重排刀）。

**测试套件完善批（2026-10-14，五批 9 提交 0302652→fdfd987，1160 全绿五道门全过；含三个引擎 fix）**：
「结构随机但执行照剧本」的补另一半——五批落地：
① **不变量账本扩容**（invariants.py）：票数账平（approve+disapprove==表决行−撤票，
vote.py 口径剔咨询行）/已办行↔事件对账（outcome→终态事件映射，consult 每链一次例外）/
子实例树闭合（parent 存在+无环+父终态⟹子终态）/终态契约（终态⟹事件族存在）；
组合面 assert_instance_ledger（中途安全）/assert_full_ledger。
② **随机动作风暴**（storm.py+test_random_storm.py，randomized 默认排除）：
knot/expense 复杂世界上随机组合 14 类动作，三层 oracle＝available_actions 服务端
动作表挑合法动作+非法探针必被拒+每步账本；drain 确定性收敛（断言任意风暴都能被办完）；
改坏计数 DoD 实验第一步即红。
③ **生成器词汇扩容**（random_gen 拆 random_blocks/random_emit，5→8 种块）：
countersign（ceil 票数截断）/mi_call（subjects 多子+collect 对齐）/wait（时长|消息）/
branch 臂可 cc+汇聚 all|plain（any 竞速计划截断）/子定义嵌套分流；Step 扩 advance/
publish/child 域；100 迭代 DoD+词汇覆盖实证。
④ **故障注入×恢复矩阵**（world_flows_fault+test_fault_matrix，23 例默认套件）：
四结构敏感位×resolve 五动作；实证子内/MI 内故障失败命令整体回滚无孤儿、故障单挂父。
⑤ **时间线长程走查**（test_timeline_journey）：knot 长剧本事件全序钉死
（预警<人工转办<到时改派<暂停<恢复<完结，冻结窗零事件）+wait 竞速两形态路由。
**随机件逼出三个引擎缺陷（各自 fix 提交+回归钉子）**：3bda453+e22f36b 扇出两段式
路由（即时臂盲窗致 all 汇聚双激活幽灵令牌；补丁=any 竞速收尾推迟到完结段+推进门
下沉 advance_gate.py 守 300 行）｜fdfd987 withdraw 一票一撤（重建待办转走后
「本人在办」代理失效，二次撤票双发事件账目漂移；事件守卫拒绝）。
随机件纪律：FLOWORK_RANDOM_ITER 默认 30，DoD 实验 100 迭代双 suite 全绿。

**流程演练台批（2026-10-14，CLI walkthrough——用户「模拟真实流程、看得到每一步」想法的落地）**：
与 simulate（自动跑通、人不在环）互补的「彩排」形态——发起**真实实例**（缺省内存库跑完即弃，
`--db` 可持久），剧本/交互逐动作推进，每步打印一张「步单」。四件套：
① `cli/walkthrough_core.py` RunObserver 差分观察器（四个留痕面按已见集合 diff：事件 id/路由决策 id/
待办 id/变量血缘前缀计数——追加型数据面即步单素材）+ 剧本装载校验 + find_task（node+by 定位，
人匹配 user: 前缀互认）+ perform_step（十动作分派，FloworkError 归一 ScriptError 带步骤上下文）。
② `cli/walkthrough_view.py` 步单渲染（叙事视角：动作→新增/离场→路由（条件公式=求值结果→选中边）→
变量 before→after（写方·操作人）→事件（载荷摘录三键）；转办离场是换人语义照常报、出结果类动作的被办
任务不重复报；终点空路由行/事件 None 值/长动作行三处降噪）。
③ 剧本模式多场景顺序跑（`examples/04_expense_walkthrough.json`：大额含驳回回退+改额+转办+会签，
小额默认边直通）+ 交互模式（`序号 动作` 语法，参数逐项提示，s/t/v/q 查状态/时间线/变量/收束）；
④ `examples/04_expense_claim.json` 费用报销模板（申请人 submit=initiator 起步→经理驳回可回退→
金额>5000 加财务复核+总监会签严格过半→传阅发起人；严格过半公式避 bare_threshold lint）。
收束输出=实例状态+流转轨迹表+留痕合计（未走完黄字示剩余待办）。demo/demo-full 迁 cli/demo.py
（命令名不变，app.py 299→169 守红线余量）。**1169 测试全绿，五道门全过**。步单格式=本批核心
设计物，后续 web 演练台/任务页办理体验直接复用。
**自审修复批（同日，13 例）**：① 交互模式引擎拒绝只提示续走不再炸会话（脚本模式中止语义不变）；
② 变量 JSON 三次输错=取消本步动作（原静默按空变量继续办完＝带病落库，最危险一例）；
③ find_task 会签多待办不给 by 即拒（原静默任取一条=list_tasks 无排序不可复现）；候选池任务
（assignee 空串）的 by 对错交引擎认领守卫判（CLI 不复制候选判定）；④ claim/unclaim 可见性——
认领只改 assignee 不换行 id，差分看不见，补「办理人」行现查；⑤ reject 透传 target/variables、
users 收逗号串形态、剧本场景级字段类型校验、header escape、两字标签对齐（顺带修 {label:<3}
填充对两字标签多出一空格的断言错位）。挂账不改：events/variable_history limit=2000 截断
（单会话不可达量级）、variable_history 无平局次键（引擎面可排序 id 与事件 A1 同型，随重排刀）、
withdraw 不在词表（作用于已办历史行，需历史行入口另立）。**1173 测试全绿，五道门全过**。


**designer 三功能定位+模拟补全计划（2026-10-16，用户拍板）**：定位收敛为「流程的信任面」——
① 可视化检视（AI 会审，人看一眼仍不可替代）② 调整编辑（不是主战场，维持现状不加深）
③ 模拟测试（特别重要）。受众=流程的技术相关方（长期方向业务拍板人）；发布把关是工作流约定
不是技术强制（REST/MCP 同样开放 deploy）。行业六轴调研落 docs_research/（本地）：Camunda
双入口同 schema（设计器用例=CI 用例，官方原文亲验）/时间快进全行业弱/统计仿真执行引擎厂商全空。
计划=docs/product/designer-simulation-plan.md（已拍板）：控制面五件套（决策全量十词表/桩/等待/
时间/变量，对齐 CPT 指令维度）+三玩法（自动冒烟/交互排练/场景回归）+一份脚本（与演练剧本同构）；
五批：0 文档 / 1 任务行化+全量决策+桩 / 2 虚拟时钟 / 3 web 排练（rehearsal 并入 simulated=驱动
形态，免迁移）/ 4 场景回归+CI。批 0 定位同步随本批落地（plugin-plan/README/manual/llms）。

**模拟补全批 1a（2026-10-16，designer-simulation-plan 批 1 提交一）**：模拟执行域**任务行化基座**——
simulated 实例建真实任务行，机器人（simulator）经真实任务动作面全通过办理（claim/complete/
complete_assist/complete_consult 按任务类型选动词），会签/协办/候选语义与真实运行完全同源；
驱动扫实例全树（子流程递归），FloworkError=诚实停点进报告 notes 不上抛（完成命令回滚令牌停原地）。
四处配套：user_task 删两处模拟分支（approval_exit=simulated 值退役，全仓无消费方）；deadline
建 Job 加模拟守卫（零 Job 不变量，批 2 虚拟时钟自算）；事件抑制下沉 kernel.record_event 咽喉
按 instance.simulated 判定（驱动器后续命令各建新 ctx，局部 suppress 标志覆盖不到——探查实证三雷
之一）；任务面隔离六处收口（list_tasks join 实例过滤+include_simulated=False 缺省、候选维度
candidate_task_ids join 过滤、instance_id 定向查询不隔离=审计/驱动器口，REST/MCP/CLI 透传继承；
claim 对模拟实例放行候选守卫=业务防抢注不约束试跑）。报告增 notes 字段（驱动器停点说明）；
模拟-驱动顺序=先驱动后出报告。测试：test_simulation 语义更新+新增 5 例（隔离矩阵/候选隔离/
并签机器人/deadline 零 Job/中途取人失败落 notes）。五道门全绿。

**模拟补全批 1b（2026-10-16，designer-simulation-plan 批 1 提交二）**：控制面三件＋共享件内核化。
① **决策全量词表**（拍板①）：simulate 增 controls 参数——decisions[{node,by,action,comment,
variables,target,to,users}] 与演练剧本同构，按序扮演办理人；**给了决策清单即禁用机器人**，
未覆盖的待办停「待决策点」进报告 pending；驳回复活圈定/转办票随人走/会签计数在模拟里原生成立
（真实任务动作面）。② **桩返回值**（拍板②修正口径）：stubs{节点:变量对象}＝替身 handler 经
同一写入口注入（ctx.variables 直引实例行），下游条件真求值；桩注册表按引擎+实例挂
（simulation_controls.py——服务节点可能在决策推进链才到访，须跨命令可用；已消费记 stubs_applied）。
③ **等待假设**：waits{节点:matched|elapsed(+payload)}——直调 finish_wait；matched 载荷走
resolve_payload_write+output_mappings 同语义（消息路径同款含契约边界）；等待节点判别走行为
advances_on_message 标志（kind 分支守卫拦下 kind=="wait" 写法——kind 知识住行为）。
④ **共享件内核化**：tasks/action_steps.py（find_pending_task+dispatch_task_action 十词表分派）
上公开面（api +3 符号：KNOWN_TASK_ACTIONS/find_pending_task/dispatch_task_action）；CLI
walkthrough_core 改薄壳委托（ScriptError 包装，词表单源）——模拟驱动器与演练台同一份分派。
⑤ **报告扩展**：decisions（全树已办行推导：node/assignee/acted_by/outcome/comment）、pending
（停住的待办）、stubs_applied、notes。⑥ **web+designer**：simulate body 扩 controls（非对象 422）；
DryRunDialog 模拟 Tab 加控制区（等待假设下拉+决策/桩 JSON），报告呈现办理记录/待决策/注记。
simulate 两路统一走 _start（instance_id 预生成——桩注册先于启动链）。测试 +10
（tests/semantics/test_simulation_controls.py 九例+web 一例）。**1197 测试全绿，五道门全过**。

**模拟补全批 2（2026-10-16，designer-simulation-plan 批 2；拍板③全兑现）**：虚拟时钟——
**实现口径修正**：放弃「模拟器自算定时器」，改「模拟域定时 Job 照建但置 virtual 态（真实调度器
只认 scheduled，天然不可见），推钟按 run_at 用真实策略面兑现（JobScheduler.run_virtual：CAS
virtual→running＋savepoint 策略＋冻结/咨询推迟拉回 virtual）」——超时五动作/等待到时/实例时限
零第二实现。四处 Job 创建点落 virtual（任务 deadline/实例时限/等待到时/_carry 剩余档——串行换棒
与转办的 carry 不漏真实域）；wait 模拟分支改为「不订阅但建 virtual 到时 Job」。controls 增 clock
（advance_minutes/advance_to_next_event 序列，parse 校验）；驱动优先级＝到期 virtual Job→决策
（下一决策暂无对应待办且时钟未耗尽时先推钟）→机器人（decisions 键含空表即禁用）→等待假设→
**终态即收束**（剩余时钟不消费——红绿逼出：假设优先用例实证终态后时钟仍被消费）；报告增
advanced_minutes，兑现证据进 notes（事件面对模拟静默）。**踩坑**：run_at 与虚拟基线是同命令两次
config.now()，真实墙钟差毫秒——2880 分钟整推时 Job 判不到到期，5 秒容差兜底（推钟粒度分钟）。
designer 模拟 Tab 时钟控制条（+1小时/+1天/下一事件+JSON）。测试 +10
（test_simulation_clock.py：等待到时/下一事件/无定时注记/reassign 全语义含 system:timeout 归因/
自动通过/notify 仍待办/实例时限 cancel/假设优先/carry 保持 virtual/解析校验）。
**1207 测试全绿，五道门全过**。

**模拟补全批 3+批 4（2026-10-16，designer-simulation-plan 收官两批）**：
**批 3 交互排练**——`engine/rehearsal.py`（start_rehearsal/perform_rehearsal_step/
rehearsal_state）：simulated 基座上的人点驱动，步单四面板＝待办（含服务端 available_actions）
+已办+路由+变量血缘与等待点；步种类走**分派表**（kind 分支守卫拦下 `kind=="wait"` 写法）；
`RuntimeService.start_simulation`（只启动不驱动——simulate/排练/场景共用启动路径，
拆 simulation_start.py 守 runtime_service 行数）；web rehearsal_routes 三端点；designer
RehearsalDialog（候选池自动认领/跨实例任务树校验/等待节点两键兑现/只读模式可用）。
**批 4 场景回归**——`engine/scenario.py`+`scenario_checks.py`：一份脚本三消费者
（设计器/CLI/测试，Camunda 双入口同 schema 范式落地）；步骤三类（任务/等待/推钟）与
演练剧本同构；断言六型业务效果口径；覆盖照明非门禁；`wf_designer_scenario`（迁移 0007）
+ scenario_store（CRUD+last_ok 预览）；web 六端点 + CLI `flowork scenario run`
（退出码 0 全绿/1 断言失败/2 脚本错误）；designer ScenarioDialog（红绿列表+脚本编辑+
断言表+覆盖）。examples/04_expense_scenario.json 报销全链路七断言实测全绿。
**公开面 +12 符号**（排练三件/场景三件+Report+Store+Controls+parse/drive）——死符号守卫
两处登记（SimulationReport/ScenarioStore 属返回模型）；投影纪律换绑全经 api。
**1224 测试全绿，五道门全过**。

**模拟补全批 5（2026-10-16，designer-simulation-plan 可选项主体）**：覆盖照明已随批 4 报告下发
（节点/边 covered+missing 双呈现）；本批补**两版对比跑**——`POST /api/scenarios/{id}/compare`
（同一脚本草稿 vs 最新/指定已发布版并排红绿；单侧跑不起来返回 ScenarioFailure 占位不中断）
+ designer「对比线上版」按钮（两版结论不一致明确告警）。web 场景面拆 `scenario_routes.py`
（与排练面分居守 300 行红线，app.py 296 行）。未做：画布路径动画（体验打磨，按定位不加深）、
跨场景覆盖聚合（统计面按需另立）。测试 +2（对比双测）。**1226 测试全绿，五道门全过**。

**模拟补全 DoD 实证批（2026-10-16，计划 §5 收口）**：新增 `examples/05_timeout_reassign.json`
（经理两级超时：1440 notify + 2880 reassign→总监；会签严格过半；传阅发起人）+
`examples/05_timeout_scenario.json`（推钟 2880 → 真实 notify/reassign 依次兑现 → 总监接棒 →
会签通过；四条断言含 `outcome=transfer, by=system:timeout` 归因钉子）。CLI 实测全绿、覆盖 6/6；
回归测试 test_scenario_timeout_reassign_end_to_end 固化（拍板③「超时全兑现」的端到端证据——
行业调研里「全行业验证不了」的分支，flowork 现在可验）。**1227 测试全绿，五道门全过**。

**模拟补全 review 修复批（2026-10-17，三路并行 review 落地；1234 全绿五道门全过）**：
三路代码 review（内核驱动/隔离触点/传输前端）发现 **1 P0 + 7 P1 + 十余 P2**，全部修复：
**P0** compare 端点 `asdict(ScenarioFailure)` 崩溃（非 dataclass → 改 @dataclass + `.of()` 工厂，
否则「一侧失败不中断」承诺自身即 500）。**P1**：① `complete_consult` 的第三参是 `opinion`
不是 comment——动作词表位置传参必被拒（单列分支 + 测试）；② **排练端点可驱动真实实例**
（越权面）：`_require_simulated` 树校验；③ 驱动循环只捕机器人分支错误——决策/等待/虚拟时钟
兑现三处补齐（策略异常在 `run_virtual` 归一 FloworkError 并结案 Job，绝不穿出丢报告）；
④ 场景 `ok` 只看断言——步失败仍报绿：加 `stopped` 标志；⑤ `controls.waits` 解析后从未使用
（预置等待假设失效）：步间 `resolve_wait` 兑现；⑥ 场景按 node 定位只查根实例（子流程待办
不可达）：`_locate_in_tree` 全树。**P2 组**：wait 到时 Job 清理补 virtual（残留空烧）、
桩注册沿 parent 上溯（子实例桩曾全部失效）+ 模拟结束清理、断言 `op` 分支求值（不可比不抛）、
`in` 形状校验、clock 步复用 parse_controls 校验、paged_tasks/task_history 补模拟隔离
（`sim_isolation.exclude_simulated` 单源）、场景 store 显式 id 未命中报错+定义归属校验、
CLI OSError 归一+补设计期校验门槛、报告 `visited/stubbed/stalled_at/route_decisions` 全树聚合
（子流程节点曾在时间线凭空消失）、`history_service` 拆 `history_token.py`（MRO：新 Mixin 须置于
TraceMixin 之前——后者有同名 NotImplemented 桩）。**designer**：场景面板切换清 compare 残留、
readonly 隐藏场景写面（保留运行）、变量 JSON 改对后解除禁用、删除失败 toast。
测试 +7 回归钉子（review 每条一个）。

**模拟补全 UI 自检批（2026-10-18，浏览器实跑四面板；1234 绿维持）**：后端起 `FLOWORK_DB=/tmp/ui_check.db`
（空库灌 examples）+ vite dev（**注意：vite 只监听 IPv6 ::1，须用 localhost 而非 127.0.0.1**）。
自检七项全过、控制台 **0 错误**（唯一 warning 是 React Flow 授权提示，非本批引入）：
① 列表页 5 示例 + 单例节点正确置灰（合并了并行会话的 singleton 批）；
② 画布 + 四入口（试运行/🎭排练/🧪场景/⚖实例）齐；
③ 路由预演 6 节点可达 + 画布高亮；
④ 完整模拟：决策清单驱动「驳回→改额」、办理记录 2 步、终态 completed；控制面 UI（决策/虚拟时钟三快捷键/桩）齐全；
⑤ 排练：待办 1 条→点同意→换部门经理（available_actions 下发 7 个动作）→走完终态 completed，步单四面板实时；
⑥ 场景：新建保存（列表显示「2 步·2 断言·未运行」）→运行→✓ 红绿点 + 断言表 + 覆盖 6/8（未覆盖节点点名）→
   发布 v1.1 后「对比线上版」草稿 vs v1.1 双绿；无已发布版时正确降级（P0 修复实证）；
⑦ 实例审计：默认隔离模拟实例（只列 1 条真实）、令牌链 #1–#6、路由决策面板呈现
   「amount > 5000 → false 选中 route->notify」。
**自检判定**：无新增缺陷（一处误判后核实为设计行为——发布成功页需点「完成」关闭）。
清理：停 dev/uvicorn、删 /tmp/ui_check.db。
