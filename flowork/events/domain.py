"""领域事件类型（过去时命名）。

事件全集＝本枚举（收编批起「枚举即全集」，tests/test_event_surface.py 静态守卫
引擎代码的字符串实参必须 ∈ 值集）。事件随 Command 管线缓冲、命令尾由
``ops._flush_history`` 同事务写入 ``wf_event_outbox``；``ctx.emit`` 端口对宿主
仍是开放事件名（自定义业务事件合法），本枚举只承诺「引擎自发事件」全集。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["DomainEvent"]


class DomainEvent(StrEnum):
    """引擎发出的领域事件全集（值即事件名）。"""

    PROCESS_STARTED = "process_started"
    PROCESS_COMPLETED = "process_completed"
    PROCESS_CANCELLED = "process_cancelled"
    NODE_ENTERED = "node_entered"
    NODE_EXITED = "node_exited"
    NODE_CANCELLED = "node_cancelled"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    TASK_REJECTED = "task_rejected"
    TASK_CLAIMED = "task_claimed"
    TASK_TRANSFERRED = "task_transferred"
    INCIDENT_RAISED = "incident_raised"
    JOB_DEAD_LETTERED = "job_dead_lettered"
    # —— v2 定义生命周期（spec §6.4；幂等部署不发 definition_deployed）——
    DEFINITION_DEPLOYED = "definition_deployed"
    DEFINITION_RETIRED = "definition_retired"
    DEFINITION_PURGED = "definition_purged"
    # —— v2 消息/信号（spec §7）——
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_CORRELATED = "message_correlated"
    # —— v2 实例生命周期（spec §7/§10）——
    PROCESS_PAUSED = "process_paused"
    PROCESS_RESUMED = "process_resumed"
    PROCESS_TERMINATED = "process_terminated"
    # 挂起/恢复对称：挂起侧此前只有
    # incident_raised（故障单层），实例生命周期层无冻结/解冻信号
    PROCESS_SUSPENDED = "process_suspended"
    INSTANCE_DEADLINE_REACHED = "instance_deadline_reached"  # （spec §10）
    TIMER_FIRED = "timer_fired"  # spec §7 定时器行承诺事件；等待节点到时首发
    TASK_ESCALATED = "task_escalated"
    # —— 变量 I/O（spec §10：仅外部更新命令发射；引擎内部写变量不发——防刷屏）——
    VARIABLES_UPDATED = "variables_updated"
    # —— 收编：此前以裸字符串实发、不在本枚举的 12 个事件 +
    # incident_resolved 补发。订阅方按 DomainEvent 编程自此不再漏收。
    # 注意：ctx.emit 端口对宿主仍是开放事件名（tests/pilot 的
    # payment_executed 即自定义业务事件），本枚举只承诺「引擎自发事件」全集。
    SUB_PROCESS_STARTED = "sub_process_started"
    # 父侧子完成信号：修前只有 started——
    # 子实例完结时父侧零事件（挂起期唤醒落空更无痕）
    SUB_PROCESS_COMPLETED = "sub_process_completed"
    TASK_ASSIST_CREATED = "task_assist_created"
    TASK_ASSIST_CANCELLED = "task_assist_cancelled"
    TASK_CONSULT_CREATED = "task_consult_created"
    TASK_CONSULT_FINISHED = "task_consult_finished"  # spec §5.1 承诺
    TASK_COUNTERSIGNER_ADDED = "task_countersigner_added"
    TASK_WITHDRAWN = "task_withdrawn"
    TIMEOUT_WARNED = "timeout_warned"
    CC_CREATED = "cc_created"
    # 管理员动作层（两级事件语义）：
    # 状态层（node_entered/cancelled、process_completed 等）回答「流程现在是
    # 什么样」，动作层（*_by_admin）回答「谁对它动了手术」——同一操作两层
    # 各自完整，订阅方按需取层；终态信号契约始终是状态层三事件
    # （process_completed/cancelled/terminated），*_by_admin 不是终态替代。
    NODE_ACTIVATED_BY_ADMIN = "node_activated_by_admin"
    NODE_CANCELLED_BY_ADMIN = "node_cancelled_by_admin"
    TOKEN_MOVED_BY_ADMIN = "token_moved_by_admin"
    PROCESS_FINISHED_BY_ADMIN = "process_finished_by_admin"
    INCIDENT_RESOLVED = "incident_resolved"  # spec §10 承诺
    # —— 取消族/补齐信号：cancel_token 统一咽喉（逐任务发 task_cancelled、
    # 节点级 node_cancelled 带 by=原因）；auto_pass 四处归档发 task_auto_passed；
    # 认领/协办完成对称补齐。node_failed 已删（全仓零发射，节点故障的真实
    # 信号是 incident_raised+挂起）。
    TASK_CANCELLED = "task_cancelled"
    TASK_AUTO_PASSED = "task_auto_passed"
    TASK_UNCLAIMED = "task_unclaimed"
    TASK_ASSIST_FINISHED = "task_assist_finished"
