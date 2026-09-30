"""端口（Protocol）：引擎内核与外部能力的边界。

全部端口在 M1 起接线并有默认实现（静态配置/内存/no-op），保证内核可裸跑、可单测。
签名在 M1~M5 各里程碑按需细化，但“内核只依赖协议”的原则不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = ["Clock", "Notifier", "OrgDirectory", "VariableStore", "WorkCalendar"]


@runtime_checkable
class Clock(Protocol):
    """时钟端口：注入以支持超时场景的确定性测试。"""

    def now(self) -> datetime: ...


@runtime_checkable
class WorkCalendar(Protocol):
    """工作日历端口：自然时间与工作时间的换算（默认实现=自然时间）。"""

    def duration_minutes(self, start: datetime, end: datetime) -> int: ...


@runtime_checkable
class OrgDirectory(Protocol):
    """组织目录端口（M4 完整实现：YAML 静态配置）。"""

    def manager_of(self, user_id: str) -> str | None: ...

    def users_with_role(self, role: str) -> list[str]: ...


@runtime_checkable
class VariableStore(Protocol):
    """变量存取端口：流程实例的 process 级变量读写（默认实现=库内 JSON 列）。"""

    def load(self, instance_id: str) -> dict[str, Any]: ...

    def save(self, instance_id: str, variables: Mapping[str, Any]) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    """通知端口：默认 no-op——事件已落 outbox，消息推送是二期的订阅方。"""

    def notify(self, event: str, payload: Mapping[str, Any]) -> None: ...
