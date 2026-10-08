"""表达式词汇目录（expression-dataface §5）：一份目录，三消费方。

消费方：① ``definitions.validator_conditions``（保留词与位点白名单表驱动，
规则不再硬编码 frozenset）；② 设计器变量树（分组下拉）；③ 目录下发
（REST /v2 批登记端点；``FunctionSignature.label`` 为函数条目同一目的）。

目录即版本承诺（expression-dataface §5）：加字段=小版本；改语义/删条目=
破坏性变更（R2 级，major+1）。本模块纯数据、零依赖（expr 叶子模块防环）。

位点词汇（与 ``validator.iter_expression_positions`` 的 kind 一致）：
condition（连线条件）/ subjects（取人 assignee·assignees·recipients）/
reassign（超时改派）/ exit（会签出口）/ mapping_input·mapping_output（子流程映射）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ALL_POSITIONS",
    "IdentifierEntry",
    "identifier_entries",
    "positions_of",
    "reserved_identifiers",
]

ALL_POSITIONS = frozenset(
    {"condition", "subjects", "reassign", "exit", "mapping_input", "mapping_output"}
)


@dataclass(frozen=True)
class IdentifierEntry:
    """一个注入标识符：名称/类型词/中文标签/可用位点/备注。"""

    name: str
    type: str
    label: str
    positions: frozenset[str]
    note: str = ""


# 判断面词汇 v1（expression-dataface §2）：instance.*/prev_task/node() 随 F2 入册。
IDENTIFIER_CATALOG: tuple[IdentifierEntry, ...] = (
    IdentifierEntry(
        name="initiator",
        type="subject",
        label="发起人",
        positions=ALL_POSITIONS,
        note="保留标识符；显式声明同名变量时以声明为准",
    ),
    IdentifierEntry(
        name="now",
        type="timestamp",
        label="求值时刻",
        positions=ALL_POSITIONS,
        note="引擎经统一时钟注入（naive UTC）；一次求值钉一个时刻",
    ),
    IdentifierEntry(
        name="task",
        type="map",
        label="当前任务快照",
        positions=frozenset({"reassign", "exit"}),
        note="同名不同方言：改派位=id/assignee/outcome；出口位=三计数",
    ),
)


def identifier_entries() -> tuple[IdentifierEntry, ...]:
    """目录全量（不可变元组，消费方免拷贝）。"""
    return IDENTIFIER_CATALOG


def reserved_identifiers() -> frozenset[str]:
    """保留标识符全集（可引用、不可声明为流程变量）。"""
    return frozenset(entry.name for entry in IDENTIFIER_CATALOG)


def positions_of(name: str) -> frozenset[str]:
    """某保留标识符的可用位点；非保留词返回空集。"""
    for entry in IDENTIFIER_CATALOG:
        if entry.name == name:
            return entry.positions
    return frozenset()
