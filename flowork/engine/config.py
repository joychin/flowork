"""引擎配置。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from flowork.clock import utc_now
from flowork.events.domain import DomainEvent
from flowork.participants.org_directory import OrgDirectoryLike
from flowork.persistence.db import url_from_value
from flowork.runtime.registry import NodeRegistry, create_registry
from flowork.spi.ports import ExpressionFunctions

HistoryLevel = str  # none / activity / audit / full（M6 起生效，当前默认 full）


def _system_now() -> datetime:
    return utc_now()


@dataclass
class EngineConfig:
    """引擎配置（全部有默认值，内核可裸跑）。

    Attributes:
        db_url: SQLAlchemy 连接串，默认内存 SQLite（测试友好）。
        node_registry: 节点注册表；默认内置类型，可传入自定义注册表。
        history_level: 历史事件写入粒度（M6 生效）。
        max_node_loops: 单节点最大完成次数（死循环守卫）。
        max_participants: 单节点参与者规模上限（会签名单/传阅接收人；加签后名单同守）。
            缺省 None＝不设防（内核不替宿主选数值）；显式配置后超限＝拒绝
            （解析位挂起+故障单，调用方位请求拒绝），绝不静默截断。
        lock_retries: 乐观锁冲突重试次数。
    """

    db_url: str = "sqlite:///:memory:"
    node_registry: NodeRegistry = field(default_factory=create_registry)
    history_level: HistoryLevel = "full"
    max_node_loops: int = 50
    max_participants: int | None = None
    # v2.3 第三批 A11：部署期 L3/L4（引用完整性/目录实体存在性）默认 WARNING；
    # True 时升 ERROR 拦发布（fail-fast 部署）。
    strict_deploy_validation: bool = False
    lock_retries: int = 3
    now: Callable[[], datetime] = _system_now
    db_file: str | Path | None = None  # CLI 便利字段：设置后覆盖 db_url（路径或完整 URL）
    org: OrgDirectoryLike | None = None  # 组织目录（委托替换/to_admins 兜底/取人词汇数据源）
    expression_functions: ExpressionFunctions | None = None  # CEL 词汇（全表达式位，spec §4.3/§9）


def resolve_db_url(config: EngineConfig) -> str:
    """db_file 覆盖 db_url：值是完整连接串时原样透传，否则按 SQLite 文件路径包装。

    ``FLOWORK_DB=postgresql+psycopg://...`` 与 ``--db postgresql+psycopg://...``
    因此可直接启动 CLI/web（spec §14 支持矩阵）。归一规则见
    ``persistence.db.url_from_value``（与 migrations/env.py 单源）。
    """
    if config.db_file is not None:
        return url_from_value(config.db_file)
    return config.db_url


def should_record_event(config: EngineConfig, event: DomainEvent | str) -> bool:
    """历史分级过滤（M6 完整生效；当前 full/audit 全记）。

    activity 档白名单＝process_/node_/incident_ 前缀（事件 review C2，2026-10-13：
    incidents 是故障告警信号，不再被该档静默吞掉；task_*/message_*/definition_*
    等仍只在 audit/full 档记录——选 activity 的宿主拿不到任务级事件）。
    """
    name = event.value if isinstance(event, DomainEvent) else str(event)
    level = config.history_level
    if level == "none":
        return False
    return not (level == "activity" and not name.startswith(("process_", "node_", "incident_")))
