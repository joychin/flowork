# 05 办理任务

本章讲任务（待办）的完整动作面：查询与认领、办结与驳回、会签与加签、三种不占票的
协作（协办 / 咨询 / 传阅）、超时动作。审批业务的日常几乎都在这一章里。

先认识一个贴心字段：每个 `TaskInfo` 自带 **`available_actions`**——服务端按任务的
状态 × 类型 × 认领态算好的「现在能对它做什么」，前端与 AI 拿到任务就知道能干什么，
不必自己拼规则：

```text
待办任务 available_actions（实测）:
['complete', 'reject', 'transfer', 'assist', 'add_countersigner', 'consult', 'unclaim']
```

所有办理动作都带 `actor`（办理人）——引擎的归因铁律，每个动作落已办、发事件都靠它。

## 查询与认领

```python
engine.task.get_task(task_id)                            # 单条
engine.task.list_tasks(instance_id=iid)                  # 实例的全部待办
engine.task.list_tasks(assignee="u_manager")             # 某人的待办
engine.task.list_tasks(candidate="u_fin1")               # 我可认领的候选（见第 4 章）
engine.task.list_tasks(assignee="")                      # 还没被认领的池内任务
```

候选池任务的认领与退回（第 4 章演示过，这里补齐）：

```python
engine.task.claim(task_id, "u_fin1")     # 认领：assignee 变为认领人
engine.task.unclaim(task_id, "u_fin1")   # 退回池子：assignee 清空，别人可再认领
```

传阅件单独查：`engine.task.list_cc_tasks(recipient="u_zhang")`，读过的标记
`engine.task.mark_cc_read(cc_id, user)`。

## 办结与驳回

### complete：同意并推进

```python
result = engine.task.complete(task_id, actor="u_manager",
                              comment="同意", variables={"approved": True})
```

`comment` 是办理意见（进事件与已办）；`variables` 顺带写流程变量（经过 input 契约
校验）。返回 `TaskCompleteResult`：`instance_state`（推进后的实例状态）和
`next_tasks`（推进产生的新待办）——「办完这一步之后谁要办什么」一步拿到，
不必再查一次。

### reject：否决并回退

```python
engine.task.reject(task_id, actor="u_bob", comment="材料需补充",
                   target="manager")     # target 缺省用节点定义的 reject_target
```

驳回不是「结束流程」，而是**回退**：否决当前节点，流程退到 `target` 指向的节点
（通常配在图纸的 `reject_target` 字段上），那边**重新生成待办**（新任务行）。
修改后再走一遍，就是「驳回 → 重提」的日常形态。

一个实测的完整轮回（会签节点带 `reject_target="manager"`）：

```text
u_bob 在总监会签里 reject（disapprove_exit: task.disapprove_count >= 1 一票否决）
  -> 经理节点出现新待办（u_manager 重新要办）
  -> u_manager 修改后重新 complete
  -> 再入会签，全员通过
已办历史里 u_manager 有两行——两轮各一行，时间线完整可审计
```

**驳回复活名单圈定「最近一轮」**：会签被驳回后重新提交，只有最近一轮参与过的人
再进名单——多轮驳回不会让名单越滚越大（与多数引擎「重入全量重解析」的刻意差异，
spec §5.1 有论证）。

## 转办与撤回

### transfer：改派

```python
engine.task.transfer(task_id, to_user="u_backup", actor="u_manager", comment="休假代办")
```

把任务改派给别人办——**票随人走**：转办人不算已参与表决，被转办人办了才算这一票
（多问一次人，好过替人投一票）。原任务归档为 transfer，新任务行的办理人是被转办人。

### withdraw：撤回

```python
engine.task.withdraw(task_id, actor="u_a", comment="填错了")
```

办结之后反悔，把自己那一票**撤回来**：任务回到待办状态重新办。两个守卫：

- **一票一撤**：同一票不能撤两次；
- **下游进展闸门**：撤回会取消下游还没办的东西，所以只要下游**已有非取消的已办**
  （有人真的办了事），撤回被拒绝并提示改用驳回——能撤的只有「下游还没人动」的窗口。

实测两种形态：`u_a` 办结后下游 `u_b` 还没办，撤回成功、A 节点重新出现待办
（B 的待办被取消）；实例已到终态再撤，直接被拒。

## 会签与加签

### 会签：multi_instance

会签 = 多人参与的审批节点。图纸写法：

```python
.user_task(
    "directors", "总监会签",
    assignees=["'u_alice'", "'u_bob'"],
    multi_instance=MultiInstance(
        mode="parallel",                                        # 或 sequential
        approve_exit="task.approve_count >= task.item_count",   # 通过口径
        disapprove_exit="task.disapprove_count >= 1",           # 否决口径
    ),
)
```

- `mode="parallel"`：全员同时有待办（并签）；`mode="sequential"`：逐人依次办
  （串行会签——前一人办完才有下一人的待办）。
- **出口是 CEL 布尔公式**，上下文注入 `task` 三计数：
  - `task.item_count`：应办人数
  - `task.approve_count`：同意票数
  - `task.disapprove_count`：否决票数
- 缺省（不写公式）＝该侧全票：`approve_exit` 缺省全员同意才过、
  `disapprove_exit` 缺省全员否决才驳。

常用口径速查：

```text
全票通过        task.approve_count >= task.item_count
一票否决        task.disapprove_count >= 1
过半数（严格）   task.approve_count * 100 >= task.item_count * 51
三分之二以上     task.approve_count * 100 >= task.item_count * 67
```

整数乘法写法是刻意的：百分比语义用乘法精确等价（ceil），不引入浮点误差。
全员的票办完而两侧出口都没达成时（比如绝对门槛没够但没人否决），节点**挂起 +
故障单**兜底（第 6 章），不会静默卡死。

### add_countersigner：加签

```python
engine.task.add_countersigner(task_id, ["u_cfo"], actor="u_bob", comment="金额大，加财务总监")
```

办理中途临时拉人进来一起签——**加签的人各占一票**（进三计数），会签门槛随之
重算（人多了，过半需要的票也多）。不占票的协作见下一节。

## 三种「不占票」的协作

会签是「加人加票」，这三种是「加人不加票」——各自语义不同，别混用：

| 动作 | 谁触发 | 语义 | 主任务受影响吗 |
|---|---|---|---|
| `assist` 协办 | 办理人 | 派生协办任务给助手，帮着准备材料；助手交回（`complete_assist`）即收口 | 不影响办理 |
| `consult` 咨询 | 办理人 | 派生意见任务给专家，**主任务软挂起**——意见全收回前主任务什么都不能办；意见带 agree/reject 立场但**不计票** | 挂起到意见交回 |
| `cc_task` 传阅 | 图纸定义 | 进入传阅节点时给收件人每人一条传阅件，随即推进 | 完全不阻塞 |

```python
assist_tasks = engine.task.assist(task_id, ["u_helper"], actor="u_manager")
engine.task.complete_assist(assist_tasks[0].id, actor="u_helper", comment="材料已核")

consult_tasks = engine.task.consult(task_id, ["u_lawyer"], actor="u_manager")
engine.task.complete_consult(consult_tasks[0].id, actor="u_lawyer",
                             opinion="agree", comment="无风险")
# 意见交回后主任务自动解禁，正常 complete
```

咨询的「软挂起」是给「先收集意见、原办理人最后拍板」的两轮形态用的：咨询期间
主任务的 complete / reject / transfer 全被拦，主任务的超时时钟**照走**——
别拿 consult 挡超时。

## 超时

人工节点可以配超时动作列表（`deadlines`，同节点上限 8 条，按时间排序逐条触发）：

```python
from flowork.definitions.node import TaskDeadline

.user_task(
    "manager", "经理审批",
    assignee="'u_manager'",
    reject_target="apply",
    deadlines=[
        TaskDeadline(after_minutes=25, action="notify"),          # 预警
        TaskDeadline(after_minutes=30, action="auto_reject"),     # 到点自动驳回
    ],
)
```

五种动作：

| action | 到点做什么 | 附带字段 |
|---|---|---|
| `notify` | 发 `timeout_warned` 事件（提醒谁的活到期了） | — |
| `reassign` | 改派给目标人 | `reassign_to`（CEL，`task.*` 可用） |
| `escalate` | 升级给上级处理 | `escalate_to`（CEL，**必填**——引擎不内置「上级」概念，科层组织写 `manager_of(task.assignee)`） |
| `auto_approve` | 自动替他同意 | — |
| `auto_reject` | 自动替他否决（走驳回回退） | — |

计时从**进入节点**起算，自然分钟。谁来「到点」？**超时 Job**。引擎把每条 deadline
排成定时任务，需要有人调 `engine.jobs.tick()` 执行到期的——三种驱动方式：

```python
engine.jobs.tick()                     # 手动推一把（测试 / 外部定时器）
engine.jobs.start(interval_seconds=30) # 引擎内置定时线程（web 形态默认开）
uv run flowork tick                    # 独立 CLI worker（多副本部署用这个）
```

测试里配假时钟可以确定性验证超时（实测）：

```python
engine = ProcessEngine(EngineConfig(now=clock))   # clock.advance_minutes(31)
iid = engine.runtime.start_process("deadline_demo", start_user="u_zhang")
# ... 发起人提交，经理待办出现 ...
clock.advance_minutes(31)
engine.jobs.tick()      # 触发 auto_reject：经理待办消失，发起人（reject_target=apply）重新待办
```

## 本章小结

- `available_actions` 服务端告诉你能做什么；一切动作带 `actor`。
- `complete` 推进（返回 `next_tasks`）；`reject` 回退到 `reject_target` 重新生成待办，
  复活名单圈定最近一轮。
- `transfer` 票随人走；`withdraw` 一票一撤 + 下游进展闸门。
- 会签 = `multi_instance` + 三计数出口公式；加签各占一票；协办 / 咨询 / 传阅不占票，
  咨询会软挂起主任务。
- 超时五动作由 `tick` 驱动；`escalate_to` 必填表达式（组织模型归宿主）。

下一章讲实例层面的管理：暂停终止、变量读写、故障处理与模拟试跑。
