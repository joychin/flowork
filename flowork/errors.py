"""公共异常体系。

层级：FloworkError →（DefinitionError | ConditionError | RuntimeError 家族，后者 M1 起）。
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
    """条件求值错误（类型不匹配、路径非法等）。"""


class VariableNotDefined(ConditionError):
    """条件引用了未定义的变量。"""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"条件引用了未定义的变量：{path!r}")


class FormulaNotSupported(ConditionError):
    """公式条件在当前版本尚未启用（M2 提供沙箱求值）。"""
