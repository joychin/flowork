"""input 契约三边界校验（kernel-v2 spec §3.2/P0-6）。

定义顶层 ``input``＝JSON Schema（2020-12）强制契约：发起实例、人工任务提交
output、节点输出映射，三处边界按 schema 校验，不合格**直接拒绝**（报错不入库，
业务失败不产生故障单，spec §8）。None＝无契约，零约束（存量流程不受影响）。
"""

from __future__ import annotations

from typing import Any

from flowork.errors import FloworkError

__all__ = ["InputContractError", "validate_input_contract"]


class InputContractError(FloworkError):
    """input 契约校验失败（边界拒收，spec §8 业务失败——不产生故障单）。"""


def validate_input_contract(
    schema: dict[str, Any] | None, data: dict[str, Any], *, boundary: str
) -> None:
    """按契约校验变量集；schema 为 None 直接通过。

    Args:
        schema: 定义顶层 input（JSON Schema 2020-12）。
        data: 待校验的实例变量全集（边界处合并后的状态）。
        boundary: 边界名（start / task_output / node_output），进错误消息定位。
    """
    if not schema:
        return
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)  # 非法 schema 本身是定义错误
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.path) or "<root>"
        raise InputContractError(
            f"input 契约校验失败（边界={boundary}，位置={location}）：{first.message}"
        )
