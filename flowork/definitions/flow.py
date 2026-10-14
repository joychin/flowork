"""连线（SequenceFlow）与路由条件模型。

条件单轨化（spec §4.1）：``condition`` 直接存 CEL 公式字符串
（expr/cel.py 求值；原结构化条件组 groups 已退役——零兼容，存量已迁移）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from flowork.definitions.node import HandlerRef

__all__ = [
    "MAX_FORMULA_LENGTH",
    "SequenceFlow",
]

MAX_FORMULA_LENGTH = 2048  # spec §4.3：CEL 公式 2KB 上限


class SequenceFlow(BaseModel):
    """流程图的边：source → target，可带 CEL 公式条件、默认标记与 on_take 钩子。"""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    name: str = ""
    is_default: bool = False
    condition: str | None = None
    on_take: HandlerRef | None = None

    def flow_code(self) -> str:
        """连线编码：显式无编码时按 source→target 推导。"""
        return f"{self.source}->{self.target}"
