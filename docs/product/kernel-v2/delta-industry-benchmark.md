# A1–A11 行业对标：主流开源引擎的做法与最佳实践

| | |
|---|---|
| 日期 | 2026-10-09 |
| 对象 | Camunda 7（7.23/7.21）、Camunda 8（含 Zeebe ≥8.2）、Flowable 7、Activiti、jBPM 7.74、Temporal、SpiffWorkflow、Netflix Conductor/Orkes，以及 BPMN 2.0 XSD、WS-HumanTask 1.1、BPSim |
| 方法 | 官方文档 + **引擎源码逐行核对**（沙箱内 `docs.camunda.org` 不可达，Camunda 7 结论均以 `camunda-engine-7.23.0-sources.jar` 与 GitHub 源码为准，权威性高于手册）+ 官方论坛工单 |
| 定位 | [对标明细.md](./对标明细.md) / [对标明细详设.md](./对标明细详设.md) 的行业依据篇。**含三处对前两篇建议的推翻/修正**（见第 0 节标 ⚠ 的行） |
| 与既有调研的分工 | 仓库已有《主流流程引擎借鉴分析》（架构层：Command 管线、Identity Link、历史分级等）与《内核能力基准调研》（七维评分卡）。本篇只回答一个问题：**A1–A11 这些具体机制，主流引擎到底怎么做**——不重复架构层结论 |

---

## 0. 结论速览（行业答案 × 对原推荐的处置）

| # | 机制 | 行业答案一句话 | 对原推荐的处置 |
|---|---|---|---|
| A1 | 节点级动作权限 | **BPMN 无此位置，主流引擎都不做**。最近的是 jBPM 的 (操作×状态×身份) 声明表（引擎级，非节点级）；C7/C8 是授权模型不是动作模型 | ⚠ **修正形态**：不做节点级配置，做 (state × task_type) 声明表；"返回 allowed_actions 数组"是行业空白，可作差异化 |
| A2 | 撤回下游守卫 | **完成不可变是五家零例外的铁律**；撤回=取消当前+新建上游实例，不是状态回滚；"下游未处理才可撤"业界**零实现** | ✅ **确认并锐化**：撤回应提为特权操作；守卫语义 flowork 首创；抄 C8 的"显式拒绝" |
| A3 | 复活"最近一轮" | **轮次在主流引擎里根本不存在**（`loopCounter` 是本次激活的位置索引，重入归零；跨引擎 0/1 起都不一致） | ✅ 确认：默认"最近一轮"；另发现 flowork 已有做这件事更好的数据基础（`token_no`），主流反而做不到 |
| A4 | 协办随主任务清理 | 四种做法：不做（C7，产生孤儿）/ 隐式删（Flowable，不可配）/ 命名策略（jBPM，默认**不**清理）/ 取消剩余开关（C8）；共同点：**必须与主任务终态同事务** | ✅ 确认：抄 jBPM 的形状 + Flowable 的默认值；新增"意见收齐才放行"（jBPM 反向级联）可用 `exit_reached` 表达 |
| A5 | 工作日历 / 耗时口径 | **历史耗时全行业一律墙钟**；**没有任何引擎按人算 due date**（C7 那个 `Task` 参数是死参数，Flowable 主动删了）；jBPM 是唯一有真工作日语义的引擎 | ⚠ **推翻**：不做"按发起人/按办理人各算一份"双口径——那是 对标产品 私货，主流零实现且 C7 结构性做不到。历史保持墙钟；端口要么删要么只接 deadline 投影 |
| A6 | 参与者上限 | **没有引擎有业务级人数上限**；上限都是基础设施级（Zeebe batch record、Temporal 51200 事件/2000 挂起）；**全样本零静默截断**（要么扛、要么明确报错、要么失败重试） | ✅ 确认并锐化：主机制是异步分批物化（flowork 已有 Job 面）；护栏"整笔拒绝+落痕"；关键约束转移到"`item_count` 必须保持计划总数语义" |
| A7 | 后审批自动通过 | **全行业零实现**（`lookAheadParticipants` 全域零命中）。语义上不可判定（未来的变量现在不存在），失效模式是**审批链空洞且审计不可见** | ⚠ **推翻**：建议**不做**取人/路由决策，只做 `anticipate` 只读提示；优先加强已有的向后看三策略 |
| A8 | 前置加签/咨询 | C7 的 `delegateTask/resolveTask`（owner/assignee 轮转 + PENDING/RESOLVED）是唯一的"两轮形态"；jBPM 的 delegate 是转手不回来；C8 没有 delegation | ⚠ **修正形态**：不抄 assignee 轮转；用派生 advisory 子实例（独立记录/时限/意见面）；"意见不占票"要显式建模 |
| A9 | 多实例子流程 | BPMN 标准**原生有** `loopDataOutputRef/outputDataItem`，但 C7 没实现（输出**后写覆盖**——正是 flowork 现存的洞）；Zeebe 的 `inputCollection/outputCollection` + **index 对齐**是业界最清晰契约；Flowable 有插件式聚合器 | ✅ 确认+定稿：按 Zeebe 契约设计；`completionCondition` 只做提前收工不做失败机制；中止时**不聚合 partial**（可测试契约） |
| A10 | 模拟试跑 | 三个物种：**令牌模拟**（零副作用、条件不求值、人指定走哪条）/ **打桩测试**（真跑+mock+跳时间）/ **真环境测试**（有副作用，官方只能警告）；jBPM 是唯一内置 BPSim 统计模拟的引擎；"模拟实例"一等公民是行业空白 | ✅ 确认并重构：`dry_run` 拆两层；模拟实例隔离 + 硬性禁写；"模拟不验证表达式"文档化 |
| A11 | 引用完整性校验 | **组织实体引用全行业不校验**（C7/C8/Flowable/jBPM 全推给运行期）；但**表达式语法** Zeebe 部署期硬失败、**同发布单元资源引用** Zeebe 校验 | ⚠ **修正强度**：默认 WARNING + strict 可选（不要默认拦发布）；补 L3 层（同单元引用 + handler 注册）；抄 Flowable 的 `ValidationError` 形态（分级+定位） |

三处推翻（A5 双日历、A7 做取人决策、A1 节点级配置形态）的详细说明见各节，修正后的建议已回写两篇 delta 文档。

---

## 1. A1 · 节点级动作权限

### 主流引擎怎么做

**BPMN 标准没有位置。** Camunda 的 BPMN 模型类 `UserTask` 暴露的标准槽位只有 `implementation` 与 `renderings`（UI 呈现描述），其余全是 `camunda:*` 扩展——`assignee`、`candidateGroups`、`candidateUsers`、`dueDate`、`followUpDate`、`formKey`。标准覆盖了"谁办"和"怎么办"，**唯独没有"能办哪些动作"**。这不是遗漏，是设计取向：动作集合被视为引擎能力，不属于流程模型。

**jBPM 是唯一把「动作 × 状态 × 身份」写成显式声明表的引擎。** `operations-dsl.mvel` 用 MVEL 声明每个操作在哪些状态可用、允许哪些角色、办完进入什么状态——例如 `Operation.Complete` 要求 `status=[InProgress]`、`allowed=[Owner, BusinessAdministrator]`、`newStatus=Completed`；`MVELLifeCycleManager.isAllowed()` 逐项判定，不通过抛 `PermissionDeniedException`。18 个操作（Claim/Start/Stop/Release/Suspend/Resume/Skip/Delegate/Forward/Complete/Fail/Register/Remove/Activate/Exit/Nominate/Modify/View）× 6 个身份（Owner/Initiator/PotentialOwner/BusinessAdministrator/TaskStakeholders/Anyone）。**但它是引擎级全局配置，不是按节点配置**——单任务想收敛动作集只能靠局部开关。

**Camunda 7 是授权模型，不是动作模型。** 权限枚举里任务相关的有 `TASK_WORK`（办）与 `TASK_ASSIGN`（分派/认领），分挂在 `Resources.TASK` 或 `Resources.PROCESS_DEFINITION` 上。这是"谁能办"，不是"这个节点允许什么动作"。

**Camunda 8 是唯一在 API 契约里声明「每个动作需要什么权限」的引擎。** 每个 REST operation 挂 `x-required-permissions`，用 `anyOf` 表达"过程级或任务级任一满足"；`USER_TASK` 资源上是 `READ/UPDATE/CLAIM/COMPLETE`。**注意两个坑**：① 过程级权限命中则短路任务级授权（官方警告 `READ_USER_TASK`/`UPDATE_USER_TASK` 是"对全流程所有用户任务的宽授权"）；② `x-required-permissions` 是"调用该端点所需权限"，**不等于"该任务当前是否可办"**（还叠加状态约束），前端只按权限渲染会渲染出必然 409 的按钮。

**Flowable 只有动作枚举。** `TaskActionRequest` 定义可提交的动作串（complete/claim/unclaim/delegate/resolve），描述端点能做什么，不表达任务上允许做什么。

**运行时返回"允许动作清单"——三家都没有。** 我逐字段核了 C7 `TaskDto`、C8 `UserTaskResult`、Flowable `TaskResponse`：**没有一个带 permissions/allowed-actions 字段**。前端/UI 层按"权限契约 + 任务状态"自己算。

### 坑与边界

- C8 的 `x-required-permissions` 只算权限不算状态（见上），Tasklist 曾因用错命令（`ASSIGN` 代替 `CLAIM`）击穿权限模型（camunda/camunda#51814）——**动作权限必须落到具体 command 上**。
- C7 的 TaskListener 是观察型钩子，不是权限门禁；用它"禁止某动作"只能靠抛异常，属 abuse。C8 的 user task listener 是唯一的阻塞型门禁（`creating/assigning/updating/completing/canceling`），代价是监听器卡住会让请求 504。
- jBPM 的 `Operation.Modify`/`View` 把**终态也纳入 status 列表**——"对已完成任务能做什么"在 jBPM 里有形式化位置。

### 对 flowork 的结论（⚠ 修正形态）

**不要照 对标产品 做节点级 `permitted_actions` 配置。** 行业事实是：BPMN 无位置、主流不做节点级、唯一做的 jBPM 是引擎级表。修正为三条：

1. **按 `(state × task_type)` 定义声明式动作表，不按节点。** flowork 的 `TaskRow.state ∈ {ready, completed, cancelled}` × `task_type ∈ {approval, assist}` 正好与 jBPM 的表结构同构——动作可用性天然是"状态问题"（终态不能重办、assist 不能驳回），不是"节点问题"。节点级配置会在节点数增长后失控，且和已有的 `_assert_operable`（实例态守卫）职责重叠。
2. **`allowed_actions` 作为 API 响应返回——这是行业空白，值得做。** 前提：服务端必须把"状态"和"目录权限" **两条都算完**再返回（避开 C8 那个"渲染出必然 409"的坑）。这也让 REST/MCP/TaskInfo 三面同构免费受益（`TaskInfo` 加一个数组字段）。
3. **抄 C8 的动词切分**：把"办"（complete/reject）与"分派"（transfer/claim/assist/add_countersigner）分两组权限位，加签/协办天然落"分派类"。**不要**抄 C8 的授权表结构（resource/permission 枚举 + 属性 matcher + 过程级短路）——那是多租户 SaaS 的复杂度；flowork 的"谁能办"来自目录（M-A），引入第三张授权表会让问题分裂到两个数据源。

---

## 2. A2 · 撤回的下游守卫

### 主流引擎怎么做

**结论先行：没有任何主流引擎支持"撤回自己已办完的任务"作为一等业务操作。** 完成即终态；唯一的杠杆是**流程实例修改（process instance modification）**，它是面向管理员的修复工具，且只作用于活动实例。

- **Camunda 7 `ProcessInstanceModification`**：指令集 `startBeforeActivity / startAfterActivity / startTransition / cancelActivityInstance / cancelTransitionInstance / cancelAllForActivity`。取消只对**活动实例**成立——**对已完成的活动没有对应指令**（想回去只能 `startBeforeActivity` 再开一个）。取消向上传播（"Canceling an activity instance propagates to parent activity instances that do not contain other activity instances"）。指令按提交顺序执行，**顺序会改变结果**。文档对动机的定性最值得注意："While such flexibility is not modelled as part of the process, process instance modification allows to **correct** the running process instance."
- **Camunda 8**：`activate / move / terminate` 三指令。**"Terminating an inactive element instance causes rejection of the command."** ——"已完成的不许再取消"是**显式拒绝**。级联规则明确：终止子流程→终止其全部活动实例；终止 call activity→终止子流程实例；终止最后一个活动实例→流程实例终止。**终止用户任务不触发其 canceling 监听器**。指令顺序与 C7 **相反**（先全部 activate 再全部 terminate）。权限是独立高阶的 `MODIFY_PROCESS_INSTANCE`，且只有 `ACTIVE` 状态实例可改。
- **Flowable `ChangeActivityStateBuilder`**：`moveActivityIdTo` 等。守卫来自 `resolveActiveExecutions`——**只能从"当前活动在那个 id 上"的执行出发；已完成的 id 上根本没有执行，直接抛异常**。测试给出确定答案（`ChangeStateTest.testSetCurrentActivityBackwardForSimpleProcess`）：完成 firstTask → 停在 secondTask → `moveActivityIdTo("secondTask","firstTask")` → 事件序列严格是 `ACTIVITY_CANCELLED(secondTask)` 然后 `ACTIVITY_STARTED(firstTask)` → 再 complete(firstTask) **又回到 secondTask**。即：**已完成的活动不会被回滚，只有当前活动被取消；回上游后向下走会把下游整条重跑，产生全新实例**。幂等/防重完全由应用负责。
- **Temporal**：cancel 是协作式（Cancelled Failure 投递给工作流代码）、terminate 是强制式（不跑清理，"partial writes stay written"）。**取消已完成的 activity：不存在**。且取消必须靠 Heartbeat 投递——"An Activity that doesn't Heartbeat can't receive a Cancellation"。Temporal 的"回到某点重做"正解是 Reset（终止当前 Run，从历史选定点起一个**新 Run**）。
- **Netflix Conductor**：只有"从最后一个失败任务重试"（`retry`），参数 `resumeSubworkflowTasks`/`retryIfRetriedByParent` 暴露了"父子都重试会重复副作用"的真实故障模式。**任意点回退（rewind）在 OSS 里连生产 API 都不是**（ROADMAP 里与 breakpoints 并列，定性为调试器）。
- **SpiffWorkflow（Python，最可比）**：`cancel()` 对已完成任务是 no-op（只级联给孩子）；`_ready()` 明确拒绝复活 `COMPLETED`/`CANCELLED`。要做"重做"只能构造新 task。

**"下游未处理才可撤"这类策略配置——不存在。** C7/C8 的 modification 没有策略参数；Flowable 的 builder 没有策略开关；Temporal 的策略枚举只有 `ParentClosePolicy`（管子工作流）；Orkes 只有两个布尔。

### 坑与边界

- C8 修改与任务存储会不同步（#32953/#33111/#64645：改了流程但待办列表里那条任务还在）；修改历史缺统一审计视图（#18376 open）。
- **顺序语义跨引擎相反**（C7 提交序 vs C8 先激活后终止）——若同时对齐两家必然出错，必须在自己的 API 层固定一种。
- "最后一条活动被取消则整个实例被取消"（C7）与"两条指令之间不算取消"是反直觉组合。
- Temporal 的 heartbeat 依赖是隐藏 SLA：不 heartbeat 的 activity 收不到取消，会跑完。

### 对 flowork 的结论（✅ 确认并锐化）

flowork 的 `TaskRow` 三态 + 只追加历史 + 乐观锁，天然落在"完成不可变"这条行业铁律上——**不要为了 withdraw 破坏它**。四条：

1. **抄 C8 的"显式拒绝"**：withdraw 打到下游已有进展时，返回明确的领域错误（携带"哪个下游任务/哪一轮已产生进展"的结构化信息），而不是静默改成别的语义。
2. **抄"回上游 = 取消当前 + 新建上游实例"**：flowork 现有 `_withdraw` 已经是这个形态（取消未完令牌 + `activate` 重建），**不要**改成"把 TaskRow 从 completed 改回 ready"——那违反只追加历史，也会让加签链引用到语义被篡改的记录。
3. **把 withdraw 提到特权操作面**（对齐 C8 的 `MODIFY_PROCESS_INSTANCE`）：与 pause/resume/terminate、resolve 五动作同层，**不是**与 complete/reject 并列的普通任务动作。这条同时回答了 A1 里"动作表"该怎么分组。
4. **加"下游未处理才可撤"守卫——flowork 首创**。行业零实现，但 flowork 的判定材料已在库里（下游 token 是否有已办任务 = 对 `wf_task_history` 的现查，与 `tasks/facts.py` 的 F2 现查模式同源，不引入新表）。做成显式策略枚举（`reject_if_downstream_progressed` 默认 / `cascade_cancel_downstream` / `force` 特权），默认拒绝。

**另抄一条**：SpiffWorkflow 的 `FINISHED_MASK` 守卫——在状态迁移入口加单点检查，终态任务只能级联影响子任务、自身不可再迁移。

---

## 3. A3 · 复活"最近一轮"

### 主流引擎怎么做

**没有任何引擎有"驳回"原语，也没有"复活原办理人"的概念。** 驳回在主流里的实现是"回到上游活动"，重入时**办理人按定义重新解析**（表达式对当前变量重新求值），**不会**恢复历史办理人。「最近一轮 vs 历史上所有人」这个开关在主流引擎里**根本不存在**——因为引擎不保存"这一轮该谁办"。

**多实例的轮次区分——所有引擎都只有"实例身份"，没有"轮次编号"：**

| 引擎 | 计数器/索引 | 存放位置 | 基数 | 重入行为 |
|---|---|---|---|---|
| Camunda 7 | `nrOfInstances`/`nrOfActiveInstances`/`nrOfCompletedInstances`；`loopCounter` | body 作用域；实例局部 | 0 起（`for i in [0, n)`） | 重建 body、计数归零 |
| Flowable | 同上（`elementIndexVariable` 可改名） | 同上 | 0 起 | 同上；**移出 MI 后 `loopCounter` 变 null** |
| Camunda 8 | `numberOf*`（**不是流程变量**，仅 `completionCondition` 可见，优先级高于同名变量）；`loopCounter` | — | 文档称 **1 起** | 同上 |

**关键结论：这些计数器是"本次激活"的，重入活动会重建 body、计数器归零。** "第几轮"在引擎里不是持久标识——想区分轮次必须应用自己存变量。

**Flowable 的 MI 重做实测**（`ChangeStateForMultiInstanceTest`，20 个用例）：把执行移出 MI body → body 消失、已完成轮次的计数消失、`loopCounter` 变 null（`assertThat(...).isNull()`）——**移出 MI 就是整体放弃该 body，不管已完成的轮次**。

**动态加实例（真正的"运行时加签"）= Flowable 独有**：`addMultiInstanceExecution(activityId, parentExecutionId, variables)`——把 `nrOfInstances` 加 1、建子执行；守卫是"同名 activityId 有多个 MI 执行时必须显式传 `parentExecutionId` 消歧"，否则抛 `Multiple multi instance executions found for activity id`。C7 无此 API（检索 `addInstance`/`addMultiInstance` 零命中）；C8 明确不支持（"Add (activate) modifications are not supported inside multi-instance subprocesses"）。

### 坑与边界

- `loopCounter` 跨引擎基数不一致（C7/Flowable 0 起、C8 文档 1 起），命名前缀都不同（`nrOf` vs `numberOf`）——任何"对齐多家"的适配层都会在这里出错。
- `loopCounter` 是 execution-local、不跨实例可见——看板想显示"第 3 轮"不能读它。
- **歧义守卫会抛异常**：回退后重入同一 MI 活动恰好会制造"同一 id 多个 MI root"——这是"重做 + 加签"叠加时的真实雷区。
- C8 的 `completionCondition` 达成时**剩余活动实例被终止**（"remaining active instances are terminated"）——重做时上一轮未办实例已是"已终止"而非"待办"，复活它们与复活已办同属禁区。

### 对 flowork 的结论（✅ 确认，并发现 flowork 的基础更好）

**主流引擎做不到这件事（没有轮次），flowork 有现成数据基础：** `TokenRow.token_no`（实例内自增）+ `TaskHistoryRow.token_id`。A3 的修法不变（取目标节点 `token_no` 最大的已完结令牌、按 `token_id` 圈历史），并明确三条：

1. **默认"最近一轮"**——对齐"重入按定义重新解析"的最小惊讶原则，避免会签基数随驳回次数膨胀（5 人会签驳回 3 次变 15 人）。"历史上所有人"作显式可选策略。
2. **抄 Flowable 的"显式消歧"守卫形态**：`add_countersigner` / 驳回重做时让调用方显式指出目标轮次或目标 token，不靠隐式推断。这解释了为什么"重做 + 加签"叠加需要消歧——flowork 的 `root_task_id` 链正是为此存在，但要保证写入侧完整（A4 的修链）。
3. **注意 flowork 与行业的一处语义差异，建议在 spec 写明**：主流引擎重入时**重新解析办理人**（人可能换），flowork 与 对标产品 都是**复活原办理人**（同一人再来一次）。flowork 的更贴合国内审批语义（驳回就是退回给原审批人），但要在 spec 里声明这是**刻意的差异**，并保证"原办理人已不可用"有兜底路径（现有逻辑：handlers 为空时走正常解析）。

---

## 4. A4 · 协办随主任务清理

### 主流引擎怎么做

**BPMN 没有"子任务"这个构件。** 三个相关槽位都不是"附属任务"：ad-hoc subprocess（C8，最接近）、boundary event（外部事件驱动）、multi-instance（加签的对应物）。

| 引擎 | 父子关联 | 主完成清子 | 特点 |
|---|---|---|---|
| Camunda 7 | `parentTaskId`（**纯引用字段**） | **无**（文档检索 subtask/parent task 生命周期零命中） | 会产出孤儿任务，且无内建清理手段 |
| Camunda 8 | 无（用 MI / ad-hoc） | `cancelRemainingInstances`（**默认 true**） | 管的是 MI/ad-hoc 内层实例；是可配开关 |
| Flowable | `parentTaskId` + `getSubTasks` | **有，隐式且不可配**——`completeTask` → `internalDeleteTask` → `handleRelatedEntities` 无条件递归删子任务 | 完成时 `cascade=false`（历史置结束时间而非删除） |
| jBPM | `parentId` + `SubTasksStrategy` | **命名策略，默认 NoAction（不清理）** | `EndParentOnAllSubTasksEnd`（子全完→**自动完成父**）、`SkipAllSubTasksOnParentSkip`（父被跳过→跳过所有子） |
| Temporal | child workflow / activity | `ParentClosePolicy`：ABANDON / REQUEST_CANCEL / TERMINATE（默认 TERMINATE），触发于父 Closed | 策略最完整；activity 层**无条件**取消、不可配 |

**加签在各引擎的对应物**：Flowable 的 `addMultiInstanceExecution` 是业界唯一运行时动态加签 API；C7/C8 都没有。

### 坑与边界

- Flowable 的隐式清理**会触发子任务的 DELETE 监听器**——如果监听器有副作用，"主任务完成"会静默触发一串子任务通知。
- `cascade` 参数用错会**永久丢历史**（true=删历史）。flowork 的"只追加"约束在这里反而是护城河。
- jBPM 默认 `NoAction` 是静默的（"以为会自动清"是最常见误解）；其反向级联用 `instanceService.complete(parentTask.getId(), "Administrator", data)`——**以 Administrator 身份完成父任务**，留下假办理人。flowork 的"所有操作带 actor 归因"会直接拒绝这种写法。
- Temporal 的 `TERMINATE` 是默认值：不显式设置，父一 Closed 子就被强杀且无清理。
- C8 ad-hoc 的三条硬限制：elements only activated on entry（**临时加协办做不到**）、`cancelRemainingInstances` 默认 true（会杀掉在途协办）、官方禁止手改 `adHocSubProcessElements`。

### 对 flowork 的结论（✅ 确认 + 一个高价值增量）

1. **抄"清理与主任务终态同事务"**（Flowable 在命令上下文内 / C8 "apply all or nothing"）。flowork 单库 + 乐观锁天然支持，这条应做成**硬不变式**：主任务进入终态时，其 `root_task_id` 链上的 assist 任务必须在同一事务内进入终态。这是 `tests/edge` 竞态套件该覆盖的场景。
2. **抄 jBPM 的命名策略形状，但把默认从 NoAction 改成"清理"**（Flowable 的默认）。对"协办"这类纯附属任务，默认清理才符合直觉。
3. **抄 C8 的教训：把这个行为做成显式字段**（哪怕默认值与"不可配"一致），而不是埋在代码里。它同时解决"A2 一票否决后残留 ready 任务"的问题——两个问题共享同一个机制，应一起设计。
4. **【增量】jBPM 的 `EndParentOnAllSubTasksEnd` 反向级联——本次调研性价比最高的吸收点。** 这是业界唯一的"子驱动父"，恰好对应真实刚需："协办意见未齐，主任务不可办"。flowork 表达它非常自然：复用 `exit_reached`（会签出口统一批的那套 CEL 计数），把"已终态 assist 数 vs item_count"写成一个出口公式——**同一个出口求值器，两种用途**。建议作为可选策略落进 A8 的 consult 设计。
5. **不要抄**：Flowable 的隐式删除（不可配、静默副作用、与只追加历史张力最大）；C7 的纯引用字段（有引用无生命周期必然产孤儿）；Temporal 的活动层"无条件取消不可配"（"主任务被撤但咨询意见已给出需留档"是常见诉求）；C8 的 ad-hoc（硬限制否掉临时加人）。

---

## 5. A5 · 工作日历与耗时口径（⚠ 推翻前一版建议）

### 主流引擎怎么做

先修正一条事实：**Camunda 7 没有 `businessCalendarName` 属性**（`camunda-engine-7.23.0-sources.jar` 全量 2266 个 java 文件检索：`businessCalendarName` 0 命中）。带这个名字属性的是 **Flowable**（`flowable:businessCalendarName`，用于 userTask 与 timerEventDefinition）。

| 引擎 | 机制 | 日历按什么选 | 内建工作日语义 | 按人算 |
|---|---|---|---|---|
| Camunda 7 | `BusinessCalendarManager` 注册表，三个内建（dueDate/duration/cycle） | **计时器类型** | ❌ 纯墙钟（`DateTime.plus(Period)`），工作日靠宿主实现接口 | ❌ **结构性不可能**（见下） |
| Flowable | `businessCalendarName` 节点级属性 | **节点级、可表达式** | ❌ 同纯墙钟 | ❌ 接口已删 `Task` 参数 |
| Camunda 8 | 无 | — | ❌ 完全没有（官方论坛确认"no direct replacement for Business Calendars"） | ❌ |
| jBPM | `BusinessCalendarImpl` + properties | 引擎全局单例 | ✅ **唯一完整实现**（周工作日/日工时/上下班时点/假日区间/时区，含真正的日历步进循环） | ❌ |

**关键证据（源码级）**：C7 的 `TaskDecorator.initializeTaskDueDate` 看似把 `Task` 传进了 `resolveDuedate(String, Task)`，但 ① `getBusinessCalender()` **硬编码**写死默认日历，无读取节点级名字的路径；② `UserTaskActivityBehavior.performExecution` 的顺序是 `insert() → decorate() → transitionTo()`，而 `decorate()` 内部 due date 初始化在 **assignee 赋值之前**——即 **C7 算 due date 时 task 的 assignee 还没赋值，这个 `Task` 参数在真实执行路径上是空的**。Flowable 干脆把参数删了。这是演进方向上的一次主动纠偏。

**历史耗时一律墙钟（三处源码级验证）**：C7 `DefaultHistoryEventProducer` 三处 `setDurationInMillis(end - start)`；Flowable `HistoricTaskInstanceEntityImpl.durationInMillis = endTime - createTime`；jBPM `ProcessInstanceLog.duration` 同族口径。

**"同一时长对不同人算不同工作日值"没有任何引擎实现**，也没有引擎为此预留字段。唯一真实客户诉求（Camunda forum 45961）是"外部系统有工作时间 + 个人日历，**我们自己算完再喂给引擎**"。

### 坑与边界

1. **`P1D` ≠ 1 个自然日，取决于谁解析**：jBPM 下 `P1D` = 8 小时工作时间；C7/Flowable 下 = 24 小时墙钟。同一 BPMN 文件跨引擎语义漂移。
2. **日历是"往前投影"函数，不可逆推**——不能用日历把 actual duration 折算成工作日（区间含非工作时段、跨假日、办理人实际在非工作时间操作）。这是"历史耗时不用日历口径"的根本原因，**不是遗漏**。
3. Flowable 的 `businessCalendarName` 拼错不报错、静默回落——**发布期校验名字已注册**是它缺的一环。
4. 改派/加签后 due date 不重算（C7/Flowable 均只在创建时算一次）；暂停/挂起不顺延。

### 对 flowork 的结论（⚠ 推翻前一版）

**撤销"双日历口径"建议。** 前一版把 对标产品 的"按发起人日历 / 按办理人日历各算一份耗时"当作洞察推荐吸收——行业对标否定它：全行业零实现，C7 结构性做不到（死参数），Flowable 主动删除参数。两个口径会产出两个不可比的数字，且无法在历史里交代清楚。修正为：

1. **历史耗时永久保持墙钟**（`cost_seconds` 现状正确，不要动）。若业务确需工作日视角，做成**查询期派生**（字段名带口径：`cost_business_minutes`），**绝不落库、绝不与墙钟同字段**。
2. **死端口二选一，不要悬着**：要么删 `WorkCalendar`（与 Notifier/ResourceProvider 同例），要么**只接一个点位**——deadline/due date 投影。若接线：照 **Flowable 而非 C7**——`business_calendar_name` 作**节点级可选属性 + 引擎级命名注册表 + 缺省自然时间**，并配 **validator 发布期校验名字已注册**（这正是 Flowable 缺的一环，flowork 补上就是净增量）。
3. **实现单一日历，不做"按人各算一份"**。若必须支持个人日历，落在适配器层：`WorkCalendar` 实现内部按办理人查表，对外仍只暴露一个 due_at，并在事件里记录"用了哪本日历"（可追溯）。
4. **语义钉死五件事**（写进 spec 或端口 docstring）：起算锚点（任务创建时刻；参考 Flowable `IN_PROGRESS_TIME_` 的"开始处理后重新起算"能力，可选后置）；时钟唯一出口（`flowork.clock`）；时区归属（适配器，内核保持 naive UTC）；假日来源（注入）；挂起是否冻结（建议不冻结，与主流一致）。

---

## 6. A6 · 参与者上限

### 主流引擎怎么做

**没有任何引擎提供"参与者数量"这种业务级可配置上限。** 上限若存在，一定是基础设施级，理由是**持久化/稳定性**：

- **Camunda 7**：无上限。`ParallelMultiInstanceActivityBehavior.createInstances` 是直接同步循环——**一个事务里创建 N 个子执行**，无任何 limit。
- **Flowable**：无上限。但有官方实测数据（forum 44263，10k 个实例）：内存逼近 2GiB 上限、大量 `OptimisticLockingException`、**并行比串行慢 4–5 倍**（原因是并行回写父实例集中撞版本号）。`flowable:parallelMultiInstanceAsyncLeave` 是汇合侧优化，不是分批创建开关。
- **Camunda 8**：唯一"硬上限 + 明确报错"。Zeebe 把大集合**分批**（chunking，8.2.5 修复了之前超大 MI 触发 `maxMessageSize` 导致实例被黑名单化且 Operate 不显示的事故）。官方 chaos 实验：20k 成功（约 10 分钟）；200k 背压阻塞；250k 明确错误 `EXCEEDED_BATCH_RECORD_SIZE`；2M 时错误在 NGINX 层劣化成 502。
- **Temporal**：显式上限 + 明确失败。Event History 51,200 events / 50MB（10,240 起告警）；未完成 Activity/Child Workflow/Signal/Cancel 各默认 **2000**，超限"the Workflow Task Execution will fail and get retried"。理由写得很直白：每个进行中的 Activity 都在 mutable state 里生成元数据，"Too many entries ... causes unstable persistence"。

**全样本零静默截断。** 三种行为：扛到性能崩溃（C7/Flowable/Spiff）、硬上限明确报错（Zeebe）、硬上限失败重试+替代方案（Temporal Continue-As-New）。因为截断会让"少批了人"无人知晓——审批场景最不可接受的失效模式。

### 坑与边界

- **"创建 N 个待办"的真正成本不是 N 次 INSERT，而是乐观锁冲突**（Flowable 实测）。这条对 flowork 直接适用：并行会签 N 个大时，N 个待办同属一个 token，完成时都更新同一批行。
- Zeebe 的"实例卡死且 UI 看不见"是运维灾难级失效——**超限必须留下可见痕迹**。
- 错误信息会在最外层网关劣化——"超限报错"要保证在所有代理层之外仍可辨识。
- `completionCondition` 会删实例（语义是"丢弃"），如果误配会静默减少审批人数——这是配置侧的静默截断，需设计器/校验兜住。
- 串行 MI 的出口语义易错：`nrOfActiveInstances` 恒为 1，`nrOfInstances` 是计划总数而非已建数——用错变量会得到"永远不满足"的完成条件。

### 对 flowork 的结论（✅ 确认并锐化）

1. **主机制是异步分批物化，不是砍人。** flowork 已有 Job 面（CAS 原子认领、租约 worker、`flowork tick`）——分批物化待办的基础件现成，这是相对 C7/Flowable 的现成优势。`item_count` 先落**计划总数**，按批（如 200/批）建 `TaskRow`，分批态用 token 上的"已物化数"表达。
2. **护栏"可配置 + 整笔拒绝 + 落痕"**：超限时抛专用错误（引擎错误二分体系）、在事件里落痕，**绝不 accept-and-truncate**。
3. **【关键约束】`item_count` 必须保持"计划总数"语义**——现有出口公式引用 `task.item_count`，分批若改变它，会串出"出口永远不满足"或"提前满足"两类 bug。参照 C7 的 `nrOfInstances`（总数）vs `nrOfCompletedInstances`（完成数）的口径分离，在 spec 写明。
4. **【性能】大 N 并行会签要警惕锁竞争反转**：把聚合计数做成**一次 UPDATE**（flowork 的 `item_count/approve_count` 聚合设计天然适合），避免 N 次独立更新。
5. **告警阈值先于硬限**：学 Temporal 分级；在 `anticipate`/dry-run 里预判"本节点将产生 N 个待办"，把风险暴露在设计期。

---

## 7. A7 · 后审批自动通过（⚠ 推翻前一版建议）

### 主流引擎怎么做

**这个机制在所有调研引擎中都不存在。** GitHub 全域检索：`lookAheadParticipants` 零命中；`skipAlreadyApproved` 仅 2 处非引擎代码。**"查询未来参与者"不是任何引擎的查询面能力。**

各家最接近的机制：

- **Camunda 7**：`taskCandidateUser` / `taskCandidateGroupIn` / `taskAssigneeNotIn`——全是**查询过滤器**，作用于已存在任务；候选人解析本身无排除能力。
- **Camunda 8**：官方文档给的惯用法是**模型侧排除**：`= remove(reviewers, book.author)`——在表达式里自己算，且**激活时求值，只能看到已存在的变量**（无前视）。
- **Flowable**：`taskCandidateOrAssigned`，无排除。
- **jBPM**：`excludedOwners`（WS-HumanTask 标准概念，黑名单/拒绝访问，**语义相反**）+ `skipable`（任务可被跳过，第三种含义）+ Autoclaim/swimlane（**同一人继续做**后续任务，也是相反）。
- **Temporal / SpiffWorkflow**：无参与者概念。

**社区共识：这是建模/用户代码问题，不是引擎开关。** 三个高度相关的 Camunda 论坛工单答案一致：
1. "如何禁止发起人自己审批"→ 官方推荐示例 `four-eyes-advanced`（**四人眼原则本身是示例代码，不是引擎功能**）；
2. "X 完成了第一个任务，不应再看到第二个任务"（本问题的教科书式陈述）→ 员工建议"在两任务之间加 Service Task 改 assignee"（**在后节点做减法，而不是在前节点跳过**）；资深用户描述了**恰好是 flowork `auto_pass_participated` 的做法**及其教训："It would start with the users in the group and subtract the user that had an earlier role ... That seemed elegant but **caused side effects when people were added to or removed from the candidate group**."
3. 官方 `four-eyes-advanced` 示例的实现：`TaskCompletionListener` 在任务完成时把当前用户写进变量，**发现同一人重复时抛异常**（fail-closed，在**后一个节点**做事后校验）。

### 为什么全行业不做（本节的核心理由）

**往前看需要"未来的变量"，而未来的变量现在不存在。** 求值未来节点的取人表达式，通常依赖：未来节点才会产生的变量（审批意见、金额）、尚未选定的分支、未来的组织目录状态。

**失效模式是决定性的**：若在 A 节点因"C 节点会由他审"而放行，而后来 C 的参与者因变量变化不再包含他——**这一环节没有任何人真正审批过，审批链出现空洞，且事后从历史里看不出来**（A 记的是 auto_pass，看不出"因为预期他会审 C"）。对一个以"actor 归因 + 只追加历史"为硬约束的引擎，这是最致命的失效模式。

### 坑与边界

- **会签算术污染（flowork 特有高危点）**：会签出口公式把 `item_count` 当分母。若上游把某人判为"跳过"而他本该在本节点投票，分母/票数会错。
- **候选组动态化的副作用**（David_Knapp 实测）：按"减去历史办理人"实时算候选集，会与组织目录变更耦合——**历史不可复现**。
- **术语混淆**：`excludedOwner`（禁止参与/权限）、`skipable`（任务可跳过）、four-eyes（禁止同一人连做）、Autoclaim（同一人继续做）——四个概念方向各异，别互相借名。事故归因高度依赖这几个词的准确含义。
- **委托口径**：flowork 的 `_delegation_replace` 在 `_apply_strategies` **之后**执行——策略比较用的是**请求人**，而 `_handled_anywhere` 取的是**实际办理人**（`row.assignee`）。这个口径目前没有被文档化。

### 对 flowork 的结论（⚠ 推翻前一版：建议不做）

前一版建议"`UserTaskSpec.auto_pass_later: bool` + 按可达性解析后置节点的人 + WARNING 兜底"。行业对标后**推翻**——三个理由：① 全行业零实现（含拥有 `excludedOwners` 原语的 jBPM 也不做）；② 语义不可判定；③ 失效模式（审批链空洞 + 审计不可见）与 flowork 的只追加历史 + actor 归因硬约束正面冲突。修正为：

1. **不做取人/路由决策。** 把需求拆两类，各用已存在的正确工具：**真诉求大概率是"同一人不要重复审批"** → 用已有的 `auto_pass_participated` / `auto_pass_previous`（向后看，确定、可复现、可审计）；若不够，**优先加强这两个**（如按审批链而非全实例限定范围）。**"业务上确实想合并同一人"** → 落建模层（合并节点，或让下游节点的取人公式显式排除，照 C8 官方的 `remove(...)` 惯用法）。
2. **若一定要提供前视信息，只做成只读预判**：flowork 已有完美载体——`TaskService.anticipate`（纯读、不落库、docstring 自称"主流引擎无等价 API（AI 原生差异点）"）。正确形态：用**当前变量**沿路由投影未来节点、解析其取人表达式、返回"后续还会参与的人"作为**提示**（UI 显示"张三为后续节点审批人，本关可考虑免审"）；**状态与决策权仍在引擎 + 条件**（符合硬约束 #3），并明确标注"基于当前变量近似，变量变更后可能失效"（`anticipate` 的现有 docstring 已有同类谨慎口径，直接沿用）。
3. **顺带修一个文档洞**：委托口径（策略比较用请求人、历史记录用实际办理人）写进 spec 或 resolver docstring。

---

## 8. A8 · 前置加签 / 咨询

### 主流引擎怎么做

**BPMN 无"咨询/投票"原语。** 标准侧最接近的是 `adHocSubProcess`（无序活动集合，`cancelRemainingInstances` 默认 true）；WS-HumanTask 里有 `task stakeholders` / `business administrators`（"oversight and outcome"角色，但不是"咨询流程"）。

**Camunda 7 的 `delegateTask` / `resolveTask` 是唯一实现"两轮形态"的主流引擎**（源码逐字）：

- `DelegationState.PENDING`："The owner delegated the task and wants to review the result after the assignee has resolved the task. When the assignee completes the task, the task is marked as RESOLVED and sent back to the owner. When that happens, **the owner is set as the assignee so that the owner gets this task back in the ToDo.**"
- `TaskService.delegateTask`："the delegation state is set to PENDING. If no owner is set on the task, the owner is set to the current assignee. ... **Only the owner can complete the task.**"
- `TaskService.resolveTask`："Can only be called when this task is PENDING delegation. After this method returns, the delegation state is set to RESOLVED and the task can be completed."
- 实现：`delegate()` = `setDelegationState(PENDING); if(owner==null) setOwner(assignee); setAssignee(newUser);`；`resolve()` = `setDelegationState(RESOLVED); setAssignee(owner);`。`ResolveTaskCmd extends CompleteTaskCmd` 但覆写为 `task.resolve()`，记 `OPERATION_TYPE_RESOLVE` **不是 COMPLETE**。

**jBPM 的 `delegate` 是转手（不回来）**：WS-HT 语义 "making that user the actual owner ... transitions the task into the **Reserved** state"；`forward` 则是"replacing himself by those people in the list of potential owners"。

**Camunda 8 没有 delegation**（引擎侧无 `DELEGATED` 状态、无 owner/assignee 分离）；替代物是 assign/reassign/unassign + user task listener（`assigning` 事件可纠正 assignee，但**不能表达"临时借出再归还"**——没有 owner 概念）。

**"意见不占票"没有一等公民**（因为引擎都没有投票概念）。三条实践路径：意见与票分离建模（独立任务/子流程，不计数）；备注/附件承载（C7 `createComment`）；Zeebe ad-hoc + completionCondition（天然不参与票数）。

### 坑与边界

1. **C7 delegation 的坑**：`resolve` 不是完成；**`CompleteTaskCmd` 里没有 delegation 校验**——"only the owner can complete" 是文档契约而非硬门禁，宿主必须自己加门禁（否则被委托人可直接 complete 掉）。
2. **delegation 只能一次一层**——没有"咨询若干人并汇总"的能力（要 N 个意见就得派生 N 个任务）。
3. **最常见的语义事故**：用"并行意见任务 + 同一个 completionCondition"实现咨询，会让意见进入票数统计——把"咨询"错当"会签"。
4. 角色矩阵各家不一致（WS-HT 是 potential owner + actual owner + BA 都可 delegate；C7 是 owner 模型）——跨引擎迁移这块语义不兼容。

### 对 flowork 的结论（⚠ 修正形态）

**不要抄 C7 的 assignee 轮转形态**（同一个任务上 owner/assignee 换来换去 + 两态 `delegationState`），理由：无独立工作项、无独立时限、无独立意见记录面，等于把"咨询"塞进主任务的状态位；而且它的门禁还不是硬门禁（坑 1）。修正为：

1. **用派生 advisory 子实例 / 子任务表达咨询**，每个顾问一个独立工作项（独立记录、独立时限、独立查询、独立审计）——满足 flowork 的"只追加历史"。这比 C7 的轮转更现代（"独立工作项 > 同任务轮转"）。
2. **"意见不占票"显式建模**：节点/子任务上加 `advisory: true`（或 `vote_weight: 0`）标记，会签计数只统计被声明为 voting 的实例。主流引擎全靠人肉建模约束，flowork 可以在 DSL 层做成类型化约束（净增量）。
3. **"原办理人最后拍板"用显式两轮任务链**（原任务 → 咨询子任务集合 → 原任务的"拍板"轮次），而不是改 assignee。结合 A4 的增量：**"意见收齐才放行"用 `exit_reached` 出口公式表达**（抄 jBPM 的反向级联语义，但不用它的 Administrator 身份代办）。
4. **明确不做的**：C8 式 ad-hoc（"elements only activated on entry"否掉临时加人）；Temporal 的活动层无条件取消（咨询意见需留档）。

---

## 9. A9 · 多实例子流程

### 主流引擎怎么做

**BPMN 标准其实原生有输出聚合**：`tMultiInstanceLoopCharacteristics` 的 `loopDataOutputRef`（输出集合）+ `outputDataItem`（每次迭代的输出项）+ `complexBehaviorDefinition`（Complex 行为，实例级条件中止）。**但主流引擎基本都没实现后两者**——C7 的解析器里根本没有 `loopDataOutputRef`/`outputDataItem`/`complexBehaviorDefinition` 的分支。

**Camunda 7 没有输出聚合（关键缺口）**：`CallableElementActivityBehavior.passOutputVariables` 每次实例完成都把子实例输出 `setVariables` 到父作用域——**语义是"后写覆盖"**，同名 target 互相覆盖。官方论坛答复（forum 23959）：顺序 MI 可用 `getParentExecution().setVariable` 手写聚合；**并行 MI 直接写父作用域会撞 DB 锁**，需要 async-after + 监听器反查。

**Flowable 有 `variableAggregation`**（`<flowable:variableAggregation target="reviews" createOverviewVariable="true"><variable sourceExpression="…" target="userId"/></flowable:variableAggregation>`）——**插件式聚合器**（`implementationType`/`implementation` 可换 class 或 delegateExpression）、可建 overview 变量。**但开源手册 BPMN 章节零处提到 aggregation**（该能力只存在于源码与测试）。

**Camunda 8 / Zeebe 是业界最明确的 MI 语义**（官方文档逐字）：

- 输入：`inputCollection` 求值为数组；`inputElement` "stored as a **local variable of the instance**"；空集合 → "the multi-instance body is completed immediately and no instances are created. It behaves like the activity is skipped."
- 输出：`outputCollection` 是 MI body 的局部变量、body 完成时上抛；`outputElement` 定义每实例输出。**index 对齐（关键契约）**："the result is inserted into the `outputCollection` at the same index as the `inputElement` of the `inputCollection`. Therefore, the order of the `outputCollection` is determined and matches to the `inputCollection`, **even for parallel multi-instance activities**. If the `outputElement` variable is not updated, **`null` is inserted instead**."
- `completionCondition`：每次实例完成求值，true → "Any instances that are still active are **terminated**"。
- **partial 明确边界**："When an interrupting boundary event is triggered, the multi-instance body and all active instances are terminated. The `outputCollection` variable is **not propagated** to the parent scope (i.e. **no partial output**)."
- 限制：MI 内**支持 move 不支持 add**；terminate end event 只终止当前迭代。

**Conductor**：动态 fork + JOIN 聚合，按 **task 名**做 map 聚合（不是按 index 的数组）。**Temporal**：没有声明式 MI，扇出/聚合是代码（child workflow + future 收集 + Parent Close Policy）。

### 坑与边界

1. **C7 的"后写覆盖"是 MI 输出聚合最典型的坑**——**flowork 现存的 `for child in children` 逐子覆写父变量完全是同一个洞**（前一版 A9 已现场发现）。
2. Flowable 的 async MI call activity 会抢父实例排他锁（官方原文 `FlowableOptimisticLockingException`），必须开 async complete。
3. `completionCondition` 提前完成**不留"谁反对、为什么"**（C7 删除原因字符串固定）——"任一反对即失败"应该用 boundary error/message 或循环后聚合判定，不要塞进 completionCondition。
4. **局部变量陷阱**（Zeebe 明文警告）：`loopCounter`/输入元素变量不得写进 output mapping（"a local variable blocks later writes of the same name"）。
5. Flowable 有专门测试 `testDeleteProcessInstanceBeforeAggregationFinished`——"实例被删时聚合未完成"是真实边界情况。

### 对 flowork 的结论（✅ 确认 + 契约定稿）

按 **Zeebe 契约**设计（它最明确、最容易写测试）：

1. **`inputCollection` + `inputElement`（实例局部）+ `outputCollection` + `outputElement` + index 对齐（未写 → `null` 占位）+ 空集合立即完成（等价 skip）**。理由：C7 的"后写覆盖"是公开痛点、Flowable 靠插件聚合器、Conductor 是 task 名 map——**index 对齐数组是唯一能保证"第 k 个输入 ↔ 第 k 个输出"的形态，且对并行安全**。
2. **`completionCondition` 只用于"提前收工"，不作为失败机制**；中止/失败单列策略，且**中止时不聚合 partial**（照抄 Zeebe 的"no partial output"——这是可测试的明确契约），并把"被终止的实例与原因"落库（C7 只留一句固定删除原因，是可观测性缺陷）。
3. **每实例一个 token/子实例，key 用 `(miBodyNodeId, loopCounter)`**（排障与聚合的锚）。
4. **并发安全**：聚合写入必须串行化——让"MI body 完成"由**父实例持锁的单一 job** 执行（flowork 的 Job CAS 认领是现成原语），不要让每个子实例各自写父作用域。
5. **不做 MI 内动态加实例**（Zeebe 明确不支持 add；要"动态追加咨询人"用 A8 的 advisory 子任务形态）。
6. **FS 边界**：输入集合求值失败的时机——Zeebe 是部署期只校验语法，取值在运行期（空 → skip；非数组 → incident）。flowork 一致：语法进部署门禁，取值只在运行期，**空集合必须显式成功**（别做成错误）。

---

## 10. A10 · 模拟试跑

### 主流引擎怎么做

**先分清三个物种**（本问题最重要的结论）：

| 物种 | 副作用 | 代表实现 | 条件边怎么走 |
|---|---|---|---|
| **令牌模拟** | 无（根本不执行 handler） | bpmn-js-token-simulation、Camunda 8 Web Modeler token simulation | **不求值条件**，由人显式指定走哪条边 |
| **打桩测试** | 无（外部调用全部 mock） | C7 mockito、C8 CPT、Temporal mock activities | 真求值（用注入的变量） |
| **真环境测试** | **有** | Camunda 8 Modeler Test mode、Temporal `start_local` | 真求值 |

- **Camunda 7**：测试框架（`assertThat(instance).isWaitingAt(...)`、`@ExtendWith(ProcessEngineExtension.class)`）+ mock 层（`autoMock()`、`ProcessExpressions.registerCallActivityMock()` 可 mock 外部子流程并赋予行为）。没有"整图不执行副作用跑一遍"的模拟器。
- **Camunda 8** 三条并存：Web Modeler token simulation（pause/play/reset、simulation log）；CPT（`@CamundaProcessTest`，断言是阻塞式轮询、默认 10s 超时）；**JSON test cases**（`testCases[].instructions[]` 声明式资产，`TestCaseRunner` 跑成参数化 JUnit）；Modeler Test mode（**真跑**，官方明确警告 "Test mode executes real process logic against the selected environment ... If you target a production environment, this can affect live data"）。
- **jBPM 是唯一内置 BPSim 统计模拟的**：*"Designer includes a powerful simulation engine which is based on jBPM and Drools"*、*"based on the BPSim 1.0 specification"*、*"When selecting Process Paths, the simulation engine find all possible paths in the business model"*（全路径枚举）。**BPSim 是 WfMC 标准**，取向是容量/统计/成本分析（吞吐、等待时间、资源利用率），不是"带 stub 的工程试跑"。
- **bpmn-js-token-simulation**（社区最流行）：**条件边不参与求值**——排他网关 `"depends on UI to properly configure activeOutgoing"`，未配置则直接离开作用域；**包容网关未配置直接 `throw new Error('no outgoing configured')`**。用户任务/服务任务一视同仁为"等待+可触发"的令牌，通过 context pad 触发。**不要把它当验证器**（用模拟验证不了网关表达式）。
- **Temporal**：mock activities（按名字替换）+ **time skipping**（内存 server 快进 Timer，"except when Activities are running"）+ replay（确定性回归，只保证"老实例还能继续"，不发现业务错误）。**注意**：replay 不是模拟，是回归门禁。
- **"模拟实例"是不是一等公民？——不是。** 找不到任何引擎有模拟实例类型标记。C8 的做法是打环境（Testcontainers / dev / 警告 prod），并用 `coveredFlowNodes`/`coveredSequenceFlows` 记覆盖率。**"把模拟实例隔离开"是明确的市场空白。**

### 坑与边界

1. token simulation 的"合规"是**执行语义合规**，不是**表达式合规**——不要当验证器。
2. C7 mockito 已归档（C7 CE EOL），依赖它做外部子流程打桩有维护风险。
3. CPT 断言是"轮询+超时"：MI 数据量大时会翻页失败（官方 caveat："increase the query page limit"）。
4. Temporal 的 time skipping 是 `WorkflowEnvironment` 全局属性；且 Activities 运行时不跳时间。
5. Flowable 在 redeploy 时**默认关闭 XSD 与语义校验**（"we assume it is validated at the first deploy"）——把风险从部署期挪到运行期。

### 对 flowork 的结论（✅ 确认并重构）

1. **`dry_run` 拆成两层**：
   - **令牌模拟层（零副作用）**：不调用任何 handler、不写 outbox/事件；条件边**默认不求值**，用显式选择（等价 bpmn-js-token-simulation 的 `activeOutgoing`）；MI 按集合长度展开 N 个占位实例、按 index 落输出槽位。用途：连线路由审查 + **全路径枚举**（参考 jBPM 的 "find all possible paths" 与 SpiffWorkflow 的 PREDICTED/MAYBE 预测）。
   - **打桩真执行层**：真引擎跑，但 `service_task` 走注册的 stub handler、`call_activity` 指向 stub 定义、**定时器可跳过**（Temporal time skipping 是这里最值得抄的原语——`now` 由 Clock SPI 驱动，flowork 已有可注入时钟）。
2. **模拟实例做成一等公民**（行业空白，且 flowork 有落点）：instance 打 `simulation: true` 标记 + **查询隔离** + **硬性禁写**（不投 outbox、不推消息、不写真实分区的变量历史）+ 生产环境护栏（照抄 C8 Hub 的 prod 警告 + 直接拒绝）。C8 的 Test mode 会真跑真数据、官方只能警告；flowork 可以在引擎层根治。
3. **stub 与 handler 按同一标识注册**（Temporal "同名替换" + C7 `autoMock()` 的共同模式）：`service_task` 的 handler key 就是 stub 挂载点。
4. **加 JSON test case 资产格式**（C8 CPT 的 `$schema` + `instructions[]` + 覆盖率元数据）：可版本化、可被 AI 生成，能把 dry_run 从"手动单步"升级为"可回归的资产"。
5. **两条边界写进文档**：**模拟不验证表达式**（条件求值需要数据；要验证就走打桩层注入变量——与 C8 的"部署期只校验 FEEL 语法、缺变量是运行期 incident"同构）；**报告诚实列出 stub 节点清单**（覆盖度可见）。

---

## 11. A11 · 引用完整性校验

### 主流引擎怎么做

**核心事实：组织实体引用在部署期一律不校验。** 四个引擎全推给运行期：

| 检查项 | C7 | C8/Zeebe | Flowable | jBPM |
|---|---|---|---|---|
| XSD/结构 | 部署失败 | 部署失败 | 可开关（redeploy 默认关） | 部署校验 |
| 表达式**语法** | 解析期创建表达式（异常收成部署错误） | **部署期编译 FEEL 并拒绝** | 解析期创建表达式 | 部署期编译 MVEL |
| 表达式**变量存在** | 否（求值期 `Unknown property used in expression`） | 否（缺变量 → **incident**） | 否 | 否 |
| 同单元内被调资源存在 | 否（call activity 运行期解析） | **是**（`bindingType=deployment`；`latest` 则否） | 否 | 否 |
| **组织实体（人/组/角色）存在** | **否**（运行期 IdentityService） | **否**（运行期解析 candidate group；引擎明文不校验授权） | **否**（`UserTaskValidator` 只检查监听器/脚本字段，无 assignee 存在性） | **否**（运行期 `UserGroupCallback`） |

**Camunda 8 的官方口径最清楚**（job priority 文档）：*"Invalid FEEL expressions are handled differently depending on when they fail: **If the expression is invalid at deployment time, BPMN deployment fails and the process is not deployed.** If the expression fails at runtime, for example because a referenced variable is missing, Camunda raises an **incident** and the job is not created."*

**部署期校验的扩展点**（按开放度）：jBPM（`ProcessValidatorRegistry.registerAdditonalValidator`，可加 validator）> Flowable（`setProcessValidator` 替换整个校验器 + `ValidationError` 带 `xmlLineNumber`/`activityId`/`isWarning` 分级）> C7（`BpmnParseListener`，40+ 方法，成本高）> Zeebe（内置 validator，宿主扩展面最窄）。

**仍不校验的示例**（C8 自己的 open issue）：task header 里的 FEEL（"The engine treats task headers as opaque strings and never checks their syntax"）、表单里的 FEEL（"Deployment completes without error, even if FEEL expressions are invalid in the form"）。

### 坑与边界

1. **"部署通过 = 引用可用"是错的**：C7/Flowable 的 call activity 运行期解析；C8 默认 `latest` 绑定不校验。
2. **校验可以关**：Flowable redeploy 默认跳过两级校验——把风险挪到运行期。
3. **对表达式的"校验"要区分三档**：语法（可行，多家做）、静态类型/函数注册（可行，要自定义）、变量存在性（**业界一致放弃**）。
4. **警告 vs 错误的粒度**：Flowable 的 `ValidationError.isWarning` 值得抄——同一次校验能同时输出"拦发布"与"提醒"。

### 对 flowork 的结论（⚠ 修正强度）

前一版建议：加 `subject_exists` 校验钩子，返回 False 时 ERROR 拦发布。行业对标后**修正强度与分层**：

1. **分四层，各给明确严重级别**：
   - **L1 结构/schema → ERROR**（部署即拒）。flowork 现有 validator 已覆盖大半。
   - **L2 表达式语法 → ERROR**（硬门禁，对齐 Zeebe 的 `hasValidExpression(mandatory)`）。flowork 的 CEL 静态检查（`iter_expression_positions` 覆盖连线位/节点位）**比 Flowable 更强**，保持。
   - **L3 定义内引用完整性 → ERROR（新增层）**：`call_activity` 的目标定义是否存在于**同一发布单元**（抄 Zeebe `ZeebeCalledElementDeploymentBindingValidator` 的"Expected to find process with id '%s' in current deployment, but not found"）；`service_task` 的 handler key 是否已注册；建议引入 `bindingType` 语义（`deployment` 绑定=强校验，`latest` 绑定=只警告）——这正是 flowork 已有的"定义可独立部署"能力需要的配套。
   - **L4 外部实体引用（组织/角色/用户/Agent）→ 默认 WARNING + strict 可选**。**修正点**：业界一致放弃的原因是"组织模型独立于流程定义演进，部署期成立不代表明天成立"；但 flowork 自己就是目录权威（M-A），**有能力做别人做不了的事**——只是**别默认拦发布**（部署期有效会随时间失效）。所以：`subject_exists` 钩子返回 False 时给 **WARNING**；提供一个可配置的 strict 模式（或 CLI 开关）把它升为 ERROR。
2. **把 L4 做成"部署期扩展点"，不写进内核**：照 jBPM `ProcessValidatorRegistry` / Flowable `setProcessValidator` 的形态，在公开面暴露 validator 端口（输入：定义 + 目录读取接口；输出：`findings[] {severity, code, position, message, suggestion}`），默认注册"内建目录校验器"。满足"内核不得 import contrib"的双向铁律，宿主可注入自己的校验。
3. **不要校验"变量是否会在运行期存在"**（业界一致不做；flowork 现状 FormulaError → 挂起+故障单与 C8 的 incident 语义一致，保持，并写成 spec 对照说明）。
4. **抄 Flowable 的 `ValidationError` 形态**：分级（isWarning）+ 精确定位。JSON DSL 没有 XML 行号，用**节点 id / 连线 id / 表达式位点路径**定位（`iter_expression_positions` 已有位点信息）；warning 不拦发布（与现有 `validator_graph` 全 WARNING 策略一致）。
5. **发布语义显式化**：`deploy`（含 VersionVerdict 报告）**始终跑 L1–L3**，把校验报告作为发布产物（`flowork deploy` 命令 + designer 校验闭环已有位置）——"跳过校验"永远不会成为隐式默认。

---

## 12. 对两篇 delta 文档的修正清单

| 项 | 原建议 | 修正 | 已回写 |
|---|---|---|---|
| A1 | 节点级 `permitted_actions` 配置 | → (state × task_type) 声明表 + `allowed_actions` API 响应 | detail 篇 |
| A3 | 复活"最近一轮" | 保持；补充"flowork 有轮次数据（token_no）、主流没有"与"复活原办理人 vs 重入重新解析"的差异声明 | detail 篇 |
| A4 | 协办清理 + 修链 | 保持；新增 jBPM 反向级联可用 `exit_reached` 表达 | detail 篇 |
| A5 | 双日历口径（发起人/办理人各一份） | **推翻**：历史一律墙钟；端口只接 deadline 投影；不做按人各算 | detail 篇 |
| A6 | 参与者上限（报错） | 保持；主机制修正为异步分批物化；关键约束是 `item_count` 语义 | detail 篇 |
| A7 | `auto_pass_later` 实现 | **推翻**：不做取人决策，只做 anticipate 只读提示 | detail 篇 |
| A8 | consult 动作 | 保持；形态修正为 advisory 子任务（非 assignee 轮转）+ 显式不占票标记 | detail 篇 |
| A9 | 多实例子流程 | 保持；契约按 Zeebe 定稿（index 对齐/null 占位/空集 skip/no partial） | detail 篇 |
| A10 | 模拟试跑 | 保持；拆两层 + 模拟实例一等公民 | detail 篇 |
| A11 | `subject_exists` ERROR | **修正强度**：默认 WARNING + strict 可选；补 L3 层（同单元引用 + handler 注册） | detail 篇 |

---

## 附：引用来源（按引擎）

**Camunda 7**：`camunda-engine-7.23.0-sources.jar`（BusinessCalendar 家族 / TaskDecorator / MultiInstanceActivityBehavior / DefaultHistoryEventProducer / ProcessEngineConfigurationImpl）、[Permissions.java](https://github.com/camunda/camunda-bpm-platform/blob/master/engine/src/main/java/org/camunda/bpm/engine/authorization/Permissions.java)、[DelegationState.java](https://github.com/camunda/camunda-bpm-platform/blob/master/engine/src/main/java/org/camunda/bpm/engine/task/DelegationState.java)、[TaskService.java](https://github.com/camunda/camunda-bpm-platform/blob/master/engine/src/main/java/org/camunda/bpm/engine/TaskService.java)、[ProcessInstanceModificationBuilder.java](https://github.com/camunda/camunda-bpm-platform/blob/master/engine/src/main/java/org/camunda/bpm/engine/runtime/ProcessInstanceModificationBuilder.java)、[BpmnParse.java](https://github.com/camunda/camunda-bpm-platform/blob/master/engine/src/main/java/org/camunda/bpm/engine/impl/bpmn/parser/BpmnParse.java)、[流程实例修改文档](https://docs.camunda.org/manual/7.21/user-guide/process-engine/process-instance-modification/)、[forum 44263 并行 MI 性能实测](https://forum.camunda.io/t/parallel-multi-instance-slow-and-resource-intensive/44263)、[forum 23959 MI 输出聚合](https://forum.camunda.io/t/aggregating-back-data-from-multi-instance-call-activity/23959)、[forum 38148 重复审批人](https://forum.camunda.io/t/camunda-8-block-user-from-accessing-claiming-a-human-task-although-being-part-of-candidate-group/38148)、[four-eyes-advanced 示例](https://github.com/camunda-consulting/camunda-7-code-examples/tree/main/snippets/four-eyes-advanced)

**Camunda 8 / Zeebe**：[multi-instance](https://docs.camunda.io/docs/components/modeler/bpmn/multi-instance/)、[user-tasks](https://docs.camunda.io/docs/components/modeler/bpmn/user-tasks/)、[ad-hoc subprocess](https://docs.camunda.io/docs/components/modeler/bpmn/ad-hoc-subprocesses/)、[process instance modification](https://docs.camunda.io/docs/components/concepts/process-instance-modification/)、[authorizations](https://docs.camunda.io/docs/components/concepts/access-control/authorizations/)、[incidents](https://docs.camunda.io/docs/components/concepts/incidents/)、[user task listeners](https://docs.camunda.io/docs/components/concepts/user-task-listeners/)、[变量传播](https://docs.camunda.io/docs/components/concepts/variables/)、[CPT](https://docs.camunda.io/docs/apis-tools/testing/getting-started/)、[JSON test cases](https://docs.camunda.io/docs/apis-tools/testing/json-test-cases/)、[Modeler Test mode](https://docs.camunda.io/docs/components/hub/workspace/modeler/validation/test-your-process/)、[token simulation](https://docs.camunda.io/docs/components/hub/workspace/modeler/validation/token-simulation/)、`user-tasks.yaml`（`x-required-permissions`）、`process-instances.yaml`、`ZeebeRuntimeValidators`/`ZeebeCalledElementDeploymentBindingValidator`、[zeebe-chaos 大 MI 实测](https://github.com/camunda/zeebe-chaos)、[forum 45961 无工作日历替代](https://forum.camunda.io/t/replacement-for-business-calendars-in-c8/45961)、issues [#32953](https://github.com/camunda/camunda/issues/32953)/[#18376](https://github.com/camunda/camunda/issues/18376)/[#51814](https://github.com/camunda/camunda/issues/51814)/[#59554](https://github.com/camunda/camunda/issues/59554)/[#48075](https://github.com/camunda/camunda/issues/48075)

**Flowable 7**：[BPMN 构造（MI / business calendar / call activity）](https://www.flowable.com/open-source/docs/bpmn/ch07b-BPMN-Constructs)、[RuntimeService.addMultiInstanceExecution](https://github.com/flowable/flowable-engine/blob/main/modules/flowable-engine/src/main/java/org/flowable/engine/RuntimeService.java)、[AddMultiInstanceExecutionCmd](https://github.com/flowable/flowable-engine/blob/main/modules/flowable-engine/src/main/java/org/flowable/engine/impl/cmd/AddMultiInstanceExecutionCmd.java)、[TaskHelper（子任务级联删除）](https://github.com/flowable/flowable-engine/blob/main/modules/flowable-engine/src/main/java/org/flowable/engine/impl/util/TaskHelper.java)、[ChangeActivityStateBuilder](https://github.com/flowable/flowable-engine/blob/main/modules/flowable-engine/src/main/java/org/flowable/engine/runtime/ChangeActivityStateBuilder.java)、[AbstractDynamicStateManager（守卫）](https://github.com/flowable/flowable-engine/blob/main/modules/flowable-engine/src/main/java/org/flowable/engine/impl/dynamic/AbstractDynamicStateManager.java)、[UserTaskValidator](https://github.com/flowable/flowable-engine)、[ValidationError](https://github.com/flowable/flowable-engine/tree/main/modules/flowable-process-validation)、`VariableAggregationDefinition.java` + `MultiInstanceVariableAggregationTest`、[issue #787 动态加实例](https://github.com/flowable/flowable-engine/issues/787)

**jBPM 7.74**：[文档（business calendar / MI Designer 属性 / assignment strategy / excludedOwners）](https://docs.jboss.org/jbpm/release/7.74.1.Final/jbpm-docs/html_single/)、`operations-dsl.mvel`、`MVELLifeCycleManager.java`、`Allowed.java`、[SubTasksStrategy.java](https://github.com/kiegroup/droolsjbpm-knowledge/blob/main/kie-internal/src/main/java/org/kie/internal/task/api/model/SubTasksStrategy.java)、[ProcessSubTaskCommand.java](https://github.com/kiegroup/jbpm/blob/main/jbpm-human-task/jbpm-human-task-core/src/main/java/org/jbpm/services/task/commands/ProcessSubTaskCommand.java)、`BusinessCalendarImpl.java`、`ProcessValidatorRegistry.java`、`RuleFlowProcessValidator.java`

**Temporal**：[Cancellation and Termination](https://docs.temporal.io/encyclopedia/workflow/cancellation-and-termination)、[Parent Close Policy](https://docs.temporal.io/parent-close-policy)、[Workflow Execution Limits](https://docs.temporal.io/workflow-execution/limits)、[测试套件（time skipping / mock / replay）](https://docs.temporal.io/develop/python/best-practices/testing-suite)

**SpiffWorkflow / Conductor / 标准**：[SpiffWorkflow task.py（FINISHED_MASK 守卫）](https://github.com/sartography/SpiffWorkflow/blob/main/SpiffWorkflow/task.py)、[WorkflowSpec.validate](https://github.com/sartography/SpiffWorkflow/blob/main/SpiffWorkflow/specs/WorkflowSpec.py)、[Conductor retry](https://github.com/orkes-io/docs/blob/main/mkdocs_content/reference-docs/api/workflow/retry-failed-workflow.md)、[Conductor ROADMAP（rewind=debugger）](https://github.com/conductor-oss/conductor/blob/main/ROADMAP.md)、[BPMN 2.0 XSD](https://www.omg.org/spec/BPMN/20100501/Semantic.xsd)、[WS-HumanTask 1.1](https://docs.oasis-open.org/bpel4people/ws-humantask-1.1-spec-cs-01.html)、[bpmn-js-token-simulation](https://github.com/bpmn-io/bpmn-js-token-simulation)、[BPSim](https://www.bpsim.org/)
