"""走查演示·驳回与拒收专场：全票否决回退、显式 target、并行驳回兄弟归零、契约拒收。

运行：``uv run python -m tests.suite.walkthrough_reject_demo``
每步 expect_position 硬校验 + 预期异常逐一打印。时限/故障专场见 walkthrough_fault_demo。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from tests.suite.demo_kit import (
    archived,
    expect_error,
    make_bare_engine,
    make_store_engine,
    step,
)
from tests.suite.walkthrough import complete_of, reject_of, task_of


def start_expense(engine: ProcessEngine, amount: int) -> str:
    return engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": amount, "expense_type": "office"},
    )


def scenario_full_disapproval_fallback(engine: ProcessEngine) -> None:
    print("\n场景 D｜串签全票否决——未配 disapprove_exit 默认全票回退，来源节点复活")
    iid = start_expense(engine, 8000)
    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")
    step(
        engine,
        iid,
        "会签 2/2 通过，串签第一位 CFO 待办",
        nodes={"level_sign"},
        tasks={("level_sign", "u_cfo")},
    )

    reject_of(engine, iid, "u_cfo", comment="预算有问题")
    step(
        engine,
        iid,
        "CFO 一票否决（1/2 未达全票出口）——串签继续下一位",
        nodes={"level_sign"},
        tasks={("level_sign", "u_ceo")},
    )

    reject_of(engine, iid, "u_ceo", comment="维持否决")
    step(
        engine,
        iid,
        "CEO 二票否决（2/2 出口达成）——回退来源节点，原办理人复活",
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
    )
    for assignee in ("u_chief_acc", "u_cash", "u_cfo", "u_ceo"):
        complete_of(engine, iid, assignee)
    step(engine, iid, "复活后重走全链 → 完结", nodes=set(), state="completed")


def scenario_explicit_target_reject(engine: ProcessEngine) -> None:
    print("\n场景 E｜显式 target 驳回——压过节点配置（会签配置的 start），直打 level_sign")
    iid = start_expense(engine, 8000)
    engine.task.reject(
        task_of(engine, iid, "u_chief_acc"),
        actor="u_chief_acc",
        target="level_sign",
        comment="单据合规问题需上级裁定",
    )
    print(f"    ✓ 已办：{archived(engine, iid)}")
    step(
        engine,
        iid,
        "驳回落点=显式 target（非配置的 start）",
        nodes={"level_sign"},
        tasks={("level_sign", "u_cfo")},
    )
    complete_of(engine, iid, "u_cfo")
    complete_of(engine, iid, "u_ceo")
    step(engine, iid, "上级裁定通过 → 完结", nodes=set(), state="completed")


def scenario_parallel_reject_cancels_sibling() -> None:
    print("\n场景 F｜并行分支中驳回——其余未完兄弟令牌归零取消")
    engine = make_bare_engine()
    engine.repository.deploy(
        FlowBuilder("demo_sibling")
        .start()
        .user_task("a", "支路A", assignee="'u_a'", reject_target="start")
        .user_task("b", "支路B", assignee="'u_b'")
        .user_task("j", "汇聚", assignee="'u_j'")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "j")
        .connect("b", "j")
        .connect("j", "end")
        .build()
    )
    iid = engine.runtime.start_process("demo_sibling", start_user="u_s")
    step(engine, iid, "发起后双支路并行", nodes={"a", "b"}, tasks={("a", "u_a"), ("b", "u_b")})

    reject_of(engine, iid, "u_a", comment="支路A驳回")
    token_b = next(t for t in engine.history.tokens(iid) if t.activity_code == "b")
    print(f"    ✓ 兄弟令牌 b 状态={token_b.state}（未完令牌归零）")
    step(
        engine, iid, "驳回到 start，支路B待办随令牌取消", nodes={"start"}, tasks={("start", "u_s")}
    )

    complete_of(engine, iid, "u_s")
    step(engine, iid, "重提后双支路再次并行", nodes={"a", "b"}, tasks={("a", "u_a"), ("b", "u_b")})
    complete_of(engine, iid, "u_a")
    step(engine, iid, "any 汇聚先到激活，支路B被吸收取消", nodes={"j"}, tasks={("j", "u_j")})
    complete_of(engine, iid, "u_j")
    step(engine, iid, "汇聚办理 → 完结", nodes=set(), state="completed")


def scenario_contract_rejection(engine: ProcessEngine) -> None:
    print("\n场景 J｜input 契约拒收——业务失败（不产生故障单，与故障专场对照）")
    expect_error(
        "发起缺 amount（边界=start）",
        engine.runtime.start_process,
        "suite_expense",
        start_user="u_dev_wu",
        variables={"expense_type": "office"},
    )
    iid = start_expense(engine, 1200)
    expect_error(
        "办理时把金额改成 -1（边界=task_output）",
        complete_of,
        engine,
        iid,
        "u_lead_be",
        variables={"amount": -1},
    )
    step(
        engine,
        iid,
        "拒收后命令回滚，待办仍在",
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
    )
    assert engine.runtime.list_incidents(instance_id=iid) == []
    print("    ✓ 契约违例不产生故障单（错误二分：业务失败 ≠ 技术故障）")
    complete_of(engine, iid, "u_lead_be")
    step(engine, iid, "合法提交通过 → 完结", nodes=set(), state="completed")


def main() -> None:
    print("═" * 72)
    print("flowork 走查演示·驳回与拒收专场（每步位置快照 + 硬校验 + 预期异常）")
    print("═" * 72)
    with tempfile.TemporaryDirectory() as tmp_name:
        engine = make_store_engine(Path(tmp_name))
        scenario_full_disapproval_fallback(engine)
        scenario_explicit_target_reject(engine)
        scenario_contract_rejection(engine)
    scenario_parallel_reject_cancels_sibling()
    print("\n" + "═" * 72)
    print("驳回与拒收专场全部通过：全票回退/显式 target/兄弟归零/契约拒收均按预期。")
    print("═" * 72)


if __name__ == "__main__":
    main()
