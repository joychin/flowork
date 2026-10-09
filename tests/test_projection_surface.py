"""投影/插件公开面纪律：cli 与 contrib.* 对内核的直接 import 只准经 flowork.api。

import-linter 的 forbidden 契约按传递闭包判定——flowork.api 作为 re-export
聚合器必然链到内部包，「只准直接 import api」无法用 forbidden 表达，故本纪律
以直接 import 静态扫描落地（与 test_file_size_contract 同为仓库结构性守卫）。
rest/mcp 插件进驻 contrib 后自动受辖；宿主代码（仓库外）遵循同一纪律。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARDED_DIRS = ("flowork/cli", "flowork/contrib")
IMPORT_RE = re.compile(r"^\s*from\s+(flowork[\w.]*)\s+import|^\s*import\s+(flowork[\w.]*)", re.M)


def test_projections_import_kernel_only_via_public_api() -> None:
    offenders: list[str] = []
    for guarded in GUARDED_DIRS:
        self_prefix = guarded.replace("/", ".")
        for path in sorted((REPO_ROOT / guarded).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for match in IMPORT_RE.finditer(source):
                target = match.group(1) or match.group(2)
                if (
                    target == "flowork"
                    or target.startswith("flowork.api")
                    or target.startswith(self_prefix)
                ):
                    continue
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} → {target}")
    assert not offenders, (
        "投影/插件对内核的 import 只准经 flowork.api 公开门面（包内自引用除外）：\n"
        + "\n".join(offenders)
    )
