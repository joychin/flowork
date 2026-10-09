"""套件·分流构造族世界：first_match / all_match 的情形矩阵（含混合票与默认臂）。

两个分流节点串接，变量驱动组合：
- gate1（first_match）：tier 三档条件边 + 默认边——命中第一/命中第二/落默认
- gate2（all_match）：risk / go 两条件臂 + 默认臂——单臂/双臂/空臂
- done（advance_policy=all）：等齐所有激活臂收口
混合票：all_match 双臂激活后驳回其一 → 兄弟令牌取消、整体回退（reject_target=start）。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.engine.engine import ProcessEngine


def branch_matrix_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_branch_matrix", "套件·分流矩阵")
        .start()
        .branch("gate1", "档位分流")
        .user_task("a_gold", "金牌道", assignee="'u_cfo'", reject_target="start")
        .user_task("a_silver", "银牌道", assignee="'u_cash'", reject_target="start")
        .user_task("a_bronze", "铜牌道", assignee="'u_chief_acc'", reject_target="start")
        .branch("gate2", "风险分流", route_mode="all_match")
        .user_task("b_risk", "高风险道", assignee="'u_compliance'", reject_target="start")
        .user_task("b_go", "加急道", assignee="'u_purch_head'", reject_target="start")
        .user_task("b_none", "常规道", assignee="'u_buyer'")
        .service_task("done", "收口动作", action="echo_paid", advance_policy="all")
        .end("branch_end", "分流结束")
        .flow("start", "gate1")
        .flow("gate1", "a_gold", condition="tier >= 100")
        .flow("gate1", "a_silver", condition="tier >= 50")
        .flow("gate1", "a_bronze", default=True)
        .flow("a_gold", "gate2")
        .flow("a_silver", "gate2")
        .flow("a_bronze", "gate2")
        .flow("gate2", "b_risk", condition='risk == "high"')
        .flow("gate2", "b_go", condition="go")
        .flow("gate2", "b_none", default=True)
        .flow("b_risk", "done")
        .flow("b_go", "done")
        .flow("b_none", "done")
        .flow("done", "branch_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(name="tier", type="number", label="档位", sample=60),
                    ProcessVariable(name="risk", type="string", label="风险", sample="low"),
                    ProcessVariable(name="go", type="boolean", label="加急", sample=False),
                ]
            }
        )
    )


def start_branch(engine: ProcessEngine, **variables: object) -> str:
    """启动分流矩阵：缺省变量 = tier 60 / risk low / go false。"""
    params: dict[str, object] = {"tier": 60, "risk": "low", "go": False, **variables}
    return engine.runtime.start_process(
        "suite_branch_matrix", start_user="u_dev_wu", variables=params
    )
