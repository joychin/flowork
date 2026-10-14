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
    assert "待办- 财务复核(finance) → u_finance" in out
    assert "待办+ 财务复核(finance) → u_finance_bak" in out
    # 会签两人各一票后走到传阅与终态
    assert "待办+ 总监会签(cfo) → u_alice" in out
    assert "待办+ 总监会签(cfo) → u_bob" in out
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
