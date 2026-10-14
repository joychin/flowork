"""随机结构发射器：_Block 清单 → FlowBuilder 定义（与 random_gen 决策层分居）。

持有结构共享常量（条件臂阈值）与全部 FlowBuilder 发射逻辑；决策（块词汇、
计划、驳回注入）在 random_gen.py。发射红线（validator 实证）：
取人字面量带引号；approve_exit 写在 multi_instance 内；wait 无条件单出边；
MI call subjects 非空、completion="all"、input 键含 subject 投影。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.node import MultiInstance
from tests.suite.random_blocks import ARMS, _Block, _ChildShape


def emit_definitions(
    blocks: list[_Block], child_shape: _ChildShape | None, seed: int, n: int
) -> dict[str, ProcessDefinition]:
    """主定义（+子定义，若有 call 族块）——每块变量声明随发射收集。"""
    definitions: dict[str, ProcessDefinition] = {"rand_main": _emit_main(blocks, seed, n)}
    if child_shape is not None:
        definitions["rand_child"] = emit_child(child_shape, seed)
    return definitions


def _emit_main(blocks: list[_Block], seed: int, n: int) -> ProcessDefinition:
    builder = FlowBuilder("rand_main", f"随机流程 {seed}").start("start")
    variables = [ProcessVariable(name="n", type="number", label="档位", sample=n)]
    for block in blocks:
        _emit_block(builder, block)
        if block.kind == "call":
            variables.append(
                ProcessVariable(name="echo_back", type="number", label="回传", sample=0)
            )
        if block.kind == "mi_call":
            variables.append(
                ProcessVariable(name="workers", type="list", label="子实例名单", sample=[])
            )
    builder.end("the_end", "结束")
    _wire(builder, blocks)
    return builder.build().model_copy(update={"variables": variables})


def _emit_block(builder: FlowBuilder, block: _Block) -> None:
    if block.kind == "task":
        extra: dict[str, object] = {"reject_target": "start"} if block.rejectable else {}
        builder.user_task(block.code, block.name, assignee=f"'{block.actor}'", **extra)
    elif block.kind == "branch":
        builder.branch(block.code, block.name, route_mode=block.branch_mode)
        for arm_code, arm_kind, arm_actor in zip(
            block.arm_codes, block.arm_kinds, block.arm_actors, strict=True
        ):
            if arm_kind == "cc":
                builder.cc_task(arm_code, f"{block.name}传阅臂", recipients=[f"'{arm_actor}'"])
            else:
                builder.user_task(arm_code, f"{block.name}臂", assignee=f"'{arm_actor}'")
        builder.user_task(
            block.default_code, f"{block.name}默认臂", assignee=f"'{block.default_actor}'"
        )
        sink: dict[str, object] = (
            {"advance_policy": "all"} if block.sink_all else {}  # plain=any→迟到到达吸收
        )
        builder.user_task(
            block.join_code, f"{block.name}汇聚签", assignee=f"'{block.join_actor}'", **sink
        )
    elif block.kind == "cc":
        builder.cc_task(block.code, block.name, recipients=[f"'{block.actor}'"])
    elif block.kind == "service":
        builder.service_task(block.code, block.name, action="echo_paid")
    elif block.kind == "countersign":
        builder.user_task(
            block.code,
            block.name,
            assignees=[f"'{member}'" for member in block.members],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit=(f"task.approve_count * 100 >= task.item_count * {block.ratio}"),
            ),
        )
    elif block.kind == "call":
        builder.call_activity(
            block.code,
            block.name,
            process_code="rand_child",
            input_mappings={"child_note": "n"},
            output_mappings={"echo_back": "child_note"},
        )
    elif block.kind == "mi_call":
        builder.call_activity(
            block.code,
            block.name,
            process_code="rand_child",
            multi_instance={
                "subjects": [f"'{subject}'" for subject in block.subjects],
                "completion": "all",
            },
            input_mappings={"child_note": "n", "worker": "subject"},
            output_mappings={"workers": "worker"},
        )
    elif block.kind == "wait":
        if block.wait_mode == "duration":
            builder.wait(block.code, block.name, timeout={"after_minutes": block.after_minutes})
        else:
            builder.wait(block.code, block.name, message={"name": block.message_name})
    else:
        raise AssertionError(f"未知块类型：{block.kind}")


def _wire(builder: FlowBuilder, blocks: list[_Block]) -> None:
    previous: list[str] = ["start"]
    for block in blocks:
        if block.kind == "branch":
            for source in previous:
                builder.flow(source, block.code)
            builder.flow(block.code, block.arm_codes[0], condition=f"n >= {ARMS[0][0]}")
            builder.flow(block.code, block.arm_codes[1], condition=f"n >= {ARMS[1][0]}")
            builder.flow(block.code, block.default_code, default=True)
            for arm in [*block.arm_codes, block.default_code]:
                builder.flow(arm, block.join_code)
            previous = [block.join_code]
        else:
            for source in previous:
                builder.connect(source, block.code)
            previous = [block.code]
    for source in previous:
        builder.connect(source, "the_end")


def emit_child(shape: _ChildShape, seed: int) -> ProcessDefinition:
    """子定义：平铺 pick 或含 first_match 分流（嵌套结构空间翻倍）。"""
    variables = [
        ProcessVariable(name="child_note", type="number", label="入参", sample=0),
        ProcessVariable(name="worker", type="string", label="MI 迭代人", sample=""),
    ]
    builder = FlowBuilder("rand_child", f"随机子流程 {seed}").start()
    if not shape.branch:
        builder.user_task("pick", "子流程办理", assignee=f"'{shape.actor}'")
        builder.end("child_end", "子结束")
        builder.flow("start", "pick")
        builder.flow("pick", "child_end")
        return builder.build().model_copy(update={"variables": variables})
    builder.branch("c_gate", "子分流", route_mode=shape.branch_mode)
    for code, actor in zip(shape.arm_codes, shape.arm_actors, strict=True):
        builder.user_task(code, "子臂", assignee=f"'{actor}'")
    builder.user_task(shape.default_code, "子默认臂", assignee=f"'{shape.default_actor}'")
    builder.user_task(
        shape.join_code, "子汇聚签", assignee=f"'{shape.join_actor}'", advance_policy="all"
    )
    builder.end("child_end", "子结束")
    builder.flow("start", "c_gate")
    builder.flow("c_gate", shape.arm_codes[0], condition=f"child_note >= {ARMS[0][0]}")
    builder.flow("c_gate", shape.arm_codes[1], condition=f"child_note >= {ARMS[1][0]}")
    builder.flow("c_gate", shape.default_code, default=True)
    for arm in [*shape.arm_codes, shape.default_code]:
        builder.flow(arm, shape.join_code)
    builder.flow(shape.join_code, "child_end")
    return builder.build().model_copy(update={"variables": variables})
