"""行数红线守卫（AGENTS.md 硬约束 6）：flowork 包单文件 ≤ 300 行。

静态扫描与 pytest 执行子集无关，单独运行本文件结论一致；超标即红，
拆分后再提交。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 300


def test_flowork_files_within_line_limit() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "flowork").rglob("*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_LINES:
            offenders.append(f"{path.relative_to(REPO_ROOT)}：{line_count} 行")
    assert not offenders, "超过 300 行红线的文件（拆分后再提交）：\n" + "\n".join(offenders)
