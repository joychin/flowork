"""运行时状态枚举与生命周期契约。

契约（《节点抽象与扩展设计》§4，全引擎唯一状态机）::

    token 到达
       │
       ▼
    ENTERED ──on_enter()──▶ RUNNING ──try_complete()==True──▶ on_exit() ──▶ COMPLETED ──▶ 路由求值
                             │   ▲
                             │   └── 未满足：保持 RUNNING（等任务/等信号/等定时）
                             ├─ 并行取消/撤回/实例取消 ──on_cancel()──▶ CANCELLED
                             └─ 任何环节异常 ──────────on_fail()──▶ FAILED → Incident

硬不变量：钩子/行为只可观察与写变量，不得改变路由。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "FAILED_INSTANCE_STATES",
    "FROZEN_INSTANCE_STATES",
    "TERMINAL_INSTANCE_STATES",
    "UNSETTLED_INSTANCE_STATES",
    "InstanceState",
    "TokenState",
]


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


# 终态：已结算（不可再办/不可写变量；子实例结算判据与变量写门槛共用此集合）
TERMINAL_INSTANCE_STATES = frozenset(
    state.value
    for state in (
        InstanceState.COMPLETED,
        InstanceState.TERMINATED,
        InstanceState.CANCELLED,
        InstanceState.FAILED,
    )
)

# 冻结态：暂停/挂起（任务全动作被拦、timer/deadline Job 推迟不消费）
FROZEN_INSTANCE_STATES = frozenset({InstanceState.PAUSED.value, InstanceState.SUSPENDED.value})

# 未结算态：终态之前的一切（可接受终态跃迁；子实例「未结算」判据同此集合）
UNSETTLED_INSTANCE_STATES = frozenset(
    {InstanceState.RUNNING.value, InstanceState.PAUSED.value, InstanceState.SUSPENDED.value}
)

# 非正常结算（terminated/cancelled/failed）：多实例三计数 child_failed 的计入口径
FAILED_INSTANCE_STATES = TERMINAL_INSTANCE_STATES - {InstanceState.COMPLETED.value}
