"""变量索引同步（kernel-v2 spec §14/P1-5）。

searchable 变量（仅 string/number/boolean，validator 把关）在**命令边界**与变量
写入同事务重写实例的索引行——命令管线内任何变量变更（发起/任务提交/节点输出/
消息关联/钩子直写）都被覆盖，保证索引与变量同事务一致（验收标准）。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from flowork.persistence.orm import VariableIndexRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["searchable_names_of", "sync_variable_index"]


def searchable_names_of(instance: ProcessInstanceRow) -> list[str]:
    """实例钉住的定义中标记 searchable 的变量名（仅 string/number/boolean）。"""
    try:
        snapshot = json.loads(instance.definition_snapshot) or {}
    except (ValueError, TypeError):
        return []
    names: list[str] = []
    for variable in snapshot.get("variables") or []:
        if variable.get("searchable") and variable.get("type") in ("string", "number", "boolean"):
            names.append(str(variable.get("name")))
    return names


def sync_variable_index(session: Session, instance: ProcessInstanceRow) -> None:
    """把实例 searchable 变量的当前值整表重写进索引（删旧插新，同事务）。"""
    instance_id = instance.id
    session.execute(delete(VariableIndexRow).where(VariableIndexRow.instance_id == instance_id))
    for name in searchable_names_of(instance):
        value = (instance.variables or {}).get(name)
        if value is None:
            continue
        text_value: str | None = None
        number_value: float | None = None
        if isinstance(value, bool):
            text_value = "true" if value else "false"
        elif isinstance(value, (int, float)):
            number_value = float(value)
        elif isinstance(value, str):
            text_value = value[:500]
        else:
            continue  # object/list 不入索引（C.5）
        session.add(
            VariableIndexRow(
                id=uuid.uuid4().hex,
                instance_id=instance_id,
                name=name,
                value_text=text_value,
                value_num=number_value,
            )
        )


def instances_by_variable(
    session: Session,
    name: str,
    *,
    text: str | None = None,
    number: float | None = None,
    number_lt: float | None = None,
    number_gt: float | None = None,
) -> list[str]:
    """按索引检索实例 ID（等值/数值区间；组合条件为 AND）。"""
    stmt = select(VariableIndexRow.instance_id).where(VariableIndexRow.name == name)
    if text is not None:
        stmt = stmt.where(VariableIndexRow.value_text == text)
    if number is not None:
        stmt = stmt.where(VariableIndexRow.value_num == number)
    if number_lt is not None:
        stmt = stmt.where(VariableIndexRow.value_num < number_lt)
    if number_gt is not None:
        stmt = stmt.where(VariableIndexRow.value_num > number_gt)
    return list(session.scalars(stmt))


_ = Any  # typing 占位
