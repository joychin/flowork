"""走查演示·时限与故障专场：超时四档实触发、暂停冻结与终态手术、技术故障五动作。

运行：``uv run python -m tests.suite.walkthrough_fault_demo``
驳回与拒收专场见 ``walkthrough_reject_demo``。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from flowork.engine.engine import ProcessEngine
from tests.suite.demo_kit import (
    archived,
    expect_error,
    make_bare_engine,
    make_store_engine,
    step,
)
from tests.suite.walkthrough import complete_of


def names(engine: ProcessEngine, iid: str) -> list[str]:
    return [event.event_name for event in engine.history.events(iid)]


def _explode(ctx, params) -> None:
    raise RuntimeError("外部接口炸了")


def _explode_fixed(ctx, params) -> None:
    ctx.variables["fixed"] = True


def scenario_timeout_ladder(engine: ProcessEngine) -> None:
    print("\n场景 G｜超时档位逐档实触发（时钟推进）")
    clock = engine.config.now
    iid = engine.runtime.start_process("suite_deadline_notify", start_user="u_dev_liu")
    due = engine.task.list_tasks(instance_id=iid)[0].due_at
    print(f"  G1 预警+升级：首条 deadline 物化 due_at={due:%m-%d %H:%M}")

    clock.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    print(f"    ▶ t+5 预警档：timeout_warned ×{names(engine, iid).count('timeout_warned')}")
    step(engine, iid, "预警不改派，待办仍在原办理人", nodes={"hold"}, tasks={("hold", "u_dev_liu")})

    clock.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    print(f"    ▶ t+10 升级档：task_escalated 事件={names(engine, iid).count('task_escalated')}")
    step(
        engine,
        iid,
        "升级＝任务移交办理人主管（赵敏）",
        nodes={"hold"},
        tasks={("hold", "u_lead_fe")},
    )
    complete_of(engine, iid, "u_lead_fe")
    step(engine, iid, "主管办结 → 完结", nodes=set(), state="completed")

    iid = engine.runtime.start_process("suite_deadline_reassign", start_user="u_dev_liu")
    clock.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    step(
        engine,
        iid,
        "G2 改派档：任务移交 reassign_to 王工",
        nodes={"gate"},
        tasks={("gate", "u_lead_be")},
    )
    clock.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    print("    ▶ t+10 自动通过档到期，但 deadline 绑定原任务（已随移交归档）→ 跳过")
    step(
        engine,
        iid,
        "后续档不作用于移交后的新任务——仍待人办",
        nodes={"gate"},
        tasks={("gate", "u_lead_be")},
    )
    complete_of(engine, iid, "u_lead_be")

    iid = engine.runtime.start_process("suite_deadline_auto", start_user="u_dev_liu")
    clock.advance(10)  # type: ignore[attr-defined]
    engine.jobs.tick()
    step(
        engine,
        iid,
        "G3 原任务未被移交：预警+自动通过两档先后生效 → 完结",
        nodes=set(),
        state="completed",
    )
    print(f"    ✓ 已办：{archived(engine, iid)}（system:timeout 代办归因）")

    iid = engine.runtime.start_process("suite_deadline_reject", start_user="u_dev_liu")
    clock.advance(10)  # type: ignore[attr-defined]
    engine.jobs.tick()
    step(
        engine,
        iid,
        "G4 超时自动驳回：闸口否决回发起人",
        nodes={"start"},
        tasks={("start", "u_dev_liu")},
    )
    complete_of(engine, iid, "u_dev_liu")
    complete_of(engine, iid, "u_dev_liu")
    step(engine, iid, "重提后人工通过 → 完结", nodes=set(), state="completed")


def scenario_pause_and_terminal(engine: ProcessEngine) -> None:
    print("\n场景 I｜暂停冻结计时 + 终态手术（terminate/cancel）")
    clock = engine.config.now
    iid = engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu")
    engine.runtime.pause_process(iid, actor="u_cto", reason="月末对账冻结")
    expect_error("暂停实例办理被拦截", complete_of, engine, iid, "u_dev_wu")
    step(
        engine,
        iid,
        "已暂停（待办可见、不可办）",
        state="paused",
        nodes={"first_task"},
        tasks={("first_task", "u_dev_wu")},
    )

    clock.advance(60)  # type: ignore[attr-defined]
    engine.jobs.tick()
    assert "timeout_warned" not in names(engine, iid)
    print("    ▶ 暂停期推进 60 分钟：timer 冻结，无预警事件")

    engine.runtime.resume_process(iid, actor="u_cto")
    clock.advance(60)  # type: ignore[attr-defined]
    engine.jobs.tick()
    count = names(engine, iid).count("timeout_warned")
    print(f"    ▶ 恢复后计时顺延补触发：timeout_warned ×{count}")
    assert count == 1
    complete_of(engine, iid, "u_dev_wu")
    complete_of(engine, iid, "u_cto")
    step(engine, iid, "恢复后办完两环 → 完结", nodes=set(), state="completed")

    killed = engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu")
    engine.runtime.terminate_process(killed, actor="u_ceo", reason="违规发起")
    dropped = engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu")
    engine.runtime.cancel_process(dropped, actor="u_dev_wu", reason="撤单")
    step(engine, killed, "terminate：管理员强杀终态", nodes=set(), state="terminated")
    step(engine, dropped, "cancel：业务作废终态", nodes=set(), state="cancelled")


def scenario_incident_resolution() -> None:
    print("\n场景 H｜技术故障二分——挂起/复发/五动作修复（resume/retry/skip/cancel/fail）")
    engine = make_bare_engine("suite_fragile")
    engine.register_handler("explode", _explode, replace=True)  # 确保处于"损坏"态

    iid = engine.runtime.start_process("suite_fragile", start_user="u_dev_wu")
    expect_error("提交触发外部调用故障", complete_of, engine, iid, "u_dev_wu")
    step(
        engine,
        iid,
        "命令回滚挂起，待办保留在提交人",
        state="suspended",
        nodes={"submit"},
        tasks={("submit", "u_dev_wu")},
    )
    incident = engine.runtime.list_incidents(instance_id=iid)[0]
    print(f"    ▶ 故障单 open：{incident['error_class']}｜{incident['message'][:42]}")

    engine.runtime.resolve_incident(incident["id"], action="resume", actor="ops")
    expect_error("resume 后根因未修——重办复发", complete_of, engine, iid, "u_dev_wu")
    assert engine.runtime.get_instance(iid).state == "suspended"

    engine.register_handler("explode", _explode_fixed, replace=True)
    reopen = engine.runtime.list_incidents(instance_id=iid)[0]["id"]
    engine.runtime.resolve_incident(reopen, action="retry", actor="ops")
    complete_of(engine, iid, "u_dev_wu")
    complete_of(engine, iid, "u_cto")
    step(engine, iid, "retry（修复后重办）→ 走完全链", nodes=set(), state="completed")

    for action, terminal in (("skip", None), ("cancel", "cancelled"), ("fail", "terminated")):
        engine.register_handler("explode", _explode, replace=True)
        other = engine.runtime.start_process("suite_fragile", start_user="u_dev_wu")
        expect_error(f"{action} 场景·外部调用故障", complete_of, engine, other, "u_dev_wu")
        engine.register_handler("explode", _explode_fixed, replace=True)
        incident_id = engine.runtime.list_incidents(instance_id=other)[0]["id"]
        engine.runtime.resolve_incident(incident_id, action=action, actor="ops")
        if action == "skip":
            assert engine.task.list_tasks(instance_id=other, assignee="u_dev_wu") == []
            step(
                engine,
                other,
                "skip：停住节点待办作废，直通善后",
                nodes={"after"},
                tasks={("after", "u_cto")},
            )
            complete_of(engine, other, "u_cto")
            step(engine, other, "善后办结", nodes=set(), state="completed")
        else:
            step(engine, other, f"{action}：实例进入终态", nodes=set(), state=terminal)


def main() -> None:
    print("═" * 72)
    print("flowork 走查演示·时限与故障专场（每步位置快照 + 硬校验 + 预期异常）")
    print("═" * 72)
    with tempfile.TemporaryDirectory() as tmp_name:
        engine = make_store_engine(Path(tmp_name))
        scenario_timeout_ladder(engine)
        scenario_pause_and_terminal(engine)
    scenario_incident_resolution()
    print("\n" + "═" * 72)
    print("时限与故障专场全部通过：超时四档/暂停冻结/终态手术/故障五动作均按预期。")
    print("═" * 72)


if __name__ == "__main__":
    main()
