# 表达式数据面设计（节点事实与注入清单）

| | |
|---|---|
| 对应 spec | [spec.md](./spec.md) §4.2（位点清单）、§4.3（求值语义）、C.4（task.* 快照）、§12/§13（投影登记） |
| 关联设计 | [variable-io-api.md](./variable-io-api.md)（读写面，2026-10-08 已批六批）——本设计只管**判断面**，互补不重叠 |
| 日期 | 2026-10-08 |
| 状态 | **已批准**（2026-10-08）；**F0+F1+F2 已落地**（§8 前三批；F3–F5 按批排期），范围变更已记《本期建设边界》backlog |
| 决策 | 判断面 = 精选事实目录（现查派生、位点分家、版本化）；`now` 由引擎经 Clock 注入；路由决策审计同批补齐 |

## 0. 一句话与五词口诀

命题（2026-10-08 讨论 定稿）：**流程状态（流程定义 / 实例数据 / 节点数据 / 节点事件）应当可查、可算**。
行业共识的劈法：公式可见的是"精选事实目录"（小而稳），接口可查的才是"全量历史"（大而全）。
落地成五个词：**变量看表单，`initiator`/`now`/`instance` 看全局，`task` 看当前，`prev_task` 看上一站，`node()` 点名查历史**。设计器变量树照这五组摆。

## 1. 调研结论（决策依据）

**四个样本**（2026-10-08 调研）：

- **对标产品（行为基准语义参考）**：判断面极瘦——条件环境仅"发起人三件套 + 表单数据"，审批意见/耗时/票数/动作记录均不进公式，历史进入公式只有取人位的 `基准取人函数("节点")` 窄缝；查询面极肥（已办/聚合多维查询 Facade）。会签出口只能 "N / N%" 单值，不能与业务字段复合。
- **Camunda 8 / Zeebe**：表达式只看变量 + 内置函数，连实例 key 都不暴露（官方 open issue）；多实例计数（nrOfCompletedInstances 等）只在会签进行中的完成条件可见，**会签一结束下游拿不到**。
- **Temporal**：Query（只读）/ Update（带校验的写、同步返回）/ Signal 三件套——对应本仓读写面设计（variable-io-api）。
- **公式能力实测**（2026-10-08 探针，经 flowork 求值入口真跑）：timestamp 解析/比较/相减得时长、duration 互比与 getHours、变量携带 datetime 直接比、contains/startsWith/endsWith/matches/拼接/size（中文 OK）、四则/取模/in/exists 宏、宿主函数注册——全部通过；`now()`/split/substring/upper/abs/max **没有**（CEL 标准刻意极简，now 缺失靠引擎注入解决）。

**四条行业共识**：①判断面精选、查询面全量，两分；②事实目录是一次对外承诺，必须版本化；③同行几乎都选"结果回写变量"，我们因已办记录同库且只追加，可选**现查派生**（口径唯一、免配置）；④复杂多步逻辑归流程图（分支+节点）或决策表，不归公式。

**差异化机会**：会签票数、审批意见、时效的**下游节点引用**是 Camunda 与对标产品的共同空白，而 flowork 的 token 三计数 + 只追加已办记录数据已齐——按本设计落地即为卖点（招标流程：投票环节票数驱动后续分支与审批人）。

## 2. 注入清单（判断面词汇，全量）

### 2.1 全局件（所有位点）

| 标识符 | 类型 | 语义 |
|---|---|---|
| `initiator` | subject | 发起人（已有，不变） |
| `now` | timestamp | 求值时刻，经 Clock 端口取；**一次路由判断钉一个时刻**（select_outgoing 循环外取一次），测试可冻结 |
| `instance.id` | string | 实例编号（子流程映射携带父单号等集成场景） |
| `instance.started_at` | timestamp | 发起时间（"单据总时长"判断） |

### 2.2 上一站 `prev_task.*`（位点：连线条件 + 取人/传阅）

字段：`assignee`（谁办的）、`outcome`（结果）、`comment`（意见，可空）、`finished_at`（办完时间）、`used_time`（耗时，duration 类型，直接与 `duration('24h')` 比较）。

语义钉死：**"导致本次流转的那个已完成任务"**——实现口径＝**实例内最近一条已完成任务**
（F2 落地）：串行、会签最后一票、加签/改派后的实际办理人、并行汇聚最后到达、
驳回复活后的驳回票，全部场景下与因果链取法等价。查无＝不注入，条件位经
静态预检按不满足跳过、取人位贡献空（温和，不出故障单）。

### 2.3 点名查任意站 `node("节点编码").*`（位点：连线条件 + 取人/传阅）

| 字段 | 类型 | 语义 |
|---|---|---|
| `runs` | int | 办过几轮（驳回重走计多轮） |
| `skipped` | bool | 是否被跳过 |
| `assignee` / `outcome` / `comment` / `finished_at` / `used_time` | 同 prev_task | **最近一轮**的办理事实 |
| `approve_count` / `disapprove_count` / `item_count` | int | **最近一轮**会签票数（token 三计数派生；非会签节点为 0） |

查无＝**固定形状**（人物字段 null、计数 0、runs 0）：`==` 比较自动不满足、取人位
自动贡献空、`node("x").assignee` 求值为 null；对 null 字段做排序/算术比较
（如 `used_time > duration('24h')`）需判空守卫——CEL 严格类型语义，缺键仍报错
（可抓字段名笔误）。返回值经 json_to_cel 包装（MapType 字段选择）。

### 2.4 既有窄位点快照 `task.*`（方言维持，各补实用字段）

- 改派位 reassign_to：`{id, assignee, outcome, created_at, deadline_at}`（F2 已补后两个——"已超时多久决定改给谁"三元表达）。
- 会签出口 approve_exit / disapprove_exit：`{item_count, approve_count, disapprove_count}` 维持；`assignees`（本轮流派人名单）→ **开放问题未实现**（§10）。

### 2.5 位点矩阵

| 词汇 | 连线条件 | 取人/传阅 | 改派 | 会签出口 | 消息关联 | 子流程映射 |
|---|---|---|---|---|---|---|
| 流程变量（表单数据） | ✓ | ✓ | ✓ | ✓ | ✓ | ✓（各侧上下文） |
| `initiator` / `instance.*` | ✓ | ✓ | ✓ | ✓ | initiator | ✓ |
| `now` | ✓ | ✓ | ✓ | ✓ | 不建议（关联键要求稳定） | ✓ |
| `prev_task.*` | ✓ | ✓ | — | — | — | — |
| `node("…")` | ✓ | ✓ | — | — | — | —（v1.1 候选） |
| `task.*` | — | — | ✓（改派方言） | ✓（出口方言） | — | — |

`task.*` 同名不同方言的别扭就此收敛：连线位以后用 `prev_task`，`task` 专职"当前任务"。

### 2.6 明确不注入（"不"与清单同等重要）

- **`me` / 当前用户**：公式在引擎时刻求值，不存在"当前人"（对标产品 SELF 是其条件值位特例，本仓无此位）。
- **组织/人员数据标识符**：走取人函数词汇（查无传播语义长在函数侧，§5.5），不做变量。
- **原始事件流水**：只给"最近一次 + 汇总"，明细考古去查询面（REST/Console）。
- **定义配置当变量**（读流程图参数拼配置）：不开——流程引擎变模板引擎的滑坡起点。

## 3. 节点事实的实现语义

- **现查派生**：`prev_task` / `node()` 求值时从 `wf_task_history`（+ token 记录）按 `(instance_id, activity_code)` 聚合，不物化进实例变量。与全行业"回写变量"路线的差异是数据底子决定的：同库、只追加、因果链完整 → 口径唯一、免回写配置。
- **查无传播（判断面版）**：节点未到达 / 被跳过 / 查无记录 → 条件位按**不满足**（false）温和处理，取人位按空贡献空（对齐 §5.5）；不抛错、不卡流程。仅"查库失败"类技术故障走 EngineExecutionError（挂起 + 故障单），与求值错语义统一。
- **轮次口径**：`node()` 全部字段默认**最近一轮**；跨轮累计用 `runs` 表达；票数即最近一轮票数（驳回重投 = 新一轮）。
- **used_time 口径**：v1 = 任务 created_at → completed_at 自然时长；流程暂停是否剔除 = 开放问题（§9）。
- **性能**：事实查询走既有 `(instance_id, …)` 索引；单条公式引用位数有限（2KB 限长间接约束），不做预聚合物化。

## 4. 公式语言配套

- **`now` 注入**：`expression_context` 增加 now 入参（Clock 端口供数）；routing / resolver / cc 等装配点透传。保留标识符清单由 `{"initiator", "task"}` 扩为 `{"initiator", "task", "now", "instance", "prev_task"}`。
- **`node()` 是引擎注入函数**（读引擎内部数据，宿主词汇不可达），与宿主 ExpressionFunctions 注册面分开；静态检查免检名单相应登记。
- **写法规范**（进文档与设计器提示）：时效一律 `… > duration('24h')` 字面量；日期 RFC3339 带时区（引擎时钟 UTC）；意见匹配用 `comment.contains('加急')` / `matches(…)`。
- **扩展函数**（split/substring/abs/max/round…）：不进内核。按需经 ExpressionFunctions 注册（宿主或 contrib 扩展词汇包），注册即被设计时校验认识（既有注册表 ERROR/WARNING 语义）。
- 中文变量名维持不可引用（CEL ASCII 标识符），目录 label 用中文。

## 5. 静态目录对象（一份目录，三消费方）

结构：`entries: [{name, kind(identifier/function), type, label, positions, sample}]`；函数条目复用 FunctionSignature，**补 label/description 字段**（顺带解决 backlog P1"函数目录树下发缺 label"）。

| 消费方 | 用法 |
|---|---|
| validator | RESERVED_IDENTIFIERS 与位点白名单由目录生成（现为硬编码 frozenset，改表驱动） |
| 设计器 | 变量树五组分组下拉（全局/当前/上一站/点名查/流程变量），随目录更新 |
| 下发端点 | 内核 Python API 先行；REST `/v2` 批登记端点；MCP 随 M7 |

版本化：目录自带 version；**加字段 = 小版本；改语义 / 删条目 = 破坏性变更（R2 级，major+1）**。

## 6. 路由决策审计（配套，合并既有 P2 遗留）

判断面开放后，"当时为什么走这条边"必须可回答。每次路由决策记录：**公式原文、求值结果、命中连线、关键取值快照**（now、引用的节点事实值/摘要）。落点：wf_token 扩列或独立路由日志表（实现批定）；查询随 /v2 与 Console。

## 7. 与既有工作的关系

- **variable-io-api（已批六批）**：读写面、variables_updated 事件、wf_variable_history、死端口（VariableStore）清理均在彼批——本设计不重复；`instance.started_at` 等读取复用其读面基建。
- **三套契约收敛**（`variables[]` 设计器元数据 / `input` JSON Schema 运行时契约 / engine-v1 business_object）：开放问题，另行决策记录；本设计不改变 input 三边界。
- **kernel-v2-plan I3 作用域树**：维持不采纳（单 JSON 列 + 1MB 上限不变）。

## 8. 实施计划（与 variable-io 六批错峰）

| 批 | 内容 | 验收 | 规模 |
|---|---|---|---|
| **F0 目录地基** | 目录对象 + FunctionSignature 补 label + validator 表驱动化 | validator 规则由目录生成；既有测试全绿 | 小 |
| **F1 now 注入** | expression_context + Clock 接线 + 保留词 + 装配点透传（可与 F0 同刀） | §9 案例集 A2/A4/E5 全过（Clock 冻结断言） | 小 |
| **F2 节点事实（核心）** | 事实查询模块（现查聚合）+ prev_task 因果链 + node() + 位点矩阵 + 查无语义 + 改派/出口快照补字段 | **已落地（2026-10-08）**：§9 A1/B1/B2/B3/C1–C3/D1/D5 共 10 例过（tests/semantics/test_node_facts.py）；C5→开放问题、D2/D3 由"最近完成"语义经 B1/J1 覆盖（专用加签/改派流程用例随 suite 补） | 中 |
| **F3 路由审计** | 决策记录（公式/结果/取值快照）+ 落库 + 查询 | §9 案例集 F 组全过；与 wf_variable_history 同事务语义对齐 | 中 |
| **F4 设计器与下发** | 目录下发端点 + 变量树五分组 + 公式编辑器提示（时效用 duration 字面量等） | 设计器无后端硬编码词汇；下拉与 validator 同源 | 小 |
| **F5 文档收口** | spec §4.2/§4.3/C.4 修订 + 边界 backlog 记账 + API.md | 五道门全绿；spec 与实现一致 | 小 |

排程建议：F0+F1 一刀先落；F2 独立成批（核心价值与风险集中处，测试先行——会签/驳回类难块惯例）；F3/F4 随 /v2 与 M7 节奏；F5 收尾。与 variable-io 六批共享 expr/context 与 ops 挂点的改动错峰合入。

## 9. 验收案例集（F1/F2/F3 的验收面）

编写原则：**每个词汇 ≥2 例；每条语义规则 1 正 1 反；结构性难角（驳回/并行/加签/改派/跳过/多轮）逐角单设用例**。
层标记：`黑盒`=tests/suite 端到端；`单测`=tests/semantics 逐项。编号即用例命名建议。主线场景=招标流程（投票环节贯穿 C 组）。

### A 组：时效类（`now` / `prev_task.used_time` / `instance.started_at`，F1+F2）

| # | 业务场景 | 示例公式（可复制） | 断言要点 | 层 |
|---|---|---|---|---|
| A1 | 审批加急升级：上一站耗时超 24h，下一连线走总监加审分支 | `prev_task.used_time > duration('24h')` | 两条路径各触发一次；Clock 冻结控制耗时取值 | 黑盒 |
| A2 | 单据总时长兜底：挂起超 72h 后续走简化通道 | `now - instance.started_at > duration('72h')` | 边界值 71h59m/72h01m 两侧行为分叉 | 黑盒 |
| A3 | 超时改派分级：超时越久改给越高层级 | `now - task.deadline_at > duration('48h') ? 'u_director' : 'u_peer'` | 改派位 now 与 task.deadline_at 同时可用；三元取值两侧各验 | 黑盒 |
| A4 | 时长写法规范回归：字面量比较 / 时刻相减 / 取整 | `(now - timestamp(started_at)) > duration('1h')`、`duration('1h30m').getHours()` | 探针结论固化为回归（RFC3339 带时区、duration 字面量唯一写法） | 单测 |

### B 组：上一站结果类（`prev_task.assignee/outcome/comment`，F2）

| # | 业务场景 | 示例公式 | 断言要点 | 层 |
|---|---|---|---|---|
| B1 | 上一站审批人的领导来审（取人位） | `assignee: manager_of(prev_task.assignee)` | 取人位 prev_task 可用；宿主函数收到正确 subject | 黑盒 |
| B2 | 驳回重报走复核分支 | `node("manager").outcome == 'reject'`（经理最近一轮为驳回） | **口径修正（F2 落地时）**：重报后出边的 prev_task＝重报任务自身，"上站驳回过"须点名查 `node()`；prev_task 因果链语义由 D1/D2/D3 承担 | 黑盒 |
| B3 | 意见路由：意见含"加急"走加急通道 | `prev_task.comment.contains('加急')` | 空意见/无意见 → 条件不满足不报错；中文 contains | 黑盒 |
| B4 | 指定节点结果 × 业务字段复合 | `node("legal_review").outcome == 'approve' && amount > 50000` | 节点事实与表单字段可自由复合（对标产品出口不可复合的对照） | 黑盒 |

### C 组：会签票数类（`node()` 三计数 + 出口方言，F2 主线）

| # | 业务场景 | 示例公式 | 断言要点 | 层 |
|---|---|---|---|---|
| C1 | **招标主线**：投票环节票数驱动分支 | `node("voting").approve_count * 100 >= node("voting").item_count * 60` | ≥60% 走委员会评审、否则重新询价；票数=**最近一轮** | 黑盒 |
| C2 | 票数段位驱动审批人（取人位） | `assignee: node("voting").approve_count >= 8 ? 'u_director' : 'u_manager'` | 值位表达式引用节点事实返回字符串 subject | 黑盒 |
| C3 | 驳回重投轮次隔离 | `node("voting").runs == 2`、重投后再判票数 | 上一轮票数不残留；runs 计数正确 | 黑盒 |
| C4 | 会签进行中出口回归（既有方言不受影响） | `task.approve_count * 100 >= task.item_count * 50` | 出口位 task.* 三计数行为与改造前逐位一致 | 黑盒 |
| C5 | 发起人在会签名单即通过（出口位候选字段） | `task.assignees.exists(a, a == initiator)` | 若采纳 assignees 补字段则验；否则记开放问题 | 黑盒 |

### D 组：因果链与结构难角（prev_task 确定性，F2）

| # | 业务场景 | 断言要点 | 层 |
|---|---|---|---|
| D1 并行汇聚 | 两并行分支固定先后完成，join 后连线的 prev_task=**最后到达**那条路的完成任务，结果确定非随机 | 黑盒 |
| D2 加签之后 | 加签人实际办理完成 → prev_task.assignee=加签人（非原候选人）；runs 相应 +1 | 黑盒 |
| D3 改派之后 | 改派后办理 → prev_task.assignee=改派后办理人 | 黑盒 |
| D4 被跳过节点 | `node("optional_review").skipped == true` 可作条件；引用其事实字段（assignee 等）= 查无 → 不满足 | 黑盒 |
| D5 未到达节点 | 引用尚未执行的节点 → 条件不满足、流程继续；仅查库异常走故障单（技术故障注入用例） | 黑盒 |

### E 组：负向与边界（F0+F2）

| # | 场景 | 断言要点 | 层 |
|---|---|---|---|
| E1 子流程内部节点不可查 | 父流程 `node("子流程内节点")` = 查无不满足；正路=输出映射回传变量（钉死边界） | 黑盒 |
| E2 位点白名单 | 连线条件写 `task.*` → 发布期 ERROR（task_unavailable 回归）；目录驱动后规则同源 | 单测 |
| E3 保留词不可声明 | 把 `now`/`prev_task`/`instance` 声明为流程变量 → 发布期 ERROR | 单测 |
| E4 中文变量名回归 | 非 ASCII 标识符不毒化求值、不可引用（既有语义不因新词汇改变） | 单测 |
| E5 now 同刻钉死 | 一次路由多个条件均引用 now → 同一时刻（Clock 冻结后断言相等） | 单测 |

### F 组：路由审计（F3）

| # | 场景 | 断言要点 | 层 |
|---|---|---|---|
| F1 重放解释 | 任一历史路由决策可查：公式原文 + 求值结果 + 命中连线 + 取值快照（now/prev_task/node() 引用值） | 黑盒 |
| F2 快照与事实一致 | 审计记录中的 used_time/票数与 wf_task_history 现场重算一致（口径唯一性） | 黑盒 |

## 10. 风险与开放问题（不阻塞 F0/F1）

1. `comment`（审批意见）进公式与审计日志的敏感信息：v1 原样透出，字段级脱敏/权限按 02-权限模型既定推迟 v2。
2. `used_time` 暂停剔除口径：v1 自然时长，剔除规则待业务样本再定。
3. 多轮历史实例的聚合成本：`runs` 大时 `node()` 查询走索引仍 O(轮次)，先观察，必要时加"最近一轮"短路。
4. 扩展词汇需求收集：宿主侧按需注册，先不预置清单（避免词典膨胀）。
5. 会签出口位 `task.assignees`（C5）未实现：加签名单注入需 behaviors/anticipate/reject_flow 三点接线，复杂度与收益暂不匹配——待"发起人在会签名单"场景成真再启。
