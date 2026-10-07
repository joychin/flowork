"""运行时状态枚举与生命周期契约。

契约（《节点抽象与扩展设计》§4，全引擎唯一状态机）::

    token 到达
       │
       ▼
    ENTERED ──on_enter()──▶ RUNNING ──try_complete()==True──▶ on_exit() ──▶ COMPLETED ──▶ 路由求值
                             │   ▲
                             │   └── 未满足：保持 RUNNING（等任务/等信号/等定时）
                             ├─ 并行取消/撤回/实例取消 ──▶ on_cancel() ──▶ CANCELLED
                             └─ 任何环节异常 ──────────▶ on_fail() ──▶ FAILED → Incident

硬不变量：钩子/行为只可观察与写变量，不得改变路由。
"""

from __future__ import annotations

from enum import StrEnum


class TokenState(StrEnum):
    """令牌状态：一次节点激活产生一个令牌，状态只前进不回退。"""

    UNFINISHED = "unfinished"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    FAILED = "failed"


class InstanceState(StrEnum):
    """流程实例状态。"""

    RUNNING = "running"
    PAUSED = "paused"  # v2（spec §7）：人工暂停——timer 冻结、任务不可办，恢复后顺延
    TERMINATED = "terminated"  # v2：终止（管理员强杀，终态；与业务取消 cancelled 区分）
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUSPENDED = "suspended"  # 发生 Incident 后挂起，修复后恢复


class NodeLifecycle(StrEnum):
    """节点生命周期阶段（驱动 on_enter/try_complete/on_exit/on_cancel/on_fail 的调用时机）。"""

    ENTERED = "entered"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
