# 09 观察与集成

本章讲怎么「看」流程、怎么把引擎接进外部系统：事件消费、路由审计、轨迹查询、
给外部 worker 派活。

## 事件消费（outbox）

内核不推送、不回调——所有对外通知都是**事件落 outbox 表，你自己拉**。这个设计
让集成变得非常朴素：一个消费者循环 + 一个幂等去重就够了。

引擎自带消费参考实现（`OutboxConsumer` + `IdempotentSink`）：

```python
from flowork.api import IdempotentSink, OutboxConsumer

delivered = []
consumer = OutboxConsumer(engine.session_factory, IdempotentSink(delivered.append))
consumer.poll()     # 拉取未投递事件，逐条交给回调
```

两个承诺配合起来得到「恰好一次」的效果：

- **至少一次**：事件不会丢（投递成功才标记）；
- **幂等去重**：`IdempotentSink` 按事件 id 记录已投递集合，重复拉取不会重复交付
  （实测连拉两次 `poll()`，回调收到的条数不变）。

回调收到的是 CloudEvents 形状的信封，常用字段：`type`（事件名）、`id`
（全序可排序）、`time`、`data`（载荷）。同一实例的事件按 `(time, id)` 排出
**确定的先后顺序**——回放与对账不会随机跳。

消费侧的典型用法是「事件驱动任务通知」：

```python
def handle(envelope):
    if envelope["type"] == "task_created":
        notify_user(envelope["data"]["assignee"], ...)   # 推送归宿主（内核不做）

consumer = OutboxConsumer(engine.session_factory, IdempotentSink(handle))
```

事件名按 `DomainEvent` 枚举编程（`from flowork.api import DomainEvent`）——
引擎自发事件的枚举即全集，不会悄悄冒出新名字。按粒度需求配
`EngineConfig.history_level`：`full`（默认全记）/ `audit` / `activity`
（只记 process_/node_/incident_ 前缀）/ `none`。

运维细节（清理窗口、多副本消费的并发口径）见 `docs/integration.md`。

## 路由审计（route_decisions）

「这一步为什么走了那条边」是有记录的——每次路由一行，含每条条件边的求值结果：

```python
for row in engine.history.route_decisions(instance_id):
    print(row.source_activity, row.edges, row.selected)
```

实测输出（金额 5000 走大额臂的实例）：

```text
路由: start edges= []                                    selected= ['start->route']
路由: route edges= [{'flow': 'route->big',
                     'formula': 'amount > 1000',
                     'result': 'true'}]                   selected= ['route->big']
```

- `edges`：每条**条件出边**的公式与求值结果（`true` / `false` / `no_fact`——
  事实缺失按不满足处理）；
- `selected`：实际激活的边；
- `snapshot`：求值时刻的事实快照（`now` / `instance` / 用到的 `prev_task`、
  `node()` 取值）——事后解释公式为什么是这个结果，证据齐的。

无条件边不进 `edges`（恒真的东西不需要审计）；无条件路由也落行（`selected`
就是那一条）。审计行在生产事故排查里是「流程为什么走错」的第一手证据。

## 轨迹与归档

```python
engine.history.trace_instance(iid)     # 令牌 + 已办 + 事件按时间合并的一页轨迹
engine.history.archive_instance(iid)   # 整实例归档导出（dict）
```

`trace_instance` 返回按时间排序的 `TraceEntry` 列表（`kind` / `title` / `time`），
令牌推进、任务办结、事件发生 interleaved——做「流转时间线」页面直接用它：

```text
轨迹: token #1 start → finished
轨迹: token #2 route → finished
轨迹: token #3 big → finished
...（共 17 条）
```

其他只读查询都在 `engine.history` 上：`events` / `task_history` / `instances` /
`tokens` / `paged_tasks`（分页待办）——速查见 `docs/API.md`。

## Job 与外部 worker

引擎里有两类 Job，别混淆：

1. **定时 Job**（超时 / 时限 / 等待到时）：引擎内部排程，`engine.jobs.tick()` 驱动
   （第 6 章超时已讲）；
2. **worker Job**：给**你自己的外部 worker** 派的活——「发邮件、调外部接口」这类
   不想阻塞流程、又想有重试与留痕的旁路任务。

worker Job 的完整生命周期（实测）：

```python
# 1. 建任务（挂在某个实例上）
job_id = engine.worker_jobs.create_worker_job(
    job_type="send_email", instance_id=iid, payload={"to": "u_z"},
)

# 2. worker 领活（租约制：CAS 原子认领，多 worker 抢不重）
claims = engine.worker_jobs.activate_jobs(["send_email"], worker_id="worker-1")

# 3a. 干成了 -> 完成
engine.worker_jobs.complete_job(job_id, worker_id="worker-1", output={"sent": True})

# 3b. 干砸了 -> 失败（指数退避后可再领；超过 max_retries 进 dead_letter）
engine.worker_jobs.fail_job(job_id, worker_id="worker-1", error="SMTP 超时")
```

语义要点：

- `activate_jobs` 是**租约领活**：领走后有可见性超时（默认 5 分钟），worker 崩了
  租约到期自动放回池子——多副本部署无需分布式锁；
- `fail_job` 记失败并按指数退避安排重试；超过 `max_retries`（默认 3）落
  **dead_letter** 并出故障单。实测 `max_retries=1` 时第一次失败直接进
  dead_letter；
- 退避窗口内别的 worker 领不到（实测领 0 条）。

典型接线：service_task 的 handler 里 `create_worker_job` 派活即返回，流程用
wait 节点等外部回执（第 8 章）——「派活出去、等消息回来」的完整异步闭环。

## 本章小结

- 事件=拉取式广播：`OutboxConsumer` + `IdempotentSink`，至少一次 + 幂等去重。
- 每次路由一行审计：条件边的公式与结果、选中边、事实快照。
- `trace_instance` 给时间线页面；只读查询全家桶在 `engine.history`。
- worker Job 租约领活 + 指数退避 + dead_letter，给外部系统派活带完整留痕。

下一章讲扩展：注册 handler、加自定义节点类型、组织目录的高级形态。
