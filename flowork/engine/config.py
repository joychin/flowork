"""引擎配置。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from flowork.events.domain import DomainEvent
from flowork.participants.functions import ParticipantFunctions
from flowork.participants.org_directory import OrgDirectory
from flowork.runtime.registry import NodeRegistry, create_registry

HistoryLevel = str  # none / activity / audit / full（M6 起生效，当前默认 full）


def _system_now() -> datetime:
    return datetime.now()


def _noop_notifier(event: str, payload: dict[str, object]) -> None:
    return None


@dataclass
class EngineConfig:
    """引擎配置（全部有默认值，内核可裸跑）。

    Attributes:
        db_url: SQLAlchemy 连接串，默认内存 SQLite（测试友好）。
        node_registry: 节点注册表；默认内置类型，可传入自定义注册表。
        history_level: 历史事件写入粒度（M6 生效）。
        max_node_loops: 单节点最大完成次数（死循环守卫）。
        lock_retries: 乐观锁冲突重试次数。
    """

    db_url: str = "sqlite:///:memory:"
    node_registry: NodeRegistry = field(default_factory=create_registry)
    history_level: HistoryLevel = "full"
    max_node_loops: int = 50
    lock_retries: int = 3
    now: Callable[[], datetime] = _system_now
    notifier: Callable[[str, dict[str, object]], None] = _noop_notifier
    db_file: str | Path | None = None  # CLI 便利字段：设置后覆盖 db_url
    org: OrgDirectory | None = None  # M4：组织目录（缺省走简化参与者解析）
    participant_functions: ParticipantFunctions | None = None


def resolve_db_url(config: EngineConfig) -> str:
    if config.db_file is not None:
        return f"sqlite:///{Path(config.db_file)}"
    return config.db_url


def should_record_event(config: EngineConfig, event: DomainEvent | str) -> bool:
    """历史分级过滤（M6 完整生效；当前 full/audit 全记）。"""
    name = event.value if isinstance(event, DomainEvent) else str(event)
    level = config.history_level
    if level == "none":
        return False
    return not (level == "activity" and not name.startswith(("process_", "node_")))
