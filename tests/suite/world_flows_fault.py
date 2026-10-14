"""套件·结构敏感位故障世界（测试套件完善批 4/5）：explode_if handler 挂四处。

变量开关 boom 控制 handler 炸/不炸（修复＝update_variables 置 False，无需换
handler）。四位置：链条中段 / all 汇聚臂上 / 子流程内 / MI 子实例内——同一
故障在不同结构位上的落点与恢复轨迹由 test_fault_matrix.py 矩阵钉死。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable

VARIABLES = [
    ProcessVariable(name="boom", type="boolean", label="引爆开关", sample=True),
    ProcessVariable(name="go", type="boolean", label="臂激活", sample=True),
]


def fragile_chain_definition() -> ProcessDefinition:
    """链条中段：submit → boom(service) → after → end。"""
    return (
        FlowBuilder("fragile_chain", "脆弱·链条")
        .start()
        .user_task("submit", "提交", assignee="'u_dev_wu'")
        .service_task("boom", "外部调用", action="explode_if")
        .user_task("after", "善后", assignee="'u_cto'")
        .end("chain_end", "链条结束")
        .flow("start", "submit")
        .flow("submit", "boom")
        .flow("boom", "after")
        .flow("after", "chain_end")
        .build()
        .model_copy(update={"variables": VARIABLES})
    )


def fragile_arm_definition() -> ProcessDefinition:
    """all 汇聚臂上：boom 臂即时完结 + hold 人工臂 → all 汇聚收口。"""
    return (
        FlowBuilder("fragile_arm", "脆弱·汇聚臂")
        .start()
        .user_task("submit", "提交", assignee="'u_dev_wu'")
        .branch("gate", "分流", route_mode="all_match")
        .service_task("boom_arm", "炸臂", action="explode_if")
        .user_task("hold", "人工臂", assignee="'u_lead_be'")
        .user_task("join", "汇聚签", assignee="'u_ceo'", advance_policy="all")
        .end("arm_end", "臂结束")
        .flow("start", "submit")
        .flow("submit", "gate")
        .flow("gate", "boom_arm", condition="go")
        .flow("gate", "hold", condition="go")
        .flow("boom_arm", "join")
        .flow("hold", "join")
        .flow("join", "arm_end")
        .build()
        .model_copy(update={"variables": VARIABLES})
    )


def fragile_kid_definition() -> ProcessDefinition:
    """子定义（child 与 MI 共用）：start → boom(service) → sign → end。"""
    return (
        FlowBuilder("fragile_kid", "脆弱·子")
        .start()
        .service_task("kid_boom", "子内调用", action="explode_if")
        .user_task("sign", "子签署", assignee="'u_kid'")
        .end("kid_end", "子结束")
        .flow("start", "kid_boom")
        .flow("kid_boom", "sign")
        .flow("sign", "kid_end")
        .build()
        .model_copy(update={"variables": VARIABLES})
    )


def fragile_child_definition() -> ProcessDefinition:
    """子流程内：submit → call(fragile_kid) → after → end。"""
    return (
        FlowBuilder("fragile_child", "脆弱·子流程")
        .start()
        .user_task("submit", "提交", assignee="'u_dev_wu'")
        .call_activity(
            "sub",
            "调子",
            process_code="fragile_kid",
            input_mappings={"boom": "boom", "go": "go"},
        )
        .user_task("after", "善后", assignee="'u_cto'")
        .end("child_end", "子流程结束")
        .flow("start", "submit")
        .flow("submit", "sub")
        .flow("sub", "after")
        .flow("after", "child_end")
        .build()
        .model_copy(update={"variables": VARIABLES})
    )


def fragile_mi_definition() -> ProcessDefinition:
    """MI 子实例内：submit → mi_call(subjects×2, completion=all) → end。"""
    return (
        FlowBuilder("fragile_mi", "脆弱·多实例")
        .start()
        .user_task("submit", "提交", assignee="'u_dev_wu'")
        .call_activity(
            "fan",
            "多子扇出",
            process_code="fragile_kid",
            multi_instance={
                "subjects": ["'u_k1'", "'u_k2'"],
                "completion": "all",
            },
            input_mappings={"boom": "boom", "go": "go"},
        )
        .end("mi_end", "多实例结束")
        .flow("start", "submit")
        .flow("submit", "fan")
        .flow("fan", "mi_end")
        .build()
        .model_copy(update={"variables": VARIABLES})
    )
