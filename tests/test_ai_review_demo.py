"""ai_review demo 测试：引擎外注册自定义节点 → 校验 → 行为 → 路由（边界 M0 DoD）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from examples.ai_review_demo import AI_REVIEW_KIND, build_definition, run_demo
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.runtime.registry import create_registry


@pytest.fixture(scope="module")
def demo_results():
    return run_demo()


def test_ai_review_definition_validates():
    from examples.ai_review_demo import AiReviewBehavior, AiReviewSpec

    registry = create_registry()
    registry.register(AI_REVIEW_KIND, AiReviewSpec, AiReviewBehavior)
    issues = validate_definition(build_definition(), registry)
    assert [i for i in issues if i.level == IssueLevel.ERROR] == []


def test_low_confidence_escalates_to_human(demo_results):
    low = demo_results[0]
    assert low["variables"]["amount"] == 12_000
    assert low["variables"]["ai_confidence"] < 0.8
    assert any("human_review" in target for target in low["route_to"])


def test_high_confidence_passes_directly(demo_results):
    high = demo_results[1]
    assert high["variables"]["ai_confidence"] >= 0.8
    assert "done" in high["route_to"]


def test_behavior_emits_event(demo_results):
    assert "ai_review_finished" in demo_results[0]["events"]


def test_demo_script_runs(tmp_path: Path):
    """作为脚本直接执行也应成功退出（演示命令）。"""
    script = Path(__file__).parent.parent / "examples" / "ai_review_demo.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "低置信度场景" in result.stdout
