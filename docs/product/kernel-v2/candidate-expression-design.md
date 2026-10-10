# 候选槽表达式对齐设计稿（B 批，spec §5.5 零兼容）

| | |
|---|---|
| 状态 | **已实施（2026-10-10，e586fb2 合提交 + 后续收口）**——运行时求值/位点收录/returns 白名单/L4 组跳过/测试迁移全落地；实施与本文三处偏差见文末「实施修订」 |
| 依据 | [spec.md §2.2/§5.4/§5.5/§16](./spec.md)（三处相互印证）+ [deploy-validation-design.md](./deploy-validation-design.md)（L4 取人位清单已含 candidate_users，先行按表达式对待） |
| 排期 | 依赖时钟统一批落地（占用 `participants/resolver.py` 与 `tasks/behaviors.py`） |
| 前置 | **A 批（候选制三缺口）已由并行会话落地**：`tasks/candidates.py` 新模块（`person_key` 前缀互认 / `candidate_hit` 两式判定 / `candidate_task_ids` 查询维度）、`resolve_participants` 三段判定、`_claim` 接 `require_candidate`、`list_tasks(candidate=…)`。本批在其上把「候选关联的内容」从裸字符串换成**求值结果** |

## 命题：五个层对同一字段的理解不一致

规格说 `candidate_users` / `candidate_groups` 是公式；运行时从不求值；设计时校验不收录；
部署校验半收录；设计器按"填名字"渲染。本文档把五层统一到规格。

| 层 | 现状（实测） |
|---|---|
| 定义模型 | 普通 `list[str]`，无"此处是公式"的任何标记 |
| 设计时校验 | `iter_expression_positions` **不产出候选位**——不查变量声明、不查函数注册、不查语法 |
| 部署期校验 | `_check_subject_literals` 收录 `candidate_users`（按表达式提取字面量）但**漏 `candidate_groups`**；且内置目录未实现 `subject_exists`，该检查在内置目录上全程空转 |
| 运行时 | `_create_claimable_task` 经 `candidate_subjects`（A 批）把两个列表**并集去重后逐项原样写** `IdentityLinkRow`——**从不求值** |
| 设计器 | `TagInput`（与审批人多人字段同控件），标签「候选用户 / 候选组」 |

**实测证据**（真引擎，配目录，A 批已落地的工作区）：`candidate_users=["'u_fin1'"]`
→ 关联存 `("'u_fin1'", 'candidate')`，u_fin1 认领被拒（引号也算进人 key）；
`candidate_users=["users_with_role('finance')"]` → 关联存函数调用原文，永远无人可认领；
`candidate_groups=["role:finance"]` → 关联存引用、组员可认领（A 批已对齐规格）；
`candidate_users=["u_fin1"]` 能工作纯属巧合——裸 id 恰好长得像人名。

## 目标语义（规格原文落成行为）

四个取人字段（`assignee` / `assignees` / `candidate_users` / `candidate_groups`）语法完全一致，
候选槽的差异只在**接受的返回类型**与**求值时机**。

1. **进入节点时求值一次**（spec §5.5 时机表「进入节点（候选制）」），在建认领任务之前。
2. **返回值分流**（spec §5.5「mixed 自动拆槽」）：
   - 带 `user:` 前缀，或无前缀且字段是 candidate_users → **候选用户**（定格：写关联表）；
   - 带 `role:` / `org:` 前缀，或字段是 candidate_groups 的无前缀值 → **候选组**（保留引用）。
   - 规则一句话：**字段定默认，前缀定覆盖**（裸名当角色/组是仓库既有惯例，见 `split_group`）。
3. **组引用保留不展开**（spec §5.5）：关联表存带前缀的组引用字符串，认领时问目录归属
   ——A 批已把 `is_member` 接入认领判定（`tasks/candidates.py::candidate_hit` 第二式），
   本批只保证"算出组引用并正确落槽"。
4. **查无传播**：求值 null/空 → 该表达式贡献空（spec §5.5）；全部为空 → 走既有
   `no_participant_policy`（raise / to_admins），不再像今天"清单内容是什么都建任务"。
5. **规模护栏**：求值出的人数同守 `EngineConfig.max_participants`（超限拒绝，绝不截断）。
6. **求值错=技术故障**：`FormulaError` → `EngineExecutionError` → 挂起 + 故障单（错误二分一致）。

**不做**（本轮边界）：候选槽的可选性/优先级排序；`org:` 递归含子部门（宿主注册递归函数自实现，
spec §5.5 明文）；候选制的"预占/预留"语义。

## 实施落点

### ① 位点收录（设计时校验）

`definitions/validator_conditions.py::iter_expression_positions`（现 305 行——**超线**，
评审确认是并行会话在飞改动所致，HEAD 为 298；本批动它时顺带拆出守线）新增两个 yield：

```python
for index, expression in enumerate(getattr(node, "candidate_users", None) or []):
    yield "subjects", f"{node.code}.candidate_users[{index}]", expression, node.code
for index, expression in enumerate(getattr(node, "candidate_groups", None) or []):
    yield "subjects", f"{node.code}.candidate_groups[{index}]", expression, node.code
```

沿用 kind=`subjects`（规则名前缀 `expression.*`），自动获得：语法检查（`formula.invalid`）、
2KB 上限、未声明变量 ERROR、未注册函数 ERROR/WARNING、保留词位点对照（`now`/`initiator`
全位点可用，`task.*` 不可用）。

**两个必须补的口子**：

**口子一（returns 白名单）**：`validator_references.py:157` 的根调用 returns 对位现在写死
`not in ("one_user", "users")`（`subject_kinds = {"subjects","reassign"}` 在 `:80`）。
候选槽按规格**也接受 `groups` / `mixed`**（spec §5.5 槽位表），该豁免按 kind 不足。
做法：位点 kind 细分出 `candidates`（候选类，接受 one_user/users/groups/mixed），
`_RULE_PREFIX` 两 kind 同映射 `expression`（规则名不新增）；`subject_kinds` 分支按 kind
取不同白名单。

**口子二（词汇位点目录）**：`expr/catalog.py` 的位点是**按 kind 字符串**登记的
（`positions_of("prev_task") = {condition, subjects, wait_correlation}`）。新增 `candidates`
kind 后若直接拿去查目录，取人位可用的词会在候选位被**误报不可用**。做法（推荐）：
查目录前把 kind 归一 `candidates → subjects`（catalog 不动，语义上候选槽就是取人位）；
备选是给 catalog 相关条目补 `candidates`——但至少要在 `ALL_POSITIONS`、`prev_task`、
`node()` 三处重复同一件事，易漂移。两个消费方（`validator_conditions.py:200` 语法位点循环、
`validator_references.py:81` 引用检查循环）都要接这个归一。

### ② 运行时求值（核心新增）

改造点＝`tasks/candidates.py::candidate_subjects`（A 批新件，当前是「取原文并集去重」）
→「取**求值结果**并集去重」。签名为容纳求值上下文而扩参（加 `ctx` 与函数表），
唯一调用方 `tasks/behaviors.py::_create_claimable_task` 仍是一行：

```python
# tasks/candidates.py（语义升级为求值；存储形态不变）
def candidate_subjects(ctx, spec, functions) -> list[str]:
    values: list[str] = []
    for field in ("candidate_users", "candidate_groups"):
        for expression in getattr(spec, field, None) or []:
            values.extend(resolve_subjects([expression], ..., label=spec.code, what="候选"))
    return list(dict.fromkeys(values))   # 顺序去重，原样落关联
```

**关键简化：运行期不需要拆槽。** 关联表本来就是「人 id 与组引用混存」（A 批
`candidate_links` 原样读），认领判定也是按**值前缀**分流（`candidate_hit` 两式）——
所以拆槽只是规格在描述「求值结果的类型约束」，落库仍是**一个列表**，不新增第二张表、
不新增 link_type、不改 `candidate_hit`。这消掉了我原先设想的「ParticipantSet 变体」：
规格的 `ParticipantSet{subjects, groups}` 是**类型系统**层面的说法，运行时不必物化。

**求值**复用 `participants/resolver.py::resolve_subjects`（自带 prev_task 预检、错误二分、
`node()` 注入、`instance` 事实注入、护栏），与指派槽**同一求值面**——两套语义必然漂移。
调用方在 `tasks/`，被调在 `participants/`（`participants/resolver.py` 已反向引用
`tasks/facts`，两边都是函数内 import 的既有惯例）；为避免模块级环，新引用同样
**函数内 import**。

**一个要写进文档的边界**（A 批现状，本批不改）：候选组字段里的**无前缀值**按角色解释
（`is_member` → `split_group` 裸名当角色），但若它恰好与认领人 id 字面相等，
`candidate_hit` 第一式（人 key 对齐）先命中——即「精确匹配优先于按组解释」。
无害，但要说明白，免得被当成 bug 报。

**空结果**：字段声明了但求值为空 → 不建认领任务，走 `no_participant_policy`
（raise / to_admins）。**注意**：组引用是「非空结果」（保留引用不展开），
所以无目录引擎上的纯组候选仍会建任务、只是无人可认领（A 批 `test_group_only_
candidate_task_on_bare_engine_is_unclaimable` 已把该语义显式化，本批保持）。

**求值错**：`FormulaError` → `EngineExecutionError` → 挂起 + 故障单（错误二分，
与指派位同款）；`resolve_subjects` 内部已按此包，并带节点码与表达式原文。

**行数**：`behaviors.py` 294 行**贴线**，求值逻辑全部落在 `tasks/candidates.py`
（当前 132 行，有余量）；`resolver.py` 234 行不动。

### ③ 认领判定（A 批已做，本批只保证兼容）

A 批已落地 `tasks/candidates.py`：`candidate_hit` 两式判定（① `person_key` 剥 `user:` 前缀互认、
② 组引用经 `is_member` 归属），`_claim` 经 `require_candidate` 调用。**实测复核**：
组候选任务建好后关联表存 `role:finance` 原文，组员可认领、非组员被拒（展开发生在认领时，
不是建任务时——与 spec §5.5「两个槽不可拍平」一致）。

本批只需保证：拆槽后组引用照旧落进 `IdentityLinkRow`（值不变），A 批判定无需改动。

**顺带修一处文档缺陷**：`tests/suite/test_candidates.py` 的文件头说明写「配目录时按
`subjects_in` 展开为具体人，物化进候选名单——任务是谁能被认领在**建任务时定格**」——
与实现和规格**相反**（实测确认是保留引用、认领时展开）。本批动这些测试时一并改正，
否则下一个人会照错误说明继续推理。

### ④ 部署校验补齐

`validator_deploy.py::_check_subject_literals`：补 `candidate_groups` 进 positions
（现漏）。补完后候选组的字面量也会经 `subject_exists` 问目录——**仅当宿主目录实现了
该钩子**（内置两个目录都没实现，实测全程静默）。

**实施注意（避免假告警）**：该检查是「这些字面量是不是目录里的人」，而候选组里的
字面量**本来就是组引用**（`'role:finance'`）——钩子对组引用大概率回 False 而误报
「在目录中不存在」。做法：进检查前按 `split_group` 剥掉**显式组引用**
（`role:` / `org:` 前缀）不查；`candidate_groups` 字段里的**无前缀值**（按字段默认
是组名）也跳过。即：只查「会被当成人来解释」的字面量。
另一可选做法是给钩子加第二个参数区分类型——但那是扩端口面，不值得，本批用跳过。

### ⑤ 设计器

- `designer/src/components/SchemaForm.tsx` 的 `FIELD_LABELS`：改为
  「候选用户（公式）」/「候选组（公式）」。
- **提示通道已就位**：表单的 placeholder 取 pydantic 字段 `description`
  （实测 `SchemaForm.tsx:262` → `desc`），所以在 `builtin.py` 的两个 Field 上加
  `description=` 即可零前端代码下发写法提示。文案建议：
  - `candidate_users`：`取人公式，如 'u_a' 或 users_with_role("finance")`
  - `candidate_groups`：`组引用公式，如 'role:finance' 或 'org:d_eng'`

### ⑥ 文档

`spec.md` **不需要改**（规格本来就是这样，是本批要追上的目标）。需同步：

- `AGENTS.md` 当前快照加一段；
- `docs/product/engine-v1/11-设计器能力补全规划.md` 那行标签
  （`user_task.candidate_users / candidate_groups | 标签输入 | ✅`）改口径；
- `expr/catalog.py` 模块 docstring 的位点行（「subjects（取人 assignee·assignees·
  recipients）」）补候选槽两字段；
- 更新 `iter_expression_positions` 的 docstring（现在写「subjects（取人：
  assignee·assignees·recipients）」）。

## 迁移与破坏性

**零兼容**（用户拍板）：裸名（无引号）在候选槽里从此是**公式**，而不再是"人名"。

- 裸组引用 `role:legal`：CEL **语法错**（冒号非运算符）→ **设计期 ERROR**
  （`expression.formula.invalid`，与是否声明流程变量无关）。
- 裸人名 `u_a`：CEL 解析通过（当变量名）——**是否设计期报错取决于有没有声明流程变量**
  （`check_variable_references` 在零声明时直接返回，与 `assignee` 位同款既有性质）：
  有声明 → 「未声明变量」ERROR；零声明 → 设计期放行，**运行期求值失败 → 技术故障
  挂起 + 故障单**。若恰好有同名流程变量则取出该变量的值（语义变化写进发布说明）。
- 两种失败都响亮（要么设计期红、要么运行期挂起），不产生新的静默——
  静默正是本缺陷存活至今的原因。

**仓库内迁移清单**（9 个测试文件、约 20 处调用 + 说明文字）：

| 位置 | 现值 | 新值 |
|---|---|---|
| `tests/suite/test_orchestration.py:78-79` | `["u_a","u_b"]` / `["role:legal"]` | 各加引号 |
| `tests/suite/test_candidates.py`（A 批新件，4 处） | `["u_a"]` / `["role:legal"]` / `["role:finance_approver"]` / `["u_cash","u_ceo"]` | 各加引号；**文件头说明改写**（见 ③） |
| `tests/semantics/test_candidate_claim.py`（A 批新件，约 12 处） | `["u_bob"]` / `["role:finance"]` / `["org:d_eng"]` / `["finance"]` / `["user:u_bob"]` | 各加引号 |
| `tests/semantics/test_m3_task_ops.py:27` | `["u_c1","u_c2"]` | 加引号 |
| `tests/semantics/test_delta_batch2.py:176` | `["u_c1"]` | 加引号 |
| `tests/semantics/test_m4_participants.py:439` | `["u_x","u_y"]` | 加引号 |
| `tests/edge/test_hardening.py:72` | `["u1","u2"]` | 加引号 |
| `tests/edge/test_edge_stress.py:245` | `["u_x","u_y"]` | 加引号 |
| `tests/test_spi_boundaries.py:145` | `["role:finance","org:d_eng"]` | 加引号 |

注意 `tests/semantics/test_candidate_claim.py` 里同一个函数混用两种写法——
`assignee="'u_alice'"`（带引号，公式）与 `candidate_users=["u_bob"]`（不带引号，字面量）
——**这正是本批要消除的不一致**；迁移后两者同为公式。

**在途实例**：定义在发起时快照进实例，快照里旧写法的实例走到候选节点会求值失败挂起。
用户已确认**不考虑旧数据迁移**；缓解事实——候选制今天在生产本就不可用（配目录即报错），
真实暴露面接近零。发布说明记一条即可。

## 验收（测试清单）

落在 `tests/semantics/test_candidate_claim.py`（A 批新件，就地扩写）与
`tests/semantics/test_candidates_expression.py`（新件，专测求值）：

1. **字面量候选**：`["'u_a'","'u_b'"]` → 两条候选关联，两人分别可认领。
2. **函数候选（人，定格）**：`["users_with_role('finance')"]` + 目录 → 关联表存
   **展开后的两个人 id**；之后入组的人不能认领该待办（快照语义）。
3. **组引用（保留）**：`candidate_groups=["'role:finance'"]` → 关联存 `role:finance`
   原文（不展开）；组内成员认领时按目录归属命中（端到端）。
   另测一条宿主注册 `returns="groups"` 的函数（spec §5.5 的 `finance_path` 形态）
   → 返回值里的组引用照存、照命中——这是「函数产出组」的路径。
4. **混合结果原样入池**：一个函数返回「人 + 组引用」混合列表 → 全部原样落关联；
   人经精确匹配可认领、组经目录归属可认领（**运行期不拆槽**——本批的设计简化）。
5. **无前缀字段默认**：`candidate_groups=["'finance'"]`（带引号无前缀）→ 按角色解释，
   组内成员可认领（`split_group` 既有惯例）。
6. **精确匹配优先**：`candidate_groups=["'u_bob'"]`（组字段里写了人名）→ u_bob 认领成功
   （`candidate_hit` 第一式先命中）——把该边界显式钉住（见 ② 末）。
7. **空结果兜底**：所有候选表达式求值为空 → `no_participant_policy`（raise / to_admins）。
   组引用不算空（保留引用），无目录引擎的纯组候选仍建任务、只是无人可认领
   （A 批 `test_group_only_candidate_task_on_bare_engine_is_unclaimable` 语义保持）。
8. **求值错=技术故障**：未注册函数/类型错 → `EngineExecutionError` + 挂起 + 故障单
   （与 assignee 位同款断言）。
9. **规模护栏**：函数返回超过 `max_participants` → 拒绝，不落任何关联。
10. **设计时校验**：裸组引用 → ERROR（语法）；裸人名 + 有声明变量 → ERROR（未声明变量）；
    合法公式 → 无问题；位点对照（`task.assignee` 在候选位 → `expression.task_unavailable`；
    `prev_task` / `now` 在候选位**可用**——口子二的回归断言）。
11. **部署期校验**：候选组字面量经 `subject_exists`（宿主实现钩子时）→ WARNING。
12. **静态守卫**：`test_file_size_contract` 全绿；`test_api_surface` 无变化（不增删符号）。
13. **回归**：`assignee` / `assignees` / `recipients` 全位点既有测试零变化。

## 已知同源问题（A 批已修，留存登记）

- 配目录后候选制不可用（兜底顺序）——A 批已修（三段判定）。
- 组认领不展开（`is_member` 未接线）——A 批已修（`tasks/candidates.py::candidate_hit`）。
- 候选任务无查询维度——A 批已修（`list_tasks(candidate=…)`）。
- `candidate_users` 在部署校验里先于运行时被按表达式对待——本批修正后各层一致。
- 候选组字段在 L4 清单里缺失——本批补（④）。

## 实施修订（三处与正文的偏差，2026-10-10 收口）

1. **发起即失败不落实例**（验收第 8 条的表述修正）：候选节点是**首节点**时求值错，
   整笔回滚——实例行不落库，「挂起+故障单」不可见（与指派位同款既有行为）。
   「挂起+故障单」在候选节点位于**后续站**时兑现（测试用两段式流程钉住）。
   这不是本批引入的缺口，是指派/候选共有的命令边界语义。
2. **`validator_conditions.py` 的位点遍历本地副本**：实施时一度保留（拆出
   `validator_positions.py` 后未删本地 `iter_expression_positions`），导致候选位
   **语法检查被本地旧清单漏掉**（裸 `role:legal` 不报错）——收口批删除本地副本、
   改为导入唯一清单。教训已入 ①：两份位点清单必然漂移，去重即修复。
3. **验收第 10 条补一个门槛事实**：未声明变量的引用检查（含 returns 白名单）不启用
   ——这是既有门槛（`check_variable_references` 零声明即返回），候选位与之同款，
   测试需显式声明一个变量才测得到白名单。
