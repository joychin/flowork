"""EngineConfig 配置校验（来源：全仓review-2026-11-23 §2.12/§2.13）。

拼错的 history_level 曾被静默按全量记录；带 tzinfo 的时钟曾让首条命令
在事件 id 计算深处抛裸 TypeError（离因很远）。两者都在构造时响。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from flowork.api import EngineConfig, FloworkError


def test_history_level_typo_is_rejected_at_construction():
    with pytest.raises(FloworkError, match="history_level"):
        EngineConfig(history_level="activty")
    for level in ("none", "activity", "audit", "full"):
        assert EngineConfig(history_level=level).history_level == level


def test_tz_aware_clock_is_rejected_at_construction():
    aware = lambda: datetime.now(timezone(timedelta(hours=8)))  # noqa: E731
    with pytest.raises(FloworkError, match="naive UTC"):
        EngineConfig(now=aware)
    # naive UTC（含非本地时区的 UTC 转换后）合法
    assert EngineConfig(now=lambda: datetime.now(UTC).replace(tzinfo=None)) is not None


def test_pool_parameters_passed_to_non_memory_engine(tmp_path):
    """pool 参数只对非内存库生效（内存库恒单连接，构造成功即可）。"""
    config = EngineConfig(db_file=tmp_path / "pool.db", pool_size=3, max_overflow=7)
    assert config.pool_size == 3 and config.max_overflow == 7
    from flowork.api import ProcessEngine

    engine = ProcessEngine(config)
    assert engine._db_engine.pool.size() == 3
    engine.close()
