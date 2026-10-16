# 设计器模拟测试补全计划（designer-simulation-plan）

> 状态：**草案，待拍板**。制定 2026-10-16。
> 行业参照：《流程模拟行业最佳实践调研》docs_research/流程模拟行业最佳实践-2026-10-16.md（本地资料）。
> 定位背景（2026-10-16 用户拍板三功能）：① **可视化检视**（AI 会审，人看一眼仍不可替代）；② **调整编辑**（不是主战场，维持现有能力不再加深）；③ **模拟测试**（特别重要，本计划补全对象）。设计器=流程的信任面（发布前审阅把关 + 运行后审计回看）；形态维持双轨（只读组件嵌入宿主 / 仓库内全功能工作台）。

## 1. 现状与缺口（代码实况，2026-10-16）

现有三层（都不动底座，本计划是加控制与加前端）：

| 能力 | 位置 | 语义 |
|---|---|---|
| 路由预演 dry-run | `definitions/dry_run.py` | 静态求值 CEL 画路径（**行业少见的真求值**）；等待节点假设 matched/elapsed/auto；不落库 |
| 整图模拟 simulate | `engine/simulation.py` + `runtime_service`（simulated 实例） | 真引擎跑草稿；隔离完整（事件抑制/不建 Job/查询默认隔离/子流程与 MI 递归传播）；**user_task 恒自动通过**（approval_exit=simulated）；**service 恒 stub（无返回值）**；**wait 冻结为卡点**；报告=终态/时间线/桩清单/卡点/路由轨迹 |
| 公式试算 + CLI 演练台 | `preview_routes` / `cli/walkthrough` | 单公式试算；演练=**真实实例**逐步推进，步单（动作→新增/离场→路由→变量→事件），10 动作词表（complete/reject/transfer/assist/consult/claim/unclaim/add_countersigner/complete_assist/complete_consult） |

缺口（编号 G，供批次引用）：

| 编号 | 缺口 | 说明 |
|---|---|---|
| G1 | 决策不可扮演 | 整图模拟恒「全通过」——驳回/改数/转办路径走不了；审批流的核心路径全在人工决策上 |
| G2 | 桩无返回值 | service_task 桩不产生输出，下游依赖服务输出的分支无法验证 |
| G3 | 时间不可推进 | wait 超时、deadline 动作、实例时限在模拟中全部不可达（行业普遍弱项） |
| G4 | 消息不可注入 | 整图模拟里没法「让消息在等待节点到达」 |
| G5 | web 无逐步演练 | CLI 有（步单设计物已成型），web 缺 |
| G6 | 无断言/回归 | 没有可重复跑的场景脚本；walkthrough 剧本格式是半成品（有步骤词表与变量注入，缺控制与断言） |
| G7 | 无覆盖率照明 | 场景跑多了可以顺手看覆盖，先不做 |
| G8 | 呈现代差 | 模拟报告是列表，画布不回放路径/不标当前节点 |

## 2. 目标形态：一个模拟执行器，三种玩法，一份脚本

**控制面五件套**（模拟执行的输入词汇——对齐 Camunda CPT 指令维度，且我们已亲验「设计器与 CI 同 schema」是行业最值得抄的范式）：

| flowork 控制面 | 语义 | CPT 对应 |
|---|---|---|
| 决策 decisions | 节点(+人)→ complete/reject + variables/comment | COMPLETE_USER_TASK / UPDATE_VARIABLES |
| 桩 stubs | 节点→返回值 dict（下游分支可验） | MOCK_JOB_WORKER_* / MOCK_CHILD_PROCESS |
| 等待/消息 waits/messages | 节点→ matched/elapsed + 载荷注入 | CORRELATE_MESSAGE / PUBLISH_MESSAGE |
| 时间 clock | 推钟 advance(PT1H)/推进到下一事件 | INCREASE_TIME / SET_TIME |
| 样例变量 variables | 起始数据（已有） | CREATE_PROCESS_INSTANCE |

**三种玩法**（同一执行器、同一脚本格式）：

1. **自动冒烟**：变量+桩+等待假设+时间脚本一键跑完 → 「能不能跑通、卡在哪」（现状增强）；
2. **交互排练**：逐步喂决策，人在每步扮演办理人（点动作、填变量、看步单）→ 「走一遍看合不合理」（web 版演练台）；
3. **场景回归**：控制面+断言写成 JSON 场景 → 设计器里红绿、CLI/CI 里跑同一份文件 → 「固定下来反复验」（抄 Camunda 双入口）。

**一份脚本格式**：walkthrough 剧本（steps：node/by/action/comment/variables/target/to）向后兼容地扩展 `controls`（stubs/waits/clock）与 `assertions`（终态/到访/未到访/任务行/变量/路由选中边）；`$schema` 版本化。三个消费者：CLI walkthrough（真实实例，保持不变）、设计器排练面板、场景运行器（模拟实例）。

## 3. 批次

### 批 0 · 定位与文档同步（小，先行）

- `designer-plugin-plan.md` §1 补三功能定位与「信任面」措辞（含「发布把关是工作流约定不是技术强制」的诚实条款）；`designer/README.md` 与 `docs/manual/11-deploy.md` 措辞同步（「开发与审计工具」→「审阅与模拟测试工具」）；llms.txt/根 README 对应句；《本期建设边界》backlog 记录定位更新。
- 验收：文档一致，无代码改动。

### 批 1 · 模拟控制面：决策与桩（内核 + web + designer）

- **内核**：`simulate` 增 `controls` 入参（decisions/stubs/waits/messages；keyword-only、缺省=None 时行为与现状完全一致）；决策走真引擎语义（approval_exit 置 agree/reject → 下游条件/会签计数/复活圈定自然生效）；报告扩展：应用的决策清单、桩返回值、等待假设兑现记录、**待决策点**（给了 controls 而未覆盖的 user_task 停在「待决策」而不是默认通过——缺省无 controls 时仍全通过）。
- **决策首刀**：complete(agree)/reject + variables（驳回回退=复活语义天然生效）；转办/加签/协办/咨询随批 3 排练域（真实任务行）获得全语义。
- **等待假设统一**：dry-run 的 matched/elapsed/auto 进整图模拟（按假设兑现 wait 完成；matched 可带载荷注入验 output_mappings）。
- **web**：`POST /api/definitions/{code}/simulate` body 扩 controls（契约快照同步）；**designer**：模拟面板加「控制」编辑区（逐节点：决策下拉+变量、桩 JSON、等待假设），报告呈现决策/桩应用记录。
- 测试：内核 semantics（驳回路径/多态会签按决策计数/待决策点语义）+ web 契约。
- **拍板点 ①**：决策词表首刀范围（建议=complete/reject+变量；其余随批 3）。
- **拍板点 ②**：桩返回值与 output_mappings 的关系（建议：桩值作为服务节点输出、走该节点 output_mappings 同语义——与真实路径一致）。

### 批 2 · 虚拟时钟（内核 + designer）

- **内核**：模拟实例显式推钟——`advance("PT24H")` / `advance_to_next_event()`；模拟器自算下一事件（wait 超时、任务 deadline、实例时限），**不建 Job**（在模拟事务内按定义+令牌状态排序取最近）；兑现：wait 到时=elapsed、**deadline 动作**按类型映射（notify 记录留痕；reassign 走改派语义；auto_approve/auto_reject 走对应出口；mark 记录）、实例时限到点落终态。
- 报告时间线带「模拟时刻」；route_audit 的 now 即虚拟时刻（时钟统一批已钉 config.now，天然复用）。
- **designer**：时间控制条（+1 小时/+1 天/推进到下一事件）；报告时间线呈现「时间跳过」标记与每步时刻。
- 测试：wait 超时分派、deadline 动作矩阵、实例时限、推钟上限（防死循环）。
- **拍板点 ③**：deadline 动作保真口径——全兑现 vs 只报告「到点将触发」（建议全兑现：审批流的价值很大一块在超时分支，行业普遍验证不了=我们的差异点）；与批 3 的关系：模拟域（无任务行）由模拟器兑现，排练域（有任务行）可复用真实 Job 机制跑虚拟时间。

### 批 3 · 交互排练（web 演练台）

- **实例三档**：real / simulated / **rehearsal**（排练域）——隔离（查询面默认过滤、事件抑制、service 恒 stub、不建外部 Job）**但建真实任务行**：排练者用既有任务动作办理，转办/加签/协办/咨询全语义天然可用，无重复实现。`simulated` 布尔列改造为 `mode`（real/simulated/rehearsal）+ 迁移；查询面（实例/任务）默认过滤 simulated+rehearsal。
- 排练可**挂剧本**（预填步骤，照着走）也可**纯交互**；CLI walkthrough 保持真实实例+内存库形态不变，共享步单渲染与步骤词表——`walkthrough_core` 提为共享件经 `flowork.api` 导出（公开面快照 +N 符号，CLI 改 import）。
- **前端**：排练面板（步单复用 CLI 设计物：动作→新增/离场→路由→变量→事件；扮演提示「轮到 u_manager」；动作按钮取 available_actions）；画布高亮当前节点+已走路径；变量注入表单。
- 测试：三档隔离矩阵（排练实例不进业务列表、事件不投递）、排练全动作走查。
- **拍板点 ④**：排练实例的生命周期（保留供回看 vs 过期自动归档；建议默认保留、一键清理）。
- **拍板点 ⑤**：三档列改造的兼容面（零兼容改 mode 列 vs 并列旧布尔——建议零兼容，早期惯例）。

### 批 4 · 场景回归（脚本 + 断言 + 存储 + CI）

- **场景格式**：剧本 steps + controls + assertions；断言=业务效果口径（终态/到访/未到访/任务行存在/变量值/路由选中边），期望值支持字面量+CEL 表达式；`$schema` 版本化（如 flowork-scenario/1）。
- **运行器**：`run_scenario(定义, 场景)` → 逐步结果+断言红绿（复用批 1/2 的执行器）；web `POST /api/definitions/{code}/scenarios/run`；CLI `flowork scenario run <定义> <场景文件>`（CI 口，退出码非零即失败）。
- **存储**：`wf_designer_scenario` 表（迁移）；designer 场景面板（列表/存/跑/红绿/最近一次结果与时刻）。
- 测试：场景运行器矩阵（直通/驳回回退/超时改派）、断言能力表、CLI 退出码、web 契约。
- **拍板点 ⑥**：场景跑「草稿」还是「指定版本」（建议都支持、显式选择：草稿主场景与对照线上并存）。
- **拍板点 ⑦**：场景是否成为发布门禁（**建议不做门禁**——行业无先例，测试/覆盖率都是照明；可选在发布确认框显示「场景红绿」提示）。

### 批 5 · 呈现与覆盖（可选后置）

- 覆盖率照明：场景批跑 × 节点/边覆盖报告（Spiff coverage.py 式）——照明指标非门禁。
- 两版对比跑：同一场景对草稿与线上版本双跑对比。
- 画布回放增强：报告路径动画（批 3 高亮的自动化版）。

## 4. 非目标（明确不做）

- **统计仿真**（工时/成本/资源 what-if）：属决策分析域（BPSim/Celonis 一系），内核只记事实；数据面（cost_seconds 等）已备，未来按信号另立。
- **AI 自动生成场景**：行业尚无成熟范式（UiPath 营销口径+两篇论文）；场景格式先立，将来「AI 按格式生成场景草案」只是一个小函数，不占主线。
- 画布编辑体验继续加深、BPMN 导入导出、业务办理面（任务页）——既有结论不变。

## 5. 验收（DoD）：费用报销模板（examples/04_expense_claim）全程

1. 批 1：模拟「经理驳回 → 重报改额 → 财务转办」路径，报告呈现决策应用与复活语义；
2. 批 2：推钟 2 天证明「48h 未办 → 转上级」分支，deadline 动作兑现且留痕；
3. 批 3：web 排练面板人肉走完全程（含会签），每一步有步单；
4. 批 4：三场景脚本（直通/驳回回退/超时改派）落库，设计器红绿可见，`flowork scenario run` 在 CI 全绿。

## 6. 工程纪律

- 五道门全绿；测试先行（每批内核语义测试 + web 契约快照同步）；单文件 ≤300 行。
- 公开面与契约快照（`flowork.api` / `/api`）增删=显式动作；REST/MCP 是否随批开放场景运行端点另议（不阻塞本计划）。
- 范围变化记《本期建设边界》backlog 附录。
