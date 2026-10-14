"""walkthrough 演练台黑盒测试（演练台批 2026-10-14）。

剧本模式两场景全旅程（分支择道/驳回回退/改额/转办/会签/传阅收束）+
错误路径（节点/办理人/引擎拒绝）+ 交互模式 stdin 冒烟。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from flowork.cli.app import app

runner = CliRunner()
EXAMPLES = Path(__file__).parent.parent / "examples"
TEMPLATE = EXAMPLES / "04_expense_claim.json"
SCRIPT = EXAMPLES / "04_expense_walkthrough.json"


def _invoke(*args: str, stdin: str | None = None) -> object:
    return runner.invoke(app, list(args), input=stdin)


def test_template_validates() -> None:
    result = _invoke("validate", str(TEMPLATE))
    assert result.exit_code == 0
    assert "校验通过" in result.output


def test_scripted_high_amount_full_journey() -> None:
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(SCRIPT))
    assert result.exit_code == 0, result.output
    out = result.output
    # 两场景都收束为 completed，步数计数 0/8 起步（发起即第 0 步）
    assert out.count("状态 = completed") == 2
    assert "步骤 0/8" in out and "步骤 8/8" in out
    # 分支择道：条件公式与求值结果进步单
    assert "amount > 5000 = true → 选中 route->finance" in out
    # 驳回回退：submit 复活 + 重报改额的变量血缘
    assert "task_rejected" in out
    assert "amount: 20000 → 18000（task_output·u_zhang）" in out
    # 转办：旧任务离场、新办理人接棒
    assert "离场 财务复核(finance) → u_finance" in out
    assert "新增 财务复核(finance) → u_finance_bak" in out
    # 会签两人各一票后走到传阅与终态
    assert "新增 总监会签(cfo) → u_alice" in out
    assert "新增 总监会签(cfo) → u_bob" in out
    assert "cc_created" in out and "process_completed" in out


def test_scripted_low_amount_takes_default_edge(tmp_path: Path) -> None:
    doc = json.loads(SCRIPT.read_text(encoding="utf-8"))
    doc["runs"] = [doc["runs"][1]]  # 只跑小额场景
    only_low = tmp_path / "low.json"
    only_low.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(only_low))
    assert result.exit_code == 0, result.output
    out = result.output
    assert "状态 = completed" in out
    assert "无条件/默认边" in out  # 默认边直通传阅（默认边不进 edges，只报选中）
    assert "finance" not in out and "cfo" not in out  # 未加签、未复核


def test_step_on_absent_node_reports_pending(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {"runs": [{"steps": [{"node": "finance", "by": "u_finance", "action": "complete"}]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(bad))
    assert result.exit_code == 1
    assert "节点 finance 没有就绪任务" in result.output
    assert "当前待办节点：submit" in result.output


def test_step_with_wrong_assignee_reports_actual(tmp_path: Path) -> None:
    bad = tmp_path / "who.json"
    bad.write_text(
        json.dumps(
            {"runs": [{"steps": [{"node": "submit", "by": "u_nobody", "action": "complete"}]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(bad))
    assert result.exit_code == 1
    assert "办理人是 u_zhang，不是 u_nobody" in result.output


def test_engine_rejection_surfaces_in_step_context(tmp_path: Path) -> None:
    bad = tmp_path / "resign.json"
    bad.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "steps": [
                            {
                                "node": "submit",
                                "by": "u_zhang",
                                "action": "complete",
                                "variables": {"amount": 3000},
                            },
                            # 已是办理人再加签自己＝重复加签，引擎整笔回拒（review 批1语义）
                            {
                                "node": "manager",
                                "by": "u_manager",
                                "action": "add_countersigner",
                                "to": "u_manager",
                            },
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(bad))
    assert result.exit_code == 1
    # 长消息可能被终端折行，断言用稳定片段
    assert "被引擎拒绝" in result.output
    assert "已在会签名单" in result.output


def test_script_missing_transfer_target_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "xfer.json"
    bad.write_text(
        json.dumps(
            {"runs": [{"steps": [{"node": "submit", "action": "transfer"}]}]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(bad))
    assert result.exit_code == 1
    assert "transfer 步骤必须给 to" in result.output


def test_interactive_walkthrough_completes(tmp_path: Path) -> None:
    stdin = "\n".join(
        [
            "",  # 起始变量跳过
            "1 complete",  # submit
            "",  # 办理人默认 u_zhang
            "提交报销",
            '{"amount": 3000, "reason": "测试"}',
            "1 complete",  # manager
            "",  # 办理人默认 u_manager
            "同意",
            "",  # 变量跳过
            "",  # 冗余：实例已终态不消费，防 CliRunner 输入记数差一
        ]
    )
    result = _invoke("walkthrough", str(TEMPLATE), stdin=stdin)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "实例已发起" in out
    assert "amount > 5000 = false" in out
    assert "状态 = completed" in out
    assert "流转轨迹" in out


def test_interactive_quit_leaves_summary(tmp_path: Path) -> None:
    stdin = "\n".join(["", "q"])  # 发起后立即收束
    result = _invoke("walkthrough", str(TEMPLATE), stdin=stdin)
    assert result.exit_code == 0, result.output
    assert "未走完" in result.output
    assert "剩余待办 submit→u_zhang" in result.output


def test_ambiguous_node_without_by_rejected(tmp_path: Path) -> None:
    doc = json.loads(SCRIPT.read_text(encoding="utf-8"))
    run = doc["runs"][0]
    run["steps"] = run["steps"][:6]  # 财务复核也办完，cfo 两人待办在途
    run["steps"].append({"node": "cfo", "action": "complete"})  # 无 by：两人待办必须点名
    only = tmp_path / "amb.json"
    only.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    result = _invoke("walkthrough", str(TEMPLATE), "--script", str(only))
    assert result.exit_code == 1
    assert "请在步骤里用 by 指定办理人" in result.output
    assert "u_alice" in result.output and "u_bob" in result.output


def _candidate_template(tmp_path: Path) -> Path:
    template = {
        "code": "walk_candidate",
        "name": "候选认领演练",
        "version": 1,
        "status": "published",
        "variables": [],
        "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {
                "code": "review",
                "name": "评审",
                "kind": "user_task",
                "candidate_users": ["'u_carol'"],
            },
            {"code": "end", "name": "结束", "kind": "end"},
        ],
        "flows": [
            {"source": "start", "target": "review"},
            {"source": "review", "target": "end"},
        ],
    }
    path = tmp_path / "cand.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    return path


def test_claim_visible_in_step_output(tmp_path: Path) -> None:
    template = _candidate_template(tmp_path)
    script = tmp_path / "cand_walk.json"
    script.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "steps": [
                            {"node": "review", "by": "u_carol", "action": "claim"},
                            {
                                "node": "review",
                                "by": "u_carol",
                                "action": "complete",
                                "comment": "认领后办理",
                            },
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _invoke("walkthrough", str(template), "--script", str(script))
    assert result.exit_code == 0, result.output
    out = result.output
    # 认领不换任务行 id：步单须补「办理人」行，否则认领在叙事里不可见
    assert "办理人 u_carol（认领后）" in out
    assert "状态 = completed" in out


def test_interactive_engine_rejection_continues(tmp_path: Path) -> None:
    stdin = "\n".join(
        [
            "",  # 起始变量跳过
            "1 complete",  # submit
            "",  # 办理人默认 u_zhang
            "提交",
            '{"amount": 3000}',
            "1 add_countersigner",  # manager 已是办理人：给自己加签会被引擎拒
            "",  # 办理人默认 u_manager
            "",  # 意见跳过
            "u_manager",  # 对象人＝自己
            "q",  # 拒绝后应回到命令行，收束退出
        ]
    )
    result = _invoke("walkthrough", str(TEMPLATE), stdin=stdin)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "动作未生效" in out and "已在会签名单" in out
    # 拒绝后实例仍在原位（manager 待办未动），收束如实示未走完
    assert "剩余待办 manager→u_manager" in out


def test_interactive_bad_json_thrice_cancels_action() -> None:
    stdin = "\n".join(
        [
            "",  # 起始变量跳过
            "1 complete",  # submit
            "",  # 办理人
            "",  # 意见跳过
            "{bad",  # 变量 JSON 错 1
            "{bad",  # 错 2
            "{bad",  # 错 3 → 放弃＝取消本步动作
            "q",  # 回命令行收束：submit 仍未办
        ]
    )
    result = _invoke("walkthrough", str(TEMPLATE), stdin=stdin)
    assert result.exit_code == 0, result.output
    assert "已取消本次动作" in result.output
    assert "剩余待办 submit→u_zhang" in result.output
