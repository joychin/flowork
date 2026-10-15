# 05 表达式与取人

本章讲引擎里所有「公式」怎么写：一门语言（CEL）、一份内置词汇目录、
找人的四种写法（含候选认领制）、以及宿主怎么注册自己的函数。

## 一门语言：CEL

引擎里所有表达式位——连线条件、取人、传阅、超时改派、子流程映射、会签出口——
都用同一种语言：**CEL**（Common Expression Language）。语法要点：

```text
amount > 5000                        比较
amount > 5000 && level != 'C'        与/或/非：&& || !
level in ['A', 'B']                  成员判断
title.size() > 0                     字符串/列表长度
amount > 5000 ? 'big' : 'small'      三元
null                                 空值，判空用 x != null
```

三条护栏：公式最长 2048 字符；求值异常统一归一为 `FormulaError`（消息截断防泄漏）；
**求值出错＝技术故障**——实例挂起 + 故障单等人处理，绝不静默走默认边
（语义细节见第 7 章故障处理）。

想独立试一条公式的值（比如做表单试算），宿主可以直接调：

```python
from flowork.api import evaluate_formula_value

evaluate_formula_value(
    "amount > 5000 && level != 'C'",
    variables={"amount": 8000, "level": "B"},   # True
)
```

## 内置词汇目录

公式里除了你声明的流程变量，还有一批引擎注入的**保留标识符**——不用声明、
拿起来就用（也不许声明同名变量）。完整目录可以用 API 枚举：

```python
from flowork.api import identifier_entries, engine_functions

for entry in identifier_entries():   # 10 个保留标识符
    print(entry.name, entry.label, entry.positions)
list(engine_functions())             # 引擎注入函数：node
```

速查表（「位点」＝哪些表达式位可用）：

| 词汇 | 类型 | 含义 | 位点 |
|---|---|---|---|
| `initiator` | 人 | 发起人 | 全部 |
| `instance` | map | `instance.id` / `instance.started_at` / `instance.business_key` | 全部 |
| `now` | 时间 | 求值时刻（统一时钟）；一次求值钉一个时刻 | 全部 |
| `prev_task` | map | 上一站：最近一条已办任务（assignee/outcome/finished_at 等） | 条件 / 取人 / 等待关联键 |
| `task` | map | 同名两方言：改派位＝当前任务快照；会签出口位＝三计数（见第 6 章） | 改派 / 会签出口 |
| `node("code")` | 函数 | 点名查任意节点事实：跑了几轮、最近谁办的、票数多少 | 条件 / 取人 / 等待关联键 |
| `wait` | map | 等待节点的结果（`wait.result` ＝ matched / elapsed） | 等待节点出边条件 |
| `subject` | 人 | 多实例子流程的第 k 个主体 | 子流程输入映射 |
| `child_total` / `child_completed` / `child_failed` | int | 多实例子流程三计数 | 完成公式 |

两个常用示例：

```text
条件：prev_task.outcome == 'agree'                上一站同意才走这条边
条件：node("risk_check").runs == 0                风控节点还没跑过
```

`prev_task` 查无（比如首节点后的第一站）时条件按不满足处理、取人贡献为空——温和降级，
不出故障单。`node()` 查无返回固定空形状（计数 0、人物 null）。

## 找人：四种写法

人工节点的四个取人槽位（第 3 章列过字段），这里是行为语义：

### 1. 单人：`assignee`

一个表达式，解析出**恰好一个人**。解析出多人是设计错误（想多人会签就用 `assignees`
或子流程），部署后的运行期会直接报错挂起。

### 2. 会签名单：`assignees`

表达式列表，每项解析出的人进会签池，配合 `multi_instance`（第 6 章细讲）。

### 3/4. 候选池：`candidate_users` 与 `candidate_groups`

任务生成时**不指定办理人**，一个候选池等认领（claim）：

- `candidate_users`：表达式求值出的人的集合，如 `'u_fin1'` 或
  `users_with_role('finance')`；
- `candidate_groups`：**组引用**（如 `'role:finance'`、`'org:d_eng'`）——
  注意组引用在候选槽是**保留引用、不展开**的：认领时按组织目录现场判归属，
  所以「明天才入组的人，能认领明天池子里的待办」，名单不会在建任务时定格。

一个完整的候选认领例子（实测跑通，注意第 5 行的委托现象）：

```python
from flowork import contrib
from flowork.api import EngineConfig, FlowBuilder, ProcessEngine, StaticOrgDirectory

org = StaticOrgDirectory.from_yaml("examples/org.yaml")
engine = ProcessEngine(
    EngineConfig(org=org, expression_functions=contrib.cel_vocabulary(org))
)

definition = (
    FlowBuilder("claim_demo", "候选认领演示")
    .start("start", "发起")
    .user_task("lead", "直属上级审批", assignee="manager_of(initiator)")
    .user_task("finance", "财务复核（候选池）", candidate_groups=["'role:finance'"])
    .end("end", "结束")
    .connect("start", "lead").connect("lead", "finance").connect("finance", "end")
    .build()
)
engine.repository.deploy(definition)

instance_id = engine.runtime.start_process("claim_demo", start_user="u_zhang")

lead = engine.task.list_tasks(instance_id=instance_id)[0]
print(lead.assignee)        # u_dave —— u_zhang 的上级是 u_bob，但 org.yaml 里
                            # u_bob 把办理权委托给了 u_dave，解析时委托替换生效
engine.task.complete(lead.id, actor="u_dave")

mine = engine.task.list_tasks(instance_id=instance_id, candidate="u_fin1")
claimed = engine.task.claim(mine[0].id, "u_fin1")   # 从池里认领
engine.task.complete(claimed.id, actor="u_fin1")
```

查询口径（容易踩坑，第 12 章 FAQ 有展开）：

- `list_tasks(candidate="u_fin1")`＝「与我相关的候选」，**默认含已被他人认领的任务**
  （「还在池里没被抢走的」要组合 `assignee=""` 查）；
- 认领成功后任务的 `assignee` 变成认领人；`unclaim` 可以退回池里。

### 取人解析的兜底顺序

取人表达式解析结果为空（查无此人、函数返回空）时：声明了候选槽就不兜底（池子空着
等认领）；没声明就走节点的 `no_participant_policy`——`raise`（挂起+故障单，缺省）
或 `to_admins`（交给目录的 admin 角色兜底）。

## 组织目录与词汇插件

上面用到的 `manager_of` / `users_with_role` / `users_in_org` / `has_role` 四个函数
**不在内核里**——内核不内置组织模型。它们来自官方词汇插件 `contrib.cel_vocabulary`，
需要配一个组织目录才有数据：

```python
from flowork import contrib
from flowork.api import EngineConfig, ProcessEngine, StaticOrgDirectory

org = StaticOrgDirectory.from_yaml("examples/org.yaml")   # YAML 静态目录
engine = ProcessEngine(
    EngineConfig(
        org=org,                                        # 目录挂进引擎
        expression_functions=contrib.cel_vocabulary(org) # 词汇注册（全位点生效）
    )
)
```

目录的 YAML 形状（users / departments / delegations / admins 四段）：

```yaml
users:
  u_zhang: {manager: u_bob, department: d_eng, roles: [developer]}
  u_fin1:  {manager: u_ceo, department: d_fin, roles: [finance]}
departments:
  d_eng: {head: u_bob, parent: d_all}
delegations:
  - {from: u_bob, to: u_dave, until: "2099-12-31"}
admins: [u_admin]
```

- `delegations` 段是**委托**：窗口期内 `u_bob` 的待办解析给 `u_dave` 代办
  （单跳、不递归、撤销留痕）——上面例子中 `manager_of(u_zhang)` 落到 `u_dave`
  就是它的效果。
- 生产环境一般不用 YAML 静态目录，而是实现 `OrgDirectoryLike` 协议（四个方法：
  查快照 / 组→人 / 查委托 / 查管理员）对接自己的组织系统；或用 DB 版
  `DirectoryStore`（第 10 章）。
- 查无一律返回空值不抛异常（None/空传播），目录没配置就调组织词汇是技术故障。

另一个官方插件 `contrib.numeric_vocabulary()` 补六个数值/字符串散件
（`split` / `abs` / `max` / `min` / `sort` / `sum`），挂法相同。

## 注册你自己的函数

宿主自定义函数实现 `ExpressionFunctions` 协议——一个只有 `functions()` 方法的最小类，
返回「函数名 → ExpressionFunction」的表；每个函数用 `FunctionSignature` 声明参数与
返回类型（设计期校验据此检查函数名与元数）：

```python
from flowork.api import (
    EngineConfig, ExpressionFunction, FlowBuilder, FunctionSignature, ProcessEngine,
)

class VipVocabulary:
    def functions(self):
        def vip_level(user: str) -> str:
            return "gold" if user == "u_ceo" else "normal"
        return {
            "vip_level": ExpressionFunction(
                FunctionSignature(("one_user",), "string", label="客户等级"),
                vip_level,
            )
        }

engine = ProcessEngine(EngineConfig(expression_functions=VipVocabulary()))
```

之后条件与取人位都能写 `vip_level(initiator) == 'gold'`——发起人是 `u_ceo` 时走
专属审批臂，其他人走默认边直达终点（实测跑通）。两个注意点：

- 函数要**纯读**（不写变量、无副作用）——路由决策只属于引擎+条件求值；
- 返回 Python 原生值即可，注册面会统一抬升成 CEL 值；但参数侧收到的已是 CEL 值，
  null 要先判空再喂给自己的逻辑。

未注册的函数在设计期校验就报 ERROR（有注册表时）——图纸不会带着拼错的函数名上生产。

## 本章小结

- 一门语言 CEL、十个保留标识符、一个 `node()` 函数——条件与取人共用同一套词汇。
- 找人四槽：单人 `assignee`、会签 `assignees`、候选 `candidate_users` / `candidate_groups`；
  候选池不指定办理人，等认领；组引用保留不展开、认领时现场判归属。
- 组织词汇是插件不是内核：`contrib.cel_vocabulary(org)` 一行挂上；委托在取人解析时替换生效。
- 宿主自定义函数走 `ExpressionFunctions` 注册，纯读、声明签名。

下一章讲任务办理的完整动作面：办结、驳回、会签、加签、协作与超时。
