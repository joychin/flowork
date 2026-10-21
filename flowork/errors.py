"""公共异常体系。

层级：FloworkError →（DefinitionError | ConditionError | RuntimeError 家族）。
设计时错误携带定位信息（节点/连线编码），便于校验器与 CLI 汇报。
"""

from __future__ import annotations


class FloworkError(Exception):
    """flowork 所有异常的基类。"""


class DefinitionError(FloworkError):
    """流程定义相关错误（加载、解析、校验）。"""


class UnknownNodeKind(DefinitionError):
    """模板引用了未注册的节点 kind。"""

    def __init__(self, kind: str, known_kinds: tuple[str, ...] | None = None) -> None:
        self.kind = kind
        hint = f"，已注册：{', '.join(known_kinds)}" if known_kinds else ""
        super().__init__(f"未注册的节点类型 kind={kind!r}{hint}")


class UnknownHandler(FloworkError):
    """HandlerRegistry 中不存在该处理器名。"""

    def __init__(self, handler: str, known_handlers: tuple[str, ...] | None = None) -> None:
        self.handler = handler
        hint = f"，已注册：{', '.join(known_handlers)}" if known_handlers else ""
        super().__init__(f"未注册的处理器 handler={handler!r}{hint}")


class ConditionError(FloworkError):
    """条件求值错误（CEL 公式的语法/护栏/求值错，见 expr.cel.FormulaError）。"""


class NotFoundError(FloworkError):
    """目标对象不存在。"""


class DefinitionNotPublishedError(NotFoundError):
    """流程定义未发布（或版本不存在）。"""


class InstanceNotFoundError(NotFoundError):
    """流程实例不存在。"""


class TaskNotAvailableError(FloworkError):
    """任务不存在、已完成或不属于当前状态。"""


class DefinitionConflictError(FloworkError):
    """并发部署撞 (code, major, revision) 唯一索引——重试即幂等（内容相同返回
    identical_to verdict）。REST 映射 409。来源：2026-10-21 内核 review P2
    （修前裸 IntegrityError → 未映射 500）。"""


class DuplicateStartError(FloworkError):
    """启动幂等键冲突（reuse=reject 撞已有实例）。"""


class DefinitionRetiredError(DefinitionNotPublishedError):
    """定义已 retired，不接新实例。"""
