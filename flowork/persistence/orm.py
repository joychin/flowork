"""SQLAlchemy ORM 模型：flowork 运行时表。

M1 建 6 张表：wf_process_definition / wf_process_instance / wf_token /
wf_task / wf_task_history / wf_event_outbox；后续里程碑按需增表
（wf_identity_link、wf_cc_task 于 M3，wf_job、wf_incident 于 M5）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now()


class ProcessDefinitionRow(Base):
    """流程定义（发布后不可变：content 为模板 JSON 快照）。"""

    __tablename__ = "wf_process_definition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/published
    name: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)  # 模板 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (Index("ix_def_code_version", "code", "version", unique=True),)


class ProcessInstanceRow(Base):
    """流程实例。version 为乐观锁列。"""

    __tablename__ = "wf_process_instance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_code: Mapped[str] = mapped_column(String(100))
    definition_version: Mapped[int] = mapped_column(Integer)
    definition_snapshot: Mapped[str] = mapped_column(Text)  # 发起时的模板 JSON（不可变）
    business_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="running")
    start_user: Mapped[str] = mapped_column(String(64))
    variables: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # M5 子流程
    parent_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)  # 乐观锁（ORM version_id）
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __mapper_args__ = {"version_id_col": version}


class TokenRow(Base):
    """令牌：一次节点激活产生一条，状态只前进。"""

    __tablename__ = "wf_token"
    __table_args__ = (Index("ix_token_instance", "instance_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    token_no: Mapped[int] = mapped_column(Integer)  # 实例内自增
    activity_code: Mapped[str] = mapped_column(String(100))
    source_activity_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="unfinished")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    approve_count: Mapped[int] = mapped_column(Integer, default=0)
    disapprove_count: Mapped[int] = mapped_column(Integer, default=0)
    reject_flag: Mapped[bool] = mapped_column(Boolean, default=False)  # 驳回产生
    approval_exit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 手动选人（M3+），MutableDict 追踪原地变更
    manual_participants: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskRow(Base):
    """待办任务（活表）。"""

    __tablename__ = "wf_task"
    __table_args__ = (
        Index("ix_task_instance", "instance_id"),
        Index("ix_task_assignee", "assignee"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    token_id: Mapped[str] = mapped_column(String(36))
    activity_code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="")
    assignee: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(16), default="approval")  # approval/assist(M3)
    state: Mapped[str] = mapped_column(String(16), default="ready")  # ready/completed/cancelled
    # 审批卡片/AI 预审共用载荷
    context_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    root_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 加签链(M3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaskHistoryRow(Base):
    """已办（append-only：只插入，永不修改；驳回复活 = 新任务 + cause_token_id）。"""

    __tablename__ = "wf_task_history"
    __table_args__ = (
        Index("ix_history_instance", "instance_id"),
        Index("ix_history_assignee", "assignee"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36))
    instance_id: Mapped[str] = mapped_column(String(36))
    token_id: Mapped[str] = mapped_column(String(36))
    activity_code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="")
    assignee: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(16), default="approval")
    outcome: Mapped[str] = mapped_column(String(16))  # agree/reject/transfer/auto_pass/withdrawn
    comment: Mapped[str] = mapped_column(Text, default="")
    context_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cause_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acted_by: Mapped[str] = mapped_column(String(64))
    acted_as_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventOutboxRow(Base):
    """领域事件（outbox）：单事务内与业务变更一起落库，后续消息/UI/MCP 统一消费。"""

    __tablename__ = "wf_event_outbox"
    __table_args__ = (Index("ix_event_instance", "instance_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(64))
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CcItemRow(Base):
    """传阅件（M1 建表；已读接口 M3）。"""

    __tablename__ = "wf_cc_task"
    __table_args__ = (Index("ix_cc_instance", "instance_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    activity_code: Mapped[str] = mapped_column(String(100))
    recipient: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200), default="")
    context_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
