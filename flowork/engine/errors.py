"""引擎执行错误族与错误归因。

全部为「技术故障」语义（错误二分）：命令管线捕获后回滚整笔命令、
独立事务记 Incident 并挂起实例。兼容面：kernel 模块对错误族显式再导出
（``__all__``），既有 ``from flowork.engine.kernel import EngineExecutionError``
等函数级导入不受影响。
"""

from __future__ import annotations

from flowork.errors import FloworkError

__all__ = [
    "DeadLoopError",
    "EngineExecutionError",
    "NoParticipantError",
    "ParticipantCountMismatchError",
    "ParticipantLimitError",
    "RejectTargetMissing",
    "RouteError",
    "attribute_node_error",
]


class EngineExecutionError(FloworkError):
    """引擎执行错误（将转为 Incident）。

    ``activity_code``：错误发生时的节点定位——由节点作用域的内核帧统一标注
    （activate / advance_after_task / finish_wait / 驳回出口），
    非节点作用域（Job/消息面）缺省 None；
    ``incident_raised`` 载荷据此携带定位，订阅方不再解析 message 文本。
    """

    activity_code: str | None = None


def attribute_node_error(error: EngineExecutionError, activity_code: str | None) -> None:
    """节点作用域帧的错误归因咽喉：只补不覆盖（内层帧更精确）。"""
    if activity_code and getattr(error, "activity_code", None) is None:
        error.activity_code = activity_code


class RouteError(EngineExecutionError):
    """路由无法确定（如条件分支条件全不满足且无默认边）。"""


class DeadLoopError(EngineExecutionError):
    """死循环守卫触发。"""


class NoParticipantError(EngineExecutionError):
    """审批节点无可用的参与者。"""


class ParticipantLimitError(EngineExecutionError):
    """参与者规模超过上限（EngineConfig.max_participants）——拒绝而非截断。

    解析位（user_task/cc 进入）触发时按技术故障处理：挂起+故障单（可见、可修、
    可 retry）；调用方位（加签）由 TaskService 拦截为请求拒绝，实例不受影响。
    """


class ParticipantCountMismatchError(EngineExecutionError):
    """单人审批节点（未声明 multi_instance）解析出多名参与者——拒绝宽松并行。

    多人必须显式声明会签；解析位＝技术故障（挂起+故障单，首节点整笔回滚）。
    """


class RejectTargetMissing(EngineExecutionError):
    """驳回目标节点缺失（未配置且无来源节点）。"""
