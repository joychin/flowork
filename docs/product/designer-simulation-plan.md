# 设计器模拟测试补全计划（designer-simulation-plan）

> 状态：**已拍板（2026-10-16），按批施工**。制定 2026-10-16。
> 七个拍板点全部已决：① 决策词**全量**（十动作词表，与演练剧本同构——连带基座翻转，见批 1）；
> ②–⑦ 按原建议，其中②有诚实修正：**桩＝替身 handler 写变量**（output_mappings 是 wait 专属、
> service handler 返回值本就由 handler 自写变量——探查证实）。
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
| 决策 decisions | 节点(+人)→ **全量十词表**（complete/reject/transfer/assist/consult/claim/unclaim/add_countersigner/complete_assist/complete_consult + comment/variables/target/to，与演练剧本同构） | COMPLETE_USER_TASK / UPDATE_VARIABLES 等 |
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

### 批 1 · 模拟执行域任务行化 + 全量决策 + 桩（内核 + web + designer）

拍板①为**全量决策词**（2026-10-16）。架构连带（探查证实）：转办/加签/协办/咨询的语义全部长在真实任务行与任务动作服务上，模拟域若维持「不建任务」（user_task.py 两处 simulated 分支）就没有承载这些动作的实体——**先翻转基座，再上控制面**。两提交：

**提交 1a · 基座翻转与隔离（内核）**

- **user_task 翻转**：删两处 simulated 分支（不建任务/进入即完成），模拟实例走真实路径（取人→建任务→等待完成）；approval_exit="simulated" 值退役（全仓无消费方）；取人失败照常挂起（诚实卡点）。
- **deadline Job 守卫**：schedule_deadline_jobs 加 simulated 跳过——「模拟零 Job」不变量保持（批 2 由模拟器自算定时器；探查发现该处原本无守卫，翻转后模拟任务会建出被真实调度器触发的 Job）。
- **事件抑制下沉**：从 start 命令的局部标志改为按 instance.simulated 在咽喉判定——否则驱动器后续命令（complete/finish_wait，每命令新 ctx 默认开事件）会把模拟事件真投 outbox。
- **任务面隔离**（探查证实六个面今天全部不过滤）：list_tasks 与候选查询 join 实例过滤（include_simulated=False 缺省，与 instances() 同款）；instance_id 定向查询不过滤（审计要看模拟实例任务）；REST/MCP/CLI 透传自动继承。
- **机器人驱动循环**：simulate 收尾把待办按缺省 agree 办完（actor=simulator，候选任务先认领），扫全树（子实例待办一并驱动），步数上限防环；停点=终态/wait 冻结/取人失败。
- 测试：test_simulation.py 语义更新（建任务并归档/exit=agree/零事件零 Job 不变量保留）+ 任务面隔离矩阵（start_process(simulated=True) 原始入口验证活跃模拟任务的可见性）。

**提交 1b · 控制面（内核+web+designer）**

- **controls 模型**：simulate(..., controls=None)——decisions[{node,by,action,params}]（全量十词表）、stubs{node:值}、waits{node:matched|elapsed(+payload)}；缺省 None＝机器人（1a 语义，对外兼容）。
- **驱动器** engine/simulation_runner.py：find_task 与十动作→TaskService 分派映射提为内核共享件（CLI walkthrough 改 import，api 快照 +N）；显式给 decisions 时未覆盖的 user_task 停为「待决策」（不回落机器人）；**桩＝替身 handler 写变量**（拍板②修正口径）；waits 直调 finish_wait（无 simulated 守卫，已证实可调）。桩与决策跨命令可用＝引擎级按实例注册 controls（服务节点可能在决策推进链中才到达）。
- **报告扩展**：决策清单从已办行推导（acted_by/outcome/comment 天然在）、桩应用记录、待决策点（停住的待办）。
- **web+designer**：simulate body 扩 controls（契约快照同步）；DryRunDialog 模拟面板加「控制」编辑区（逐节点：动作下拉（十词表）+参数、桩 JSON、等待假设）。
- 测试：全词表驱动走查（**转办票随人走/加签计数/协办收口在模拟里成立**——全量决策词的价值钉子）、待决策语义、桩注入使下游分支可验、web 契约。

### 批 2 · 虚拟时钟（内核 + designer）✅ 已落地（2026-10-16，实现口径修正见下）

- **实现修正**：原稿「模拟器自算下一事件、不建 Job」改为「**模拟域 Job 照建但置 `virtual` 态**（真实调度器只认 scheduled，天然不可见），推钟按 run_at 用**真实策略面**兑现（`JobScheduler.run_virtual`）」——超时五动作/等待到时/实例时限零第二实现，拍板③的全兑现由真实语义背书。同命令毫秒级时钟偏移以 5 秒容差兜底（推钟粒度是分钟）。
- **内核**：`controls.clock`（`{"advance_minutes": N}` / `{"advance_to_next_event": true}` 序列）；驱动优先级＝到期 virtual Job → 决策（下一决策暂无对应待办且时钟未耗尽时先推钟）→ 机器人（给了 decisions 键即禁用，含空表）→ 等待假设 → 终态即收束（剩余时钟不消费）；`_carry` 与等待到时 Job 同样落 virtual（串行换棒/转办的剩余档不漏给真实调度器）；报告增 `advanced_minutes`，兑现证据进 notes（事件面对模拟静默）。
- **designer**：模拟 Tab 时钟控制条（+1 小时/+1 天/下一事件 快捷键 + JSON 数组编辑）；报告呈现推钟合计。
- 测试：tests/semantics/test_simulation_clock.py 十例（等待到时/下一事件/无定时注记/reassign 全语义/自动通过/notify 仍待办/实例时限 cancel/假设优先/carry 保持 virtual/解析校验）。
- 已决（③ **全兑现**）：由真实策略面背书——审批流的价值很大一块在超时分支，行业普遍验证不了＝我们的差异点。

### 批 3 · 交互排练（web 演练台）✅ 已落地（2026-10-16）

- 基座已在批 1（人点驱动＝simulated 基座的交互形态）——本批**不再有实例形态改造**：排练面板（步单复用 CLI 设计物：动作→新增/离场→路由→变量→事件；扮演提示「轮到 u_manager」；动作按钮取 available_actions）；画布高亮当前节点+已走路径；变量注入表单。
- 已决（⑤ **免迁移**）：rehearsal 并入 simulated——交互排练只是同一基座的第三种驱动形态（机器人/决策清单/人点），不是第三种实例形态，布尔列不动。
- 已决（④）：排练实例默认保留供回看、提供一键清理。
- **落地实录**：内核 `engine/rehearsal.py`（start_rehearsal/perform_rehearsal_step/
  rehearsal_state——步单四面板=待办+已办+路由+变量血缘与等待点；步种类分派表而非
  kind 分支）；`RuntimeService.start_simulation`（只启动不驱动，simulate/排练/场景
  共用，拆 simulation_start.py 守行数）；web `rehearsal_routes.py` 三端点
  （POST definitions/{code}/rehearsal · POST rehearsals/{id}/step · GET rehearsals/{id}，
  校验门槛与 simulate 同口径）；designer `RehearsalDialog`（发起人+起始变量 → 逐步
  办理：动作按钮取 available_actions、意见/变量、驳回落点、转办/加签/协办对象、
  等待节点「消息到达/超时」两键；步单四面板实时呈现；只读模式同样可用）。
  候选池任务自动认领（认领是流程细节不是演练关卡）；跨实例任务拒绝（树校验）。
- 测试：tests/semantics/test_rehearsal_scenario.py 五例 + tests/test_web_rehearsal_api.py 三例。

### 批 4 · 场景回归（脚本 + 断言 + 存储 + CI）✅ 已落地（2026-10-16）

- **场景格式**：剧本 steps + controls + assertions；断言=业务效果口径（终态/到访/未到访/任务行存在/变量值/路由选中边），期望值支持字面量+CEL 表达式；`$schema` 版本化（如 flowork-scenario/1）。
- **运行器**：`run_scenario(定义, 场景)` → 逐步结果+断言红绿（复用批 1/2 的执行器）；web `POST /api/definitions/{code}/scenarios/run`；CLI `flowork scenario run <定义> <场景文件>`（CI 口，退出码非零即失败）。
- **存储**：`wf_designer_scenario` 表（迁移）；designer 场景面板（列表/存/跑/红绿/最近一次结果与时刻）。
- 测试：场景运行器矩阵（直通/驳回回退/超时改派）、断言能力表、CLI 退出码、web 契约。
- 已决（⑥ **都支持、显式选择**：草稿主场景与「对照线上」并存；⑦ **不做发布门禁**——发布确认框显示场景红绿提示即可，行业无门禁先例）。
- 全量决策词（拍板①）的直接收益：场景 steps 与演练剧本**完全同构**（node/by/action/参数一比一），一份格式三消费者。
- **落地实录**：`engine/scenario.py` + `scenario_checks.py`（运行器与评估面拆分守行数）——
  步骤三类（任务动作十词表 / `{wait, result}` / `{clock}`，与演练剧本同构）；
  断言六型（final_state / visited(contains|not_contains) / task(node×outcome×by×count) /
  variable(equals|in|op+value) / route(selected) / pending(empty|node)）业务效果口径；
  覆盖照明（节点/边 covered vs missing，非门禁）；报告含逐步执行/断言红绿/覆盖/notes。
  存储 `wf_designer_scenario`（迁移 0007，设计器专属资产）+ `engine/scenario_store.py`
  （CRUD + last_ok/last_run_at/last_summary 列表预览）。接口：web 六端点（列表/保存/
  取/删/存跑/直跑）+ CLI `flowork scenario run <定义> <场景> [--deploy] [-v]`（全绿 0 /
  断言失败 1 / 脚本错误 2＝CI 口）。designer `ScenarioDialog`（左列表红绿点+步数断言数+
  上次结论；右 JSON 编辑+保存+运行；断言表红绿+覆盖照明+注记）。
  示例：examples/04_expense_scenario.json（报销全链路七断言，CLI 实测全绿、覆盖 8/8）。
- 测试：tests/semantics/test_rehearsal_scenario.py 十例 + tests/test_web_rehearsal_api.py 七例
  + 契约快照九端点。

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
