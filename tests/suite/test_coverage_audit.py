"""覆盖审计（三重）：特性矩阵全覆盖、世界节点全走查、黑盒 import 纯度。

审计用 ast 静态扫描 @covers / @covers_node 装饰器实参与 import 语句——
与 pytest 的执行子集无关，单独运行本文件结论一致。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.suite.features import all_features
from tests.suite.world_catalog import all_nodes

SUITE_DIR = Path(__file__).parent

# 黑盒白名单：套件只允许 import 公共面（引擎门面/目录/定义层/错误/事件/装配工具）
ALLOWED_FLOWORK_MODULES = {
    "flowork.contrib",
    "flowork.definitions.builder",
    "flowork.definitions.contract",
    "flowork.definitions.definition",
    "flowork.definitions.flow",
    "flowork.definitions.node",
    "flowork.definitions.validator",
    "flowork.directory.store",
    "flowork.engine.config",
    "flowork.engine.engine",
    "flowork.errors",
    "flowork.events.domain",
    "flowork.participants.org_directory",
    "flowork.persistence.db",
    "flowork.persistence.variable_index",
    "flowork.runtime.registry",
}


def _suite_sources() -> list[Path]:
    return sorted(path for path in SUITE_DIR.glob("*.py") if path.name != "__init__.py")


def _decorator_strings() -> tuple[set[str], set[tuple[str, str]]]:
    """静态扫描装饰器实参：(covers 特性集, covers_node (定义, 节点) 集)。"""
    features: set[str] = set()
    nodes: set[tuple[str, str]] = set()
    for path in _suite_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in item.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)):
                    continue
                constants = [
                    arg.value
                    for arg in decorator.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
                if decorator.func.id == "covers":
                    features.update(constants)
                elif decorator.func.id == "covers_node" and constants:
                    definition_code, node_codes = constants[0], constants[1:]
                    nodes.update((definition_code, code) for code in node_codes)
    return features, nodes


def _flowork_imports() -> set[str]:
    modules: set[str] = set()
    for path in _suite_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                modules.update(
                    alias.name for alias in item.names if alias.name.startswith("flowork")
                )
            elif isinstance(item, ast.ImportFrom) and item.module:
                if not item.module.startswith("flowork"):
                    continue
                if item.module == "flowork":
                    modules.update(f"flowork.{alias.name}" for alias in item.names)
                else:
                    modules.add(item.module)
    return modules


def test_feature_matrix_fully_covered() -> None:
    covered, _ = _decorator_strings()
    known = all_features()
    missing = known - covered
    unknown = covered - known
    assert not missing, f"特性矩阵未覆盖：{sorted(missing)}"
    assert not unknown, f"登记了矩阵外的特性（请先入 features.py）：{sorted(unknown)}"


def test_every_world_node_walked() -> None:
    _, covered_nodes = _decorator_strings()
    world = all_nodes()
    expected = {(code, node) for code, nodes in world.items() for node in nodes}
    missing = expected - covered_nodes
    unknown = covered_nodes - expected
    assert not missing, f"世界定义节点未被走查：{sorted(missing)}"
    assert not unknown, f"走查登记了不存在的节点：{sorted(unknown)}"


def test_blackbox_import_purity() -> None:
    violations = _flowork_imports() - ALLOWED_FLOWORK_MODULES
    assert not violations, f"套件 import 了白名单外的内核模块（污染黑盒边界）：{sorted(violations)}"
