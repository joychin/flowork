"""统一时钟：全库时间戳一律 naive UTC。

多副本/跨机器部署时，本地时间不可比；所有落库时间戳（ORM 默认值、
EngineConfig.now、调度判定）统一取 naive UTC，副本间以 NTP 对齐。
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["utc_now"]


def utc_now() -> datetime:
    """当前 UTC 时间（无 tzinfo，与 SQLite/PG 的 naive DateTime 列一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)
