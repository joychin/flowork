# 设计器插件化与审计面计划（designer-plugin-plan）

> 状态：**批 A/B/C/D 全部落地（2026-10-11，971 测试全绿）**；D3 公式中文投影按计划留作可选未做。
> 制定 2026-10-11，同日 review 修订（含「草稿模拟」拍板）。
> 定位拍板来自会话讨论：设计器 = 交付开发人员的查看与审计插件。
> 定位属范围变更，已记《本期建设边界》backlog 附录。
> **定位更新（2026-10-16，用户拍板三功能）**：designer = 流程的信任面——① 可视化检视
> （AI 会审，人看一眼仍不可替代）；② 调整编辑（不是主战场：维持现有能力、不再加深）；
> ③ 模拟测试（特别重要——补全计划见 designer-simulation-plan.md）。受众＝流程的技术相关方
> （长期方向：业务拍板人，届时唯一杠杆是公式与证据中文化，暂不做）；发布把关是工作流约定
> 不是技术强制（REST/MCP 同样开放 deploy，凭宿主凭证分配）。

## 1. 定位结论（已拍板，本文档只管落地）

- **受众**：开发人员；**形态**：可嵌入插件（npm 包）；**职责**：查看 + 审计（定义审计 + 运行审计）。
- **编辑能力保留**：仓库自身开发时全功能；宿主嵌入默认只读，按配置开放编辑。
- **产品分工**：设计器插件 = 开发者审计面；任务页（M-C 主线）= 业务办理面。数据同源，受众不同。
- **数据源不变**：库是唯一事实源；设计器无私有存储，一切经 web 层走内核仓储；文件仅为种子与导入导出旁路。
- **契约纪律**：插件消费的 `/api` 面之于设计器，如同 `flowork.api` 之于宿主——发包前冻结最小契约并加快照守卫。
- **模拟语义拍板（2026-10-11）**：「只对已发布流程模拟没有意义」——模拟的主场景是**草稿**（AI 改完草稿 → 人模拟拿证据 → 拍板发布）。证据必须在拍板之前存在。

## 2. 现状盘点（2026-10-11，已逐项对代码核实）

设计器已有：画布编辑全套、schema 驱动表单、registry 调色板、校验闭环（走内核 validate，含部署期预演）、
路由预演（dry-run，含等待假设）、公式试算、词汇目录下发、语义 diff、草稿/发布生命周期。

web 层 11 个端点：health / node-kinds / definitions 列表·详情·保存 / publish（**已返回 next_major /
next_revision / breaking**）/ validate(`?deploy=true`) / dry-run / formula-preview / catalog / diff。
**没有任何实例与运行审计读面。** app.py 现 223 行。

内核已就绪、web 未接出（文件级位置）：

| 能力 | 位置 | 审计用途 |
|---|---|---|
| 完整模拟 | `engine/runtime_service.py` RuntimeService.simulate → `engine/simulation.py` SimulationReport | 发布前验证，试跑与真实运行同源 |
| 实例/任务/令牌查询 | `engine/history_service.py`：instances（state/definition_code 过滤，**默认隔离模拟实例**）/ get_instance / tokens / paged_tasks（游标分页）/ task_history / **trace_instance**（实例时间线聚合）/ route_decisions | 走到哪、谁办过、条件为何这么选 |
| 变量面 | `engine/variable_actions.py`（VariableActionsMixin 挂 RuntimeService）：variables_of / variable_history | 变量改过什么、何时改的 |
| 版本判定 | `definitions/versioning.py` VersionVerdict（breaking 为布尔，**破坏性明细在 triggered_rules**；identical_to=无变化） | 这次发布是否破坏性、改了什么；publish 流程已计算，web 已回一半 |
| 草稿存取 | `engine/designer_drafts.py`（挂 RepositoryService）：update_draft / designer_draft / deploy_draft | 草稿模拟与发布的取数源 |
| 目录读面 | 目录读缓存（directory/queries） | 取人引用翻译 |
| handler 注册表 | 行为注册表 | service 节点动作名录 |

**唯一的内核缺口**：simulate 走 `start_process(code)`，只能按 code 解析库内发布版，**不能模拟草稿**——
按 §1 拍板，批 A 含一件内核小改（见 A1）。

## 3. 分批实施

### 批 A · 审计数据面（web 开口子 + 一件内核小改）✅ 已落地（2026-10-11）

落地实录：内核 `simulate` 增 `spec` 入参（草稿直通，版本列占位 (0,0)，start_process
公开面不开放 spec）；web 新模块 `contrib/web/audit_routes.py` 九端点（simulate /
instances 列表·详情·timeline·route-decisions·variable-history·tasks / versions 列表·
按版本取 spec）；publish 响应补 triggered_rules + identical_to；designer_drafts 增
published_versions。测试 +14（内核 5 + web 9，tests/semantics/test_simulation.py +
tests/test_web_audit_api.py）。

- **A1 草稿模拟（含内核小改）**：`POST /api/definitions/{code}/simulate`——body 可选携带 spec
  （缺省取已保存草稿），模拟前强制 validate（设计期 ERROR 拦截）；`?major=&revision=` 时模拟指定
  发布版（对照线上行为）。内核改动：simulate/start_process 增加**按已解析定义直通**的入口——
  草稿不落定义表、实例行版本列记草稿占位、隔离语义全部沿用（suppress_events / 不建 Job /
  instances() 默认排除）；内核测试先行。
- **A2 实例只读面**（对齐 HistoryService 方法表，资源命名与参数风格对齐 spec §12 已定稿的 /v2 面）：
  实例列表（state / definition_code 过滤，沿用默认模拟隔离——**模拟实例不进列表是语义不是 bug**）、
  实例详情（含 tokens 令牌状态）、实例时间线（消费 trace_instance）、`route-decisions`、
  `variable-history`、任务列表（用 paged_tasks）。
- **A3 publish 响应补全**（降级实件——判定已在返回）：补 `triggered_rules`（破坏性变更规则明细）与
  `identical_to`（内容无变化提示）两字段；与既有 diff 端点合成「AI 改了什么」证据链。
- **A4 版本面**：`GET /api/definitions/{code}/versions`（已发布版本列表）+ **按版本取发布 spec 的详情端点**
  （形状抄 spec §12 的 `GET /v2/definitions/{code}?major=&revision=`）——只读模式（C2）没有草稿，
  此端点是 readonly 视图的数据前提。
- 纪律：批 A 端点拆独立路由模块（preview_routes.py 先例，守 app.py ≤300 行）；一律经 `flowork.api`
  （投影纪律测试已守）；web API 测试先行。

### 批 B · 审计呈现（前端）✅ 已落地（浏览器走查全过）

落地实录：试运行双 Tab（路由预演保留 + 完整模拟——终态/时间线/桩/卡点/路由轨迹，
主场景=当前草稿含未保存编辑）；发布确认呈现判定全貌（幂等去重 identical_to 与
triggered_rules 明细实测呈现）；实例审计视图（顶栏「实例」入口 → 列表按状态过滤+
模拟隔离勾选 → 详情概要+令牌链+三面板）。发布编排移入对话框本地（不触 store 并行
在途改动）。

DoD 走查（examples 定义 + 真实实例）✅：①**驳回实例**——simple_approval 上
`task.reject`（「金额信息不全，退回补充」），详情时间线完整呈现否决 1/reject 办理行
带意见/task_rejected 事件/复活令牌（start 重报，unfinished）；②**超时改派实例**——
approval_timeout 定义 manager 挂 `deadlines: reassign→'u_boss'`，经真实 tick 管线
触发，时间线呈现 transfer 办理行（超时reassign）/task_transferred 事件
（actor=system:timeout）/新任务 u_boss，令牌计数 0/1 对齐「票随人走」；两实例的
路由决策与变量历史面板均正常渲染（含空路由记录时的空态）。

- **B1** 试运行升级：路由预演保留为轻量 Tab，新增「完整模拟」面板（主场景=当前草稿）——终态结论、
  节点时间线、stub 清单、卡点、路由轨迹（逐边公式与结果）。
- **B2** 发布确认呈现版本判定（消费 A3：major/revision、triggered_rules 明细、identical_to 时呈现「无变化」）。
- **B3** 实例视图：定义详情页增「实例」入口 → 实例列表（按状态过滤）→ 实例详情三面板
  （时间线=trace_instance / 路由决策 / 变量历史）。
- DoD：用 examples 定义跑真实实例后完整走查一遍（含一次驳回、一次超时改派）；
  **断言「模拟实例不出现在实例列表」**（钉住隔离语义）。

### 批 C · 插件化改造 ✅ 已落地

落地实录：C3=`tests/test_web_api_surface.py` 契约冻结快照（只冻 /api 面；增删=显式动作，
D 批扩展时同步）；C4=迁移 0005（草稿表独立 layout 列）+ 读写拆合（content 只存语义、
旧行惰性迁移、发布合回既有行为）+ 顺带修 import 副作用（web 默认应用改模块级
__getattr__ 惰性创建——旧库缺列不再炸 import）；C2=`src/config.ts` 配置面
（apiBase/mode/definitionCode）+ 只读模式（顶栏编辑动作/调色板/属性面板隐藏、画布
不可拖不可连、快捷键守卫）；C1=`vite.lib.config.ts`（ESM 单文件+CSS，React CJS 包裹层
process.env.NODE_ENV 显式 define——浏览器裸 process ReferenceError 踩坑）+
`src/embed.tsx` 挂载入口 + 引擎可选托管（dist-lib 存在即挂 /designer-lib，示例页
/designer-demo）。浏览器验证：只读嵌入页深链直开、编辑面全隐藏。

- **C1** 构建：Vite library mode 出 npm 包（名待定，如 `@flowork/designer`）；引擎 contrib/web 可选托管
  构建产物（单进程零配置 demo 模式）。
- **C2** 配置面 = 包的公开接口：`apiBase`、`mode: "edit" | "readonly"`（默认 readonly）、挂载容器。
  readonly 模式视图目标=已发布版本（数据来自 A4 版本详情端点），非草稿。
- **C3** 最小契约冻结：插件依赖的 `/api` 端点清单入档 + 快照测试守卫（模式对齐 api_surface）；
  插件与引擎同仓同版本发布，冻结先行于对外发包。
- **C4** 卫生：草稿 JSON 中布局坐标与语义内容分离存储（定义导出/Git 镜像的前提；旧草稿惰性迁移）。
- DoD：任意外部 React 应用按文档三行代码嵌入只读审计视图。

### 批 D · 支撑面 ✅ 已落地（D3 未做，见下）

落地实录：D2=`GET /api/handlers`（注册表 names 排序下发）；D1=`GET /api/directory/summary`
（duck-typing 探测静态目录 users/departments/roles，未配置降级 configured=false；
`FLOWORK_ORG=org.yaml` 装配）+ 前端 `src/options.ts` 建议缓存（用户建议带 CEL 引号
`'u_zhang'` 且 label 附部门/角色翻译；candidate_groups 建议 `role:`/`org:` 合法组引用）
+ SchemaForm 取人/动作字段 datalist（单值输入与标签输入两形态）。**D3 公式中文投影
未做**（计划既定的可选降级项，受众为开发人员非必需——待需要时另批）。

- **D1** 目录只读下发：`GET /api/directory/summary`（组织/角色/用户摘要）；前端做取人引用翻译
  （如 role 引用 → 部门/角色名）与下拉辅助。
- **D2** handler 名录下发：service 节点下拉选择 + 发布前预检提示（原挂账「handler 名录与模拟试跑」就此收口）。
- **D3**（可选，降级项）公式中文投影：词汇目录驱动；开发受众下非必需，做不做随批 D 一并定。

### 排序与并行

主线 **A → B → C → D**；C1 构建骨架可与 A 并行（不触契约）。A + B 落地即打通
「AI 生成 → 人审阅 → 人拍板」主链路；C 使其可交付；D 补审阅颗粒度。

## 4. 明确不做

- 画布编辑体验继续加深（现状已超审阅所需）。
- BPMN 导入导出、模板市场、复杂表单编辑器。
- 设计器自带鉴权（宿主网关代理 + 凭证注入；引擎只记身份事实——既有裁定）。
- 业务办理面（任务页属 M-C 主线，不混入本计划）。

## 5. 开放问题（不阻塞批 A/B）

1. 只读凭证形态：建议宿主代理注入、web 层不做鉴权；宿主无网关能力时再议最简 token。
2. 实例详情是否加事件流视图：首版用任务/路由/变量三面，outbox 事件流后置。
3. D3 公式中文投影做否：随批 D 拍板。

## 6. 工程纪律

每批一提交（conventional commits）；测试先行（含 A1 内核直通的内核测试）；五道门全绿；
web 层一律经 `flowork.api`；单文件 ≤300 行；范围再变化记《本期建设边界》backlog 附录。
