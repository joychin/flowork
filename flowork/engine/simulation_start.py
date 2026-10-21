"""模拟启动面：只建 simulated 实例不驱动（simulate 与场景共用的启动路径）。

拆出 runtime_service 守 300 行红线：启动本体（草稿直通、桩注册先于启动链、
隔离语义、instance_id 预生成）与驱动（simulation_runner）分居两文件。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["start_simulation"]


def start_simulation(
    engine: ProcessEngine,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    start_user: str = "simulator",
    major: int | None = None,
    revision: int | None = None,
    spec: dict[str, Any] | None = None,
    controls: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """只启动不驱动：返回（模拟实例 id，解析后的控制面）。

    场景运行器需要自己掌控驱动节奏（脚本按序推进），与 simulate
    共用同一启动路径（草稿直通、桩注册先于启动链、隔离语义全同）。
    """
    from flowork.engine.simulation_controls import parse_controls, register_simulation_stubs

    parsed = parse_controls(controls)
    # 先定实例 id：桩注册必须先于启动链（服务节点在启动命令内即会到访）
    instance_id = uuid.uuid4().hex
    if parsed.stubs:
        register_simulation_stubs(engine, instance_id, parsed.stubs)

    def _command(ctx: CommandContext) -> str:
        return engine.runtime._start(
            ctx,
            code,
            start_user,
            variables,
            None,
            None,
            None,
            major,
            revision,
            None,
            "reject",
            simulated=True,
            spec=spec,
            instance_id=instance_id,
        )

    engine.execute(_command)
    return instance_id, parsed
