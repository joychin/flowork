"""事件面守卫（2026-10-13 收尾批 4）：引擎自发的每一个事件名都在 DomainEvent 枚举内。

背景（内核完善评估实证）：此前 12+ 个事件以裸字符串实发、不在官方枚举
（task_consult_finished/task_withdrawn/task_countersigner_added/timeout_warned/
token_moved_by_admin 等）——订阅方按 ``DomainEvent`` 编程会静默漏收这五类
通知且零报错。本守卫静态扫描 flowork/ 全部 ``record_event``/``emit`` 调用：
字符串字面量实参必须 ∈ DomainEvent 值集（防名单再漂移）。

端口口径：``ctx.emit`` 对宿主仍是**开放事件名**（tests/pilot 与 tests/suite
的 ``payment_executed`` 即自定义业务事件），故运行期不做值域拒绝，本守卫只
约束引擎自身代码；动态实参（terminate_cascade 的 terminal_event、
task_actions 的 _emit_task_event 形参）已人工核实为枚举成员，不在扫描面。
"""

from __future__ import annotations

import ast
from pathlib import Path

from flowork.events.domain import DomainEvent

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWN_EVENT_NAMES = {member.value for member in DomainEvent}


def test_every_string_event_name_is_in_domain_enum():
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "flowork").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in {"record_event", "emit"}):
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value not in KNOWN_EVENT_NAMES
            ):
                violations.append(
                    f"{rel}:{arg.lineno} 裸字符串事件名 {arg.value!r} 不在 DomainEvent——"
                    "请入册枚举或改传成员"
                )
    assert not violations, "\n".join(violations)


def test_domain_event_values_unique():
    """值唯一性（别名会稀释名单的「一一对应」承诺）。"""
    values = [member.value for member in DomainEvent]
    assert len(values) == len(set(values))


def test_dynamic_event_sites_use_enum_members():
    """动态实参位点钉子（事件 review C5）：守卫只扫字符串字面量，
    terminate_cascade 的 terminal_event 是计算值——静态钉住其两翼仍引枚举
    成员（改成裸字符串时本测试先红）；message 面经 emit_message_event 形参
    类型 DomainEvent 收口（mypy strict 兜住调用侧）。
    """
    src = (REPO_ROOT / "flowork/engine/terminate_cascade.py").read_text(encoding="utf-8")
    assert "DomainEvent.PROCESS_TERMINATED" in src
    assert "DomainEvent.PROCESS_CANCELLED" in src
    strategies_src = (REPO_ROOT / "flowork/jobs/strategies.py").read_text(encoding="utf-8")
    assert "DomainEvent.PROCESS_TERMINATED" in strategies_src
    assert "DomainEvent.PROCESS_CANCELLED" in strategies_src
