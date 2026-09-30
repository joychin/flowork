"""连线（SequenceFlow）与路由条件模型。

条件两层表达（FlowCondition）：
- 结构化条件组 groups：组间 OR、组内 AND —— 推荐方式，可校验、可可视化；
- 公式 formula：字符串表达式，M2 起经沙箱求值（asteval，白名单+长度+超时限制）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flowork.definitions.node import HandlerRef

__all__ = [
    "MAX_FORMULA_LENGTH",
    "ConditionItem",
    "ConditionOperator",
    "FlowCondition",
    "SequenceFlow",
]


MAX_FORMULA_LENGTH = 4096


class ConditionOperator(StrEnum):
    """结构化条件操作符白名单。"""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"


class ConditionItem(BaseModel):
    """单个条件：left 为变量路径（点号取子字段），right 为常量（in/not_in 时为列表）。"""

    model_config = ConfigDict(extra="forbid")

    left: str
    operator: ConditionOperator
    right: Any = None


class FlowCondition(BaseModel):
    """路由条件：groups 与 formula 二选一（校验器强制）。"""

    model_config = ConfigDict(extra="forbid")

    groups: list[list[ConditionItem]] = Field(default_factory=list)
    formula: str | None = None


class SequenceFlow(BaseModel):
    """流程图的边：source → target，可带条件、默认标记与 on_take 钩子。"""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    name: str = ""
    is_default: bool = False
    condition: FlowCondition | None = None
    on_take: HandlerRef | None = None

    def flow_code(self) -> str:
        """连线编码：显式无编码时按 source→target 推导。"""
        return f"{self.source}->{self.target}"
