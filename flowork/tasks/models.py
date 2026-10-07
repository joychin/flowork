"""任务/实例/事件的对外 DTO（工具友好：全 typed，供四服务与未来 MCP 工具直接使用）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskInfo(BaseModel):
    """一条待办任务。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    instance_id: str
    token_id: str
    activity_code: str
    name: str
    assignee: str
    task_type: str = "approval"
    state: str = "ready"
    context_payload: dict[str, Any] = {}
    created_at: datetime | None = None


class TaskHistoryInfo(TaskInfo):
    """一条已办记录（append-only）。id 为历史行 ID，task_id 为原任务 ID。"""

    task_id: str = ""
    outcome: str = ""
    comment: str = ""
    acted_by: str = ""
    acted_as_agent: bool = False
    cause_token_id: str | None = None


class InstanceInfo(BaseModel):
    """流程实例概要。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    definition_code: str
    definition_major: int
    definition_revision: int
    business_key: str | None = None
    state: str
    start_user: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_id: str | None = None


class EventInfo(BaseModel):
    """一条领域事件（outbox）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    event_name: str
    instance_id: str | None = None
    payload: dict[str, Any] = {}
    occurred_at: datetime | None = None


class TokenInfo(BaseModel):
    """一条令牌（流转轨迹的骨架）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    token_no: int
    activity_code: str
    source_activity_code: str | None = None
    state: str
    item_count: int = 0
    approve_count: int = 0
    disapprove_count: int = 0
    reject_flag: bool = False
    approval_exit: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
