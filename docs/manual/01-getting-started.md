# 01 五分钟跑通

本章讲三件事：flowork 是什么（一句话）；用 Python 从零跑通一个完整的审批流程
（部署 → 发起 → 办理 → 结束 → 看事件）；以及不想写代码时可以用的体验命令。

## flowork 是什么

flowork 是一个**流程引擎内核**：你把业务流程画成一张有向图（谁审批、条件怎么走、
超时怎么办），交给它部署；之后每次发起一笔业务，它就照着图纸推进——生成待办、
记录已办、按条件选路、到终点收尾，全程留痕。

「内核」意味着它是一个 Python 库，不是一个大系统。你把它嵌进自己的应用里，
通过 `flowork.api` 这一个入口使用它：

```python
from flowork.api import ProcessEngine

engine = ProcessEngine()   # 零配置，默认内存数据库，构造即建表
```

同一份内核有三种用法形态，本手册以第一种为主线：

1. **嵌入式 Python**（本手册主线）：宿主应用直接持有 `ProcessEngine`，走 Python API。
2. **命令行**：`flowork demo` 等命令，无代码体验与运维操作（见第 11 章速查表）。
3. **独立服务**：REST `/v2` 与 MCP 两层插件把内核包成 HTTP / AI 工具面（第 11 章概览）。

## 跑通第一个流程

下面这段代码是完整可跑的（仓库根目录下执行 `uv run python` 即可），它做了五件事。
对照的流程是 `examples/01_simple_approval.json`——一个最简单的串行审批：
发起 → 经理审批 → 财务复核 → 结束。

### 第 1 步：建引擎

```python
from flowork.api import ProcessEngine

engine = ProcessEngine()
```

`ProcessEngine` 不带参数时用内存 SQLite 数据库，构造时自动建表，进程结束数据即消失——
适合试验和测试。要用文件库或 PostgreSQL，改 `EngineConfig` 即可（第 11 章细讲）。

### 第 2 步：部署流程定义

```python
from flowork.api import create_registry, load_definition_file

definition = load_definition_file(
    "examples/01_simple_approval.json", create_registry()
)
engine.repository.deploy(definition)
```

`load_definition_file` 把 JSON 模板读成 `ProcessDefinition` 对象；
`create_registry()` 提供内置的八种节点类型（第 3 章逐个讲）。
`deploy` 是「部署即发布」：校验通过后落库，立即可用来发起实例，并给出版本判定结果。

### 第 3 步：发起实例

```python
instance_id = engine.runtime.start_process(
    "simple_approval", start_user="u_zhang", variables={"amount": 500}
)
```

用定义的 `code`（这里是 `simple_approval`）发起。`start_user` 是发起人，
之后表达式里可以用 `initiator` 引用他；`variables` 是这笔业务的流程变量
（这里是金额 500）。返回实例 ID——后续所有操作都靠它。

### 第 4 步：办理任务

```python
actor_by_activity = {"manager": "u_manager", "finance": "u_finance"}
for _ in range(5):
    tasks = engine.task.list_tasks(instance_id=instance_id)
    if not tasks:
        break
    task = tasks[0]
    actor = actor_by_activity.get(task.activity_code, task.assignee)
    engine.task.complete(task.id, actor=actor, comment="同意")
```

流程走到「经理审批」节点时会生成一条待办任务。`list_tasks` 查当前待办，
`complete` 办结它并附上办理人（`actor`）与意见——**每个动作都要带办理人**，
这是引擎的归因铁律。办结经理审批后引擎自动推进到财务复核，再办结一次，
流程就到终点了。

### 第 5 步：看结果与事件回放

```python
state = engine.runtime.get_instance(instance_id).state
print(state)                                    # completed

for event in engine.history.events(instance_id):
    print(event.event_name, event.payload)
engine.close()
```

实例状态变成 `completed`。`history.events` 回放整笔流程发生过的事件——
从 `process_started` 到 `process_completed`，中间每个节点的进出、每个任务的
创建与办结都有一条（真实输出节选）：

```text
process_started  {'definition_code': 'simple_approval', 'major': 1, 'revision': 1, 'start_user': 'u_zhang', ...}
node_entered     {'activity_code': 'start', ...}
task_created     {'activity_code': 'manager', 'assignee': 'u_manager', 'task_type': 'approval', ...}
task_completed   {'activity_code': 'manager', 'assignee': 'u_manager', 'actor': 'u_manager', 'outcome': 'agree', 'comment': '同意'}
node_entered     {'activity_code': 'finance', ...}
process_completed {'end_status': 'completed'}
```

事件是引擎对外通知的唯一通道（内核不做推送，消费方式见第 9 章）。

## 不写代码先看看

仓库提供了两条体验命令（在源码仓库内运行）：

```bash
uv run flowork demo        # 跑通示例 01：发起 → 经理审批 → 财务复核 → 结束，回放事件表
uv run flowork demo-full   # 全语义演示：条件分支 → 会签 → 驳回 → 重提 → 传阅 → 流转轨迹表
```

`demo-full` 覆盖了审批引擎最核心的几个语义（分支怎么选路、会签怎么数票、
驳回怎么回退重提），跑一遍再看它的输出，能对第 3、5 章要讲的内容建立直觉。

还有一个更贴近真实使用的「流程演练台」，用剧本驱动一个真实实例逐步走查，
每步打印一张「步单」（路由怎么选的、变量怎么变的、发了什么事件）：

```bash
uv run flowork walkthrough examples/04_expense_claim.json --script examples/04_expense_walkthrough.json
```

## 本章小结

- 一切从 `from flowork.api import ...` 开始：`ProcessEngine` 是引擎门面，
  挂着 `repository`（定义）、`runtime`（实例）、`task`（任务）、`history`（历史）
  等服务。
- 生命周期五步：建引擎 → 部署定义 → 发起实例 → 办理任务 → 查状态与事件。
- 所有动作带 `actor`；所有查询走只读的 history 服务。
- 内存库零配置即跑，持久化换个配置的事。线程注意：内存库是**单连接**，引擎已在连接层串行化（并发命令排队执行），多线程宿主可用但吞吐受限——生产多线程/多副本请用文件库或 PostgreSQL。

下一章把这五步背后涉及的概念（实例、令牌、任务、变量、事件）讲清楚。
