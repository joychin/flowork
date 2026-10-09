"""套件·子流程构造族世界：call_activity 的维度矩阵（映射/嵌套/多子/驳回/暂停/版本）。

与 pilot 的 purchase→supplier 单点子流程互补——这里按「一个世界探一个维度簇」组织：
- suite_call_grand   孙辈：两层嵌套的终点，也是暂停/版本实验的载体
- suite_call_child   子辈：中间人（承接映射、向下嵌套、可驳回）
- suite_call_matrix  主流程：all_match 三臂（嵌套臂/直调臂/默认臂），mode 变量驱动组合
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable


def _vars(*names: str) -> list[ProcessVariable]:
    return [ProcessVariable(name=name, type="string", label=name, sample="") for name in names]


def call_grand_definition() -> ProcessDefinition:
    """孙辈：start → leaf(u_compliance 办结，带 grand_note) → end。"""
    return (
        FlowBuilder("suite_call_grand", "套件·孙流程")
        .start()
        .user_task("leaf", "孙辈办结", assignee="'u_compliance'", reject_target="start")
        .end("grand_end", "孙流程结束")
        .flow("start", "leaf")
        .flow("leaf", "grand_end")
        .build()
        .model_copy(update={"variables": _vars("grand_note")})
    )


def call_child_definition() -> ProcessDefinition:
    """子辈：middle(u_cash，可驳回到 start) → down(嵌套调孙) → end。

    输入映射三个维度：同名直传 / 字面量 / 缺键裸标识（→ None）。
    输出映射：孙的 grand_note 经 from_grand 回到父，再由主流程取走。
    """
    return (
        FlowBuilder("suite_call_child", "套件·子流程")
        .start()
        .user_task("middle", "子辈审查", assignee="'u_cash'", reject_target="start")
        .call_activity(
            "down",
            "嵌套调孙",
            process_code="suite_call_grand",
            input_mappings={"grand_note": "child_note"},
            output_mappings={"from_grand": "grand_note"},
        )
        .end("child_end", "子流程结束")
        .flow("start", "middle")
        .flow("middle", "down")
        .flow("down", "child_end")
        .build()
        .model_copy(
            update={"variables": _vars("child_note", "literal_note", "ghost", "from_grand")}
        )
    )


def call_matrix_definition() -> ProcessDefinition:
    """主流程：all_match 三臂按 mode 组合——nested=嵌套臂 / pause=直调臂 / 默认=跳过臂。

    pay（advance_policy=all）等齐所有激活臂：单臂/双臂组合都由此收口。
    输出映射覆盖父已有变量（overwrite_me）与多键写回（from_child/extra）。
    """
    return (
        FlowBuilder("suite_call_matrix", "套件·子流程矩阵")
        .start()
        .branch("hub", "模式分流", route_mode="all_match")
        .call_activity(
            "call_nested",
            "嵌套臂（调子）",
            process_code="suite_call_child",
            input_mappings={
                "child_note": "child_note",
                "literal_note": "'字面量'",
                "ghost": "missing_var",
            },
            output_mappings={
                "from_child": "from_grand",
                "extra": "child_note",
                "overwrite_me": "child_note",
            },
        )
        .call_activity(
            "call_direct",
            "直调臂（调孙）",
            process_code="suite_call_grand",
            input_mappings={"grand_note": "child_note"},
            output_mappings={"from_child": "grand_note", "overwrite_me": "grand_note"},
        )
        .user_task("skipped_direct", "默认臂快审", assignee="'u_chief_acc'")
        .service_task("pay", "收口动作", action="echo_paid", advance_policy="all")
        .end("matrix_end", "矩阵结束")
        .flow("start", "hub")
        .flow("hub", "call_nested", condition='mode == "nested" || mode == "both"')
        .flow("hub", "call_direct", condition='mode == "pause" || mode == "both"')
        .flow("hub", "skipped_direct", default=True)
        .flow("call_nested", "pay")
        .flow("call_direct", "pay")
        .flow("skipped_direct", "pay")
        .flow("pay", "matrix_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(name=name, type="string", label=name, sample="")
                    for name in ("mode", "child_note", "from_child", "extra", "missing_var")
                ]
                + [
                    ProcessVariable(
                        name="overwrite_me", type="string", label="覆盖位", sample="old"
                    )
                ]
            }
        )
    )


__all__ = ["call_child_definition", "call_grand_definition", "call_matrix_definition"]
