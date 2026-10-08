"""v2 新增表（kernel-v2 spec §14）：设计器草稿/消息缓冲·订阅/变量索引。

与 orm.py 共享同一 Base 元数据（orm.py 尾部再导出，建表入口不变）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from flowork.persistence.orm import Base, _now


class DesignerDraftRow(Base):
    """设计器工作副本（v2 §6.2：编辑态在设计器本地——不进引擎定义生命周期表）。

    seq＝每 code 单调递增的保存序号（设计器显示的 draft_version）；open＝是否
    待发布（发布即消费，seq 保留以延续编号）。
    """

    __tablename__ = "wf_designer_draft"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    open: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MessageBufferRow(Base):
    """消息缓冲区（v2 §7）：先到的消息在此等订阅出现；TTL 过期清理。

    message_id 全局唯一防重（消费端至多收到一次，spec §7）；correlation_key
    为**求值后**的键值（CEL 求值发生在等待节点登记订阅时）。
    """

    __tablename__ = "wf_message_buffer"
    __table_args__ = (
        Index("uq_message_id", "message_id", unique=True),
        Index("ix_message_name_key", "name", "correlation_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    correlation_key: Mapped[str] = mapped_column(String(200), default="")
    message_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MessageSubscriptionRow(Base):
    """消息订阅（v2 §7）：等待方登记 (name, correlation_key)；state open/matched。

    节点离开（完成/取消/实例终止）即关（close_subscriptions）；暂停期间保持。
    """

    __tablename__ = "wf_message_subscription"
    __table_args__ = (Index("ix_msgsub_name_key_state", "name", "correlation_key", "state"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    token_id: Mapped[str] = mapped_column(String(36))
    node_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(64))
    correlation_key: Mapped[str] = mapped_column(String(200), default="")
    state: Mapped[str] = mapped_column(String(16), default="open")
    # 已消费的 message_id（关联时定格）——防重按 message_id 判，与 (name,key) 无关
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class VariableIndexRow(Base):
    """变量索引（v2 §14/P1-5）：searchable 变量值随命令同事务同步，供实例检索。

    string/boolean → value_text；number → value_num（布尔不入数值槽）。
    """

    __tablename__ = "wf_variable_index"
    __table_args__ = (Index("ix_var_instance_name", "instance_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    value_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)


class VariableHistoryRow(Base):
    """变量留痕（变量 I/O 批，spec §14）：全写方逐键改前/改后，追加只写。

    命令尾 diff 产生（值相等跳过）；source=external_update / task_output /
    task_reject / engine（发起·钩子·消息·映射）。old_value=None 表示新建键（birth）。
    归因是命令级的：同一条命令内的全部变化共享 source/acted_by。
    """

    __tablename__ = "wf_variable_history"
    __table_args__ = (Index("ix_varhist_instance", "instance_id", "name", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="engine")
    acted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acted_as_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RouteDecisionRow(Base):
    """路由决策审计（expression-dataface §6/F3）：一次 select_outgoing 一行。

    edges=[{flow,formula,result}]（result ∈ true/false/no_fact 查无跳过）；
    selected=最终激活的 flow_code；snapshot={now,prev_task,node:{code:facts}}
    （求值时刻钉住的取值，重放解释用）。求值失败不落行——故障单消息已含
    公式与错误（spec §8 错误二分）。
    """

    __tablename__ = "wf_route_decision"
    __table_args__ = (Index("ix_route_instance", "instance_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_activity: Mapped[str] = mapped_column(String(100), default="")
    mode: Mapped[str] = mapped_column(String(16), default="all_match")
    edges: Mapped[list[Any]] = mapped_column(JSON, default=list)
    selected: Mapped[list[Any]] = mapped_column(JSON, default=list)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
