"""实例变量外部读写（变量 I/O 批，kernel-v2 spec §11 / variable-io-api.md）。

RuntimeService 组合件：外部受控改值（三门槛：白名单/类型/终态）、只读值全集、
血缘查询。生效语义：不触发路由重算，只影响后续求值位点；已走分支不倒带。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flowork.definitions.contract import InputContractError
from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.persistence.orm import ProcessInstanceRow, VariableHistoryRow
from flowork.runtime.lifecycle import TERMINAL_INSTANCE_STATES, InstanceState

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["VariableActionsMixin", "VariableChangeInfo"]

# 终态不可写（循 pause 惯例以 InstanceNotFoundError 拒；/v2 批再评估 409 语义）
_TERMINAL_STATES = TERMINAL_INSTANCE_STATES
# 求值上下文保留标识符（expr/context 注入），外部写入一律拒绝
_RESERVED_KEYS = frozenset({"initiator", "task"})
_MAX_UPDATES_BYTES = 256 * 1024


@dataclass(frozen=True)
class VariableChangeInfo:
    """一条变量血缘记录（wf_variable_history 行投影）。"""

    name: str
    old_value: Any
    new_value: Any
    source: str
    acted_by: str | None
    created_at: str


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "number":  # bool 是 int 子类，显式排除
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "list": lambda v: isinstance(v, list),
    }.get(expected, lambda v: True)(value)


def _validate_external_updates(instance: ProcessInstanceRow, updates: dict[str, Any]) -> None:
    """三道保险之二：白名单（声明 ∪ input schema 属性 ∪ 既有键）+ 类型 + 契约 + 尺寸。"""
    from flowork.definitions.contract import input_schema_of, validate_input_contract

    reserved = _RESERVED_KEYS & set(updates)
    if reserved:
        raise InputContractError(f"保留标识符不可写入：{sorted(reserved)}")
    size = len(json.dumps(updates, ensure_ascii=False, default=str).encode())
    if size > _MAX_UPDATES_BYTES:
        raise InputContractError(f"updates 序列化超限（>256KB）：{size} 字节")
    snapshot = json.loads(instance.definition_snapshot) or {}
    declared = {v.get("name"): v.get("type") for v in snapshot.get("variables", [])}
    input_props = set((snapshot.get("input") or {}).get("properties", {}).keys())
    allowed = set(declared) | input_props | set(instance.variables or {})
    unknown = set(updates) - allowed
    if unknown:
        raise InputContractError(f"未声明且实例中不存在的键不可外部写入：{sorted(unknown)}")
    for key, value in updates.items():
        expected = declared.get(key)
        if expected and not _type_matches(expected, value):
            raise InputContractError(f"变量类型不符：{key} 声明为 {expected}")
    schema = input_schema_of(instance)
    if schema:
        merged = dict(instance.variables)
        merged.update(updates)
        validate_input_contract(schema, merged, boundary="external_update")


class VariableActionsMixin:
    """RuntimeService 的变量面（宿主提供 _engine）。"""

    _engine: ProcessEngine

    def update_variables(
        self,
        instance_id: str,
        updates: dict[str, Any],
        *,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """外部受控改值（PATCH 合并写，不提供删键）。返回更新后的变量全集。

        归因与留痕：write_source=external_update——命令尾 diff 落 wf_variable_history
        并发射 variables_updated（引擎内部写方不发事件，spec §10）。
        """

        def _command(ctx: CommandContext) -> dict[str, Any]:
            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")
            if instance.state in _TERMINAL_STATES:
                raise InstanceNotFoundError(
                    f"实例已终态（{instance.state}），不可外部更新变量：{instance_id}"
                )
            if instance.state == InstanceState.PAUSED.value:
                # 暂停＝冻结快照（审计 D2，spec §7 第二十轮）：取证期间不可写；
                # 修数据路径＝resume → 改 → 再 pause
                raise InstanceNotFoundError(
                    f"实例已暂停（冻结快照），不可外部更新变量：{instance_id}"
                    "——先 resume 再改，改完可再 pause"
                )
            _validate_external_updates(instance, updates)
            ctx.touch(instance)
            ctx.write_source = "external_update"
            ctx.write_actor = actor
            ctx.write_reason = reason or None
            instance.variables.update(updates)
            return dict(instance.variables)

        result: dict[str, Any] = self._engine.execute(_command)
        return result

    def variables_of(self, instance_id: str) -> dict[str, Any]:
        """实例变量值全集（只读，含运行期键；不进命令管线）。"""
        with self._engine.session_factory() as session:
            instance = session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")
            return dict(instance.variables or {})

    def variable_history(
        self,
        instance_id: str,
        *,
        name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[VariableChangeInfo]:
        """变量血缘查询（倒序，可按键过滤）：改前/改后 + 写方归因。"""
        from sqlalchemy import select

        def _command(ctx: CommandContext) -> list[VariableChangeInfo]:
            stmt = select(VariableHistoryRow).where(VariableHistoryRow.instance_id == instance_id)
            if name:
                stmt = stmt.where(VariableHistoryRow.name == name)
            rows = ctx.session.scalars(
                stmt.order_by(VariableHistoryRow.created_at.desc()).offset(offset).limit(limit)
            )
            return [
                VariableChangeInfo(
                    name=row.name,
                    old_value=row.old_value,
                    new_value=row.new_value,
                    source=row.source,
                    acted_by=row.acted_by,
                    created_at=str(row.created_at),
                )
                for row in rows
            ]

        result: list[VariableChangeInfo] = self._engine.execute(_command)
        return result
