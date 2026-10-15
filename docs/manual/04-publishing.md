# 04 流程发布与更新

第 3 章把图画对了，本章讲图纸的**一生**：怎么发出去（deploy 管线）、怎么判这次
改动算大算小（版本与破坏性规则）、改了之后在跑的实例怎么办（快照语义）、
以及怎么退役与对账。这一章的语义是「多副本安全发布」的地基——理解了它，
你才敢放心地天天改流程。

## 先建立模型：版本三段式与不可变发布

一个定义的身份 = `code + major + revision`：

- `code`：业务身份（「费用报销」这张图）；
- `major`：主版本——**破坏性变更**才加一（1.x → 2.0）；
- `revision`：修订号——兼容性改动在同一 major 下加一（1.1 → 1.2）。

两条铁律：

1. **deploy 即 publish**：没有独立的「发布」步骤，`deploy` 落库即可用。
   也没有 draft 状态——编辑态在你的 git / 设计器草稿里，不占引擎的版本序列。
2. **发布即不可变**：每个已发布版本的内容永远不变（行不可改）。要新行为就
   发新版本，版本只增不减。

为什么这么设计：在跑的实例钉着老版本快照（第 2 章），如果已发布内容可以原地改，
「钉版本」就成了空头承诺。不可变是快照语义的前提。

## deploy 到底做了什么

`repository.deploy(definition)` 一次调用、一个事务，按顺序做六件事：

```text
1. 设计期强校验     assert_valid——第 3 章那些 ERROR 规则，一条不达标整笔回滚
2. 部署期引用校验   L3/L4（见下文）——默认 WARNING 留痕，strict 可拦
3. 规范化与哈希     内容剥离版本/画布字段 → 规范 JSON → SHA-256（内容身份证）
4. 幂等去重         哈希与任一已发布版本相同 → 直接返回 identical_to，不落库不发事件
5. 版本判定         与基线逐条对比 R1–R4，算出 (next_major, next_revision) 与破坏性明细
6. 落库 + 事件      写入版本行（内容/哈希/判定结果/发布人），发 definition_deployed 事件
```

签名与全部参数：

```python
verdict = engine.repository.deploy(
    definition,
    published_by="system",       # 发布人（归因，进事件）
    target_major=None,           # 维护分支：在该 major 的最新 revision 上续发
    force_major_reason="",       # 强制升 major，必须带理由
    allow_minor_reason="",       # 强制不升 major，必须带理由
)
```

返回 `VersionVerdict` 判定报告——**先看懂这个返回值**，它有六个字段：

| 字段 | 含义 |
|---|---|
| `next_major` / `next_revision` | 本次落库的版本号 |
| `breaking` | 是否破坏性变更 |
| `triggered_rules` | 触发的破坏性规则明细（规则编码 + 中文说明，逐条可解释） |
| `content_hash` | 内容哈希（发布不可变 ⇒ 同哈希即同内容） |
| `identical_to` | 非 None ＝ 幂等去重命中：内容与该 `(major, revision)` 完全相同，本次没落库 |
| `override` | 非 None ＝ 你用 force_major / allow_minor 覆盖过判定，记录了 kind 与 reason |

一个完整的版本演进实测（同一张报销图的连续六次 deploy）：

```text
首发           -> (1, 1) breaking=False                # 新 code 从 1.1 起
改名           -> (1, 2) breaking=False                # 兼容改动，revision 加一
插入前置核验节点 -> (2, 1) breaking=True                 # R4 拓扑破坏，major 加一
                 触发: R4_topology - 节点 audit 入边变化：[] -> ['manager']
                 触发: R4_topology - 节点 route 入边变化：['manager'] -> ['audit']
撤掉核验节点    -> (3, 1) breaking=True                 # 回退拓扑也是拓扑变更！
同内容再发      -> identical_to=(3, 1)                  # 幂等去重，不产生新版本
```

注意第 5 行：**把改动改回去也是破坏性变更**——R4 比较的是新旧拓扑的差异，
不关心「变回从前」。版本号是单调递增的账本，不是可以回退的指针。

## 幂等去重：重复部署是安全的

运维与 CI 里最常见的场景：同一份模板部署两遍（重启重放、多环境同步）。
deploy 按**内容哈希**识别：剥离 `major / revision / status / layout` 后的
规范 JSON 完全一致，就认定「这个版本已经发过」——不落新版本、不发事件，
返回 `identical_to=(major, revision)` 指向那个已发布版本。

实测：

```python
engine.repository.deploy(definition)    # -> (1, 1)
verdict = engine.repository.deploy(definition)   # 再发一次
verdict.identical_to                    # (1, 1) —— 没有产生 1.2
```

画布坐标挪一挪再发**不算新版本**（layout 不参与哈希）——设计器里拖动节点
不会污染版本序列。同理，并发部署同一内容撞车由 `(code, major, revision)`
唯一索引兜底，第二个事务落库失败，不会出现两个 1.1。

## 破坏性判定：R1–R4 逐条讲

deploy 拿「待发布」与「基线」（同 code 的最高已发布版本）逐条对比四条规则，
**任一命中即 breaking**，major 加一。规则本身也是判定输出的一部分
（`triggered_rules` 带中文明细），不需要背——理解每条在保护什么：

### R1 输入契约（保护发起方的既有调用）

required 扩大、既有字段类型变化、enum 收窄、关闭多余字段容忍
（`additionalProperties` → false）、**契约从无到有**——都算。
一句话：新版开始拒收老调用方一直能发的请求，就是破坏。

### R2 索引契约（保护宿主的检索与报表）

`searchable` 变量集合变化（增或删都算）——宿主按变量值建了索引 / 报表，
集合一变查询就漂移。

### R3 节点身份（保护按节点 code 编程的下游）

已发布节点的 code 被删除、或同 code 节点的 kind 改变。下游系统（报表、
订阅方、`node("code")` 表达式）按 code 认节点——code 消失或换类型，
它们的引用就成了悬空指针。

### R4 拓扑（保护路由语义）

任一节点的**入边集合**变化。这是最容易被无意触发的：往流程中间**插一个
节点**，会把插入点后继节点的入边改掉；把条件分支改成并行、把汇聚点挪位置，
都会命中。改名、改条件公式、改取人表达式**不算**拓扑破坏——它们不改变
「谁能到达谁」。

四条之外的常见疑问：

- **改取人 / 改条件公式 / 改超时配置**：不触发 R1–R4，算兼容性修订
  （revision +1）。它们改变行为但不破坏既有调用方与下游契约；
- **删变量声明**：不触发四条规则（但 `searchable` 变量的增删触发 R2）。
  删了声明，引用它的条件会在**设计期校验**被拦——图改完自证，不靠版本判定；
- **加节点 / 删节点**：删节点命中 R3；纯新增一个挂在末尾的节点（不改任何
  既有节点入边）四条都不命中——算兼容性扩展。

## 覆盖判定：force_major 与 allow_minor

判定是机械的，业务偶尔比机械更懂上下文。两个口子，**都必须带 reason**
（进事件，留审计痕迹）：

```python
# 场景一：判定说兼容，但你想升 major（比如配合一次大改版的对外公告）
engine.repository.deploy(definition, force_major_reason="年度改版，对外重发")

# 场景二：判定说破坏（命中 R4），但业务裁定语义兼容
engine.repository.deploy(
    definition,
    allow_minor_reason="入边变化仅补充核验步骤，语义兼容（业务裁定 2026-10-15）",
)
```

实测 allow_minor 的返回：`(next_major, next_revision)` 照常推进 revision、
`breaking=False`、`override={'kind': 'allow_minor', 'reason': '...'}`。
两个参数互斥（同时传直接报错）。

配套还有**预演**——只判定不落库，CI 里先看后果再真发：

```python
preview = engine.repository.verdict(definition)     # 同款 VersionVerdict，零副作用
```

想给人看的「这次改了什么」清单，用 `semantic_diff`（中文变更条目，
不做判定）：

```python
from flowork.api import semantic_diff

changes = semantic_diff(base_definition, target_definition)
# [{'type': 'node', 'action': 'removed', 'key': 'audit', 'detail': '删除节点 audit（user_task）'},
#  {'type': 'flow', 'action': 'added', 'detail': '新增连线 manager->route'}, ...]
```

一个使用细节：`semantic_diff` 不做规范化——一边是刚 `build()` 出来的裸形态、
另一边是库里 round-trip 过的全字段形态时，会报出一堆默认字段的「假差异」。
两侧走同一条装载路径（都过 `load_definition`）再比，输出才干净。

## 部署期引用校验（L3 / L4）

设计期校验只看图纸自身；deploy 事务内还有第二层——**引用完整性**，要查库、
查注册表、查目录（默认 WARNING 留痕不拦发布，`EngineConfig.\
strict_deploy_validation=True` 全部升 ERROR 拦发布）：

| 层 | 查什么 | 默认级别 | 为什么不拦 |
|---|---|---|---|
| L3a | `call_activity` 目标定义：`binding=deployment` 未发布 | **ERROR（必拦）** | 部署时绑定关系就该成立（Zeebe 同语义） |
| L3b | `call_activity` 目标：`binding=latest` 未发布 | WARNING | 子流程可独立部署，运行期解析最新版；自引用跳过 |
| L3c | `service_task` 的 handler 未注册 | WARNING | **先部署后注册是合法模式**（第 10 章的 handler 随时可补） |
| L4 | 取人位字符串字面量问目录「这个人存在吗」 | WARNING | 目录没实现该能力时零噪音跳过；False 才提示 |

设计哲学是「**可见优于静默**」：引用问题默认留痕（日志与判定输出可见），
只有明确不可能自愈的（deployment 绑定缺失）才硬拦。strict 模式给想要
fail-fast 部署流水线的团队。

## 更新之后：在跑实例怎么办（快照语义）

发新版本后，三类对象各走各的路：

```python
# 老实例：不受影响，按它发起时钉住的版本走完
engine.runtime.get_instance(iid).definition_major    # 实例行上记着钉的版本

# 新实例：默认用最高已发布版本
engine.runtime.start_process("expense", start_user="u_z")

# 要用指定版本发起（比如灰度老版本）
engine.runtime.start_process("expense", start_user="u_z",
                             major=1, revision=2)     # 建议成对传，钉死精确版本
```

**没有「在跑实例迁移到新版本」的操作**——那本质是数据迁移（新老图纸对不上，
令牌位置怎么映射没有通用答案），引擎有意不做。老实例跑完老版本，新实例走
新版本，这就是全部。查「多少实例还钉在老版本上」用
`runtime.list_instances(major=..., revision=...)`。

版本解析规则（`get_definition` 与 `start_process` 同一条路）：

- 都不传：最高 published（major 优先、revision 次之）；
- 只传 `major`：该 major 下的已发布版本（**没有「取该 major 最新 revision」
  的排序保证**）——要钉死请 `major + revision` 成对传；
- 查不到 published：报 `DefinitionNotPublishedError`；全 retired 报
  `DefinitionRetiredError`。

## 维护分支：给老 major 续发补丁

破坏性升到 2.x 之后，1.x 上还钉着一批在跑实例，要给它们修个 bug 怎么办？
`target_major` 让你把新 revision 发在**指定 major** 上：

```python
v = engine.repository.deploy(patch_definition, target_major=1)
# 实测：1.x 最新是 1.2 -> 本次落 (1, 3)；默认分支不受影响，最新版仍是 2.1
```

判定基线也换成该 major 的最新 revision（比较对象对了，判定才准）。
配合 `start_process(major=1, revision=3)` 就能「老版本吃老补丁」。

## 退役与对账

### retire：下线

```python
engine.repository.retire("expense", actor="admin", reason="业务下线")
```

该 code **全部** published 行转 retired：在跑实例照常跑完，新发起被拒
（`DefinitionRetiredError`，报错信息里写明「重新 deploy 可恢复」）。

**恢复＝重新 deploy**：retired 不终结版本账本——再 deploy 时版本号越过
既有最高继续走（实测：retire 时最高是 3.2，恢复部署落 **3.3**，不会回 1.1）。

### purge：物理删除

```python
engine.repository.purge("expense", actor="admin", force=True)   # 仅 CLI 暴露 --force
```

物理删除全部版本行，**有实例引用（在途或历史）则拒绝**——先核对实例清单。
不可逆、不留事件以外的痕迹，只给「彻底清掉试验定义」这类场景。

### drift：git 与库对账

模板放 git、部署进库，两边会漂。`definition_drift(directory)` 扫目录下全部
JSON 与库里每 code 的最高 published 哈希对账，返回三张清单：

```python
engine.repository.definition_drift("templates/")
# {'undeployed':  [{'code': 'new_flow'}],           # 文件有、库里没有
#  'extra_in_db': [],                               # 库里有、文件没有
#  'stale_in_db': [{'code': 'expense', ...}]}       # 两边都有但哈希不一致（库落后）
```

CI 里挂一条 drift 检查，防止「改了模板忘部署」「库里混进手发定义」。
CLI 同款：`uv run flowork drift <目录>`。

### 查看与取回定义

```python
engine.repository.list_definitions(latest_only=True)      # 清单（默认每 code 只列最高版）
engine.repository.get_definition(code, major=None, revision=None)      # 模型
engine.repository.get_definition_json(code, major, revision)           # 原始 JSON（快照同款）
```

## 版本操作速查

| 想做什么 | 用什么 |
|---|---|
| 首发 / 更新（一体） | `deploy(definition)` |
| 先看这次改动会判成什么 | `verdict(definition)`（预演，不落库） |
| 给人看「改了什么」 | `semantic_diff(base, target)` |
| 重复部署同一内容 | 直接再 deploy——幂等去重，`identical_to` 告诉你命中 |
| 强行升 / 不升 major | `force_major_reason` / `allow_minor_reason`（必带理由） |
| 给老 major 发补丁 | `deploy(..., target_major=N)` |
| 用指定版本发起 | `start_process(..., major=N, revision=M)` |
| 下线（可恢复） | `retire(code)`；恢复＝重新 deploy |
| 彻底删除 | `purge(code, force=True)`（仅 CLI） |
| git / 库对账 | `definition_drift(dir)` / CLI `flowork drift` |

## 本章小结

- 版本三段式（code + major + revision），deploy 即 publish、发布即不可变、
  内容哈希幂等去重——重复部署与并发部署都安全。
- 破坏性四规则 R1（输入契约）/ R2（检索索引）/ R3（节点身份）/ R4（拓扑），
  判定报告逐条可解释；业务可以带理由覆盖（force_major / allow_minor）。
- 在跑实例钉快照，没有实例迁移这回事；老 major 可用 target_major 续发补丁；
  retire 可恢复（版本继续走），purge 不可逆（仅 CLI）；drift 对账防漂移。
