"""包版本双源守卫：flowork.__version__ 与 pyproject.toml 不得漂移。

两处各硬编码一份（0.1.0）——改其一不改其二，发布物与运行时口径分叉。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import flowork

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_package_version_matches_pyproject():
    project_version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]
    assert flowork.__version__ == project_version, (
        f"flowork/__init__.py 的 __version__（{flowork.__version__}）与 "
        f"pyproject.toml 的 project.version（{project_version}）漂移"
    )
