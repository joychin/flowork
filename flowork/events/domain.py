"""领域事件类型（过去时命名）。

M0 仅定义类型；M1 起随 Command 管线写入 ``wf_event_outbox``（HistoryInterceptor），
后续消息中心 / UI / MCP 订阅方统一消费事件表。
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
    NODE_FAILED = "node_failed"
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
