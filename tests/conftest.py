"""共享 fixtures。"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork.runtime.registry import NodeRegistry, create_registry

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


@pytest.fixture
def registry() -> NodeRegistry:
    """内置类型已注册的全新注册表。"""
    return create_registry()


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES_DIR
