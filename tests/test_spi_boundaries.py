"""P0-2（kernel-v2 spec §9/§16）：SPI 边界与目录四方法端口测试。

- import-linter 红灯演示：内核 import 插件包（contrib）必须被 CI 检出；
- OrgDirectory 四方法（is_member/subjects_in/manager_of/snapshot）：协议结构性
  ＋静态目录/持久化目录实现语义；
- validate：图纸用组主体而目录未配置 → WARNING（不拦发布）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.participants.org_directory import OrgDirectory

FLOWORK_ROOT = Path(__file__).parent.parent / "flowork"
KERNEL_PACKAGES = (
    "definitions",
    "runtime",
    "expr",
    "spi",
    "events",
    "tasks",
    "participants",
    "persistence",
    "engine",
    "jobs",
    "directory",
)
PLUGIN_PACKAGES = ("contrib",)


def _imports_of(tree: ast.AST) -> set[str]:
    """收集 import / from-import 目标相对 flowork 根的包名集合。

    ``from flowork.contrib import x`` → {"contrib"}；``from flowork import contrib``
    → {"contrib"}；``import flowork.contrib`` → {"contrib"}；外部包取顶层名。
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                targets.add(parts[1] if parts[0] == "flowork" and len(parts) > 1 else parts[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "flowork":
                if len(parts) > 1:
                    targets.add(parts[1])
                else:  # from flowork import contrib, engine, ...
                    targets.update(alias.name for alias in node.names)
            else:
                targets.add(parts[0])
    return targets


def _kernel_files() -> list[Path]:
    files: list[Path] = [FLOWORK_ROOT / "errors.py", FLOWORK_ROOT / "clock.py"]
    for package in KERNEL_PACKAGES:
        files.extend(sorted((FLOWORK_ROOT / package).rglob("*.py")))
    return [path for path in files if path.exists()]


def test_kernel_never_imports_plugin_packages():
    """内核包零 import 插件包（与 lint-imports 契约同判据，CI 双保险）。"""
    violations = [
        f"{path.relative_to(FLOWORK_ROOT)} → {target}"
        for path in _kernel_files()
        for target in _imports_of(ast.parse(path.read_text(encoding="utf-8")))
        if target in PLUGIN_PACKAGES
    ]
    assert not violations, f"内核 import 插件包（红灯）：{violations}"


def test_import_boundary_checker_flags_violation():
    """红灯演示：把一段「内核 import contrib」的合成源码喂给检查器，必须被检出。"""
    synthetic = ast.parse("from flowork.contrib import org_vocabulary\n")
    assert _imports_of(synthetic) & set(PLUGIN_PACKAGES), "检查器漏检合成违规"


def test_org_directory_port_four_methods():
    """OrgDirectoryLike 实现类结构性满足四方法（spec §9 签名）。"""
    for method in ("is_member", "subjects_in", "manager_of", "snapshot"):
        assert callable(getattr(OrgDirectory(), method, None)), f"静态目录缺 {method}"


ORG = OrgDirectory.from_dict(
    {
        "users": {
            "u_zhang": {"manager": "u_bob", "department": "d_eng"},
            "u_bob": {"manager": "u_ceo", "department": "d_eng", "roles": ["eng_lead"]},
            "u_fin1": {"department": "d_fin", "roles": ["finance"]},
        },
        "departments": {"d_eng": {"head": "u_bob"}, "d_fin": {"head": "u_ceo"}},
    }
)


class TestStaticDirectoryFourMethods:
    def test_is_member_role_and_org(self):
        assert ORG.is_member("u_bob", "role:eng_lead")
        assert ORG.is_member("u_bob", "eng_lead")  # 裸名视同角色
        assert ORG.is_member("u_zhang", "org:d_eng")
        assert not ORG.is_member("u_zhang", "org:d_fin")
        assert not ORG.is_member("u_ghost", "role:eng_lead")
        assert not ORG.is_member("u_bob", "role:")

    def test_subjects_in_direct_members_only(self):
        assert sorted(ORG.subjects_in("role:finance")) == ["u_fin1"]
        assert sorted(ORG.subjects_in("org:d_eng")) == ["u_bob", "u_zhang"]
        assert ORG.subjects_in("role:nope") == []

    def test_manager_of_user_org_and_none(self):
        assert ORG.manager_of("u_zhang") == "u_bob"
        assert ORG.manager_of("org:d_eng") == "u_bob"  # 部门负责人
        assert ORG.manager_of("u_ceo") is None

    def test_snapshot_from_config_and_empty(self):
        assert ORG.snapshot("u_bob") == {"department": "d_eng", "roles": ["eng_lead"]}
        assert ORG.snapshot("u_zhang") == {"department": "d_eng", "roles": []}
        assert ORG.snapshot("user:u_bob") == {"department": "d_eng", "roles": ["eng_lead"]}
        assert ORG.snapshot("u_ghost") == {}

    def test_empty_config_neutral(self):
        """空配置目录：四方法返回 False/[]/None/{}（裸跑不受影响）。"""
        empty = OrgDirectory()
        assert empty.is_member("u", "role:x") is False
        assert empty.subjects_in("role:x") == []
        assert empty.manager_of("u") is None
        assert empty.snapshot("u") == {}


def test_validate_warns_group_subjects_without_directory():
    """验收（spec §16 P0-2）：图纸用组主体而目录未配置 → validate 警告（不拦发布）。"""
    from flowork.definitions.validator import IssueLevel, validate_definition
    from flowork.runtime.registry import create_registry

    definition = (
        FlowBuilder("grp_warn")
        .start()
        .user_task("a", candidate_groups=["'role:finance'", "'org:d_eng'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    registry = create_registry()
    issues = validate_definition(definition, registry, org_configured=False)
    assert "participant.group_without_directory" in {i.rule for i in issues}
    assert not [
        i for i in issues if i.level == IssueLevel.ERROR and i.rule.startswith("participant.")
    ]

    # 目录已配置 → 不告警；未知（None，独立校验器）→ 也不告警
    assert "participant.group_without_directory" not in {
        i.rule for i in validate_definition(definition, registry, org_configured=True)
    }
    assert "participant.group_without_directory" not in {
        i.rule for i in validate_definition(definition, registry)
    }


def test_contrib_has_role_predicate():
    """contrib has_role(one_user, string)→bool：谓词语汇（条件位，CEL 面）。"""
    from flowork.expr.cel import evaluate_formula

    table = {name: item.fn for name, item in contrib.cel_vocabulary(ORG).functions().items()}
    assert evaluate_formula('has_role("u_bob", "eng_lead")', {}, functions=table)
    assert not evaluate_formula('has_role("u_zhang", "eng_lead")', {}, functions=table)


@pytest.mark.parametrize("subject", ["role:finance", "role:nope"])
def test_contrib_manager_of_rejects_role(subject):
    """manager_of 吃 role: 主体 → 报错（贡献包内置守卫，经 CEL 面调用）。"""
    from flowork.expr.cel import evaluate_formula_value

    table = {name: item.fn for name, item in contrib.cel_vocabulary(ORG).functions().items()}
    with pytest.raises(Exception, match="角色主体"):
        evaluate_formula_value(f'manager_of("{subject}")', {}, functions=table)


# —— contrib CEL 注册面（cel_vocabulary，spec §4.3/§9）——


def test_contrib_cel_vocabulary_port_shape():
    """cel_vocabulary 返回 ExpressionFunctions：三词汇且带类型签名。"""
    port = contrib.cel_vocabulary(ORG)
    assert callable(getattr(port, "functions", None))
    table = port.functions()
    assert set(table) == {"manager_of", "users_with_role", "has_role"}
    assert table["manager_of"].signature.params == ("one_user",)
    assert table["manager_of"].signature.returns == "one_user"
    assert table["users_with_role"].signature.returns == "users"
    assert table["has_role"].signature.params == ("one_user", "string")
    assert table["has_role"].signature.returns == "bool"


def test_contrib_cel_vocabulary_callable_semantics():
    """CEL 词汇纯读语义：与参与者面同源（manager/roles/成员判定），None 空传播。"""
    from flowork.expr.cel import evaluate_formula

    functions = contrib.cel_vocabulary(ORG)
    table = {name: item.fn for name, item in functions.functions().items()}
    assert evaluate_formula('manager_of("u_zhang") == "u_bob"', {}, functions=table)
    assert evaluate_formula('manager_of("u_nobody") == null', {}, functions=table)
    assert evaluate_formula('has_role("u_bob", "eng_lead")', {}, functions=table)
    assert not evaluate_formula('has_role("u_zhang", "eng_lead")', {}, functions=table)
    assert evaluate_formula('users_with_role("eng_lead").size() == 1', {}, functions=table)


# —— contrib 数值/字符串词汇包（numeric_vocabulary，能力补足 P0-2）——


def test_contrib_numeric_vocabulary_port_shape():
    """numeric_vocabulary 返回 ExpressionFunctions：六词汇且带类型签名与中文 label。"""
    port = contrib.numeric_vocabulary()
    table = port.functions()
    assert set(table) == {"split", "abs", "max", "min", "sort", "sum"}
    expected = {
        "split": (("string", "string"), "list", "拆分字符串"),
        "abs": (("number",), "number", "绝对值"),
        "max": (("list",), "number", "列表最大值"),
        "min": (("list",), "number", "列表最小值"),
        "sort": (("list",), "list", "列表排序"),
        "sum": (("list",), "number", "列表求和"),
    }
    for name, (params, returns, label) in expected.items():
        assert table[name].signature.params == params
        assert table[name].signature.returns == returns
        assert table[name].signature.label == label


def test_contrib_numeric_vocabulary_semantics():
    """CEL 面直测：拆分/绝对值/最值/排序/求和，空列表语义（max/min→null，sum→0）。"""
    from flowork.expr.cel import evaluate_formula, evaluate_formula_value

    table = {name: item.fn for name, item in contrib.numeric_vocabulary().functions().items()}
    assert evaluate_formula('split("a,b,c", ",").size() == 3', {}, functions=table)
    assert evaluate_formula('split("a,b,c", ",")[1] == "b"', {}, functions=table)
    assert evaluate_formula('split("solo", "") == ["solo"]', {}, functions=table)
    assert evaluate_formula("abs(0 - 5) == 5", {}, functions=table)
    assert evaluate_formula("max([3, 1, 2]) == 3", {}, functions=table)
    assert evaluate_formula("min([3, 1, 2]) == 1", {}, functions=table)
    assert evaluate_formula("sort([3, 1, 2]) == [1, 2, 3]", {}, functions=table)
    assert evaluate_formula('sort(["b", "a"]) == ["a", "b"]', {}, functions=table)
    assert evaluate_formula("sort([3, 1, 2])[0] == 1", {}, functions=table)
    assert evaluate_formula("sum([1, 2, 3]) == 6", {}, functions=table)
    assert evaluate_formula("max([]) == null", {}, functions=table)
    assert evaluate_formula("min([]) == null", {}, functions=table)
    assert evaluate_formula("sum([]) == 0", {}, functions=table)
    # 与 comprehension 组合：排序后过滤
    assert evaluate_formula("sort([3, 1, 2]).filter(x, x > 1) == [2, 3]", {}, functions=table)
    assert evaluate_formula_value("sum([1, 2])", {}, functions=table) == 3


def test_contrib_numeric_min_does_not_shadow_method_form():
    """注册全局 min 后，celpy 的 [..].min() 方法形态仍可用（同签名共存）。"""
    from flowork.expr.cel import evaluate_formula

    table = {name: item.fn for name, item in contrib.numeric_vocabulary().functions().items()}
    assert evaluate_formula("[3, 1].min() == 1", {}, functions=table)
    assert evaluate_formula("min([5, 2]) == 2", {}, functions=table)


def test_contrib_numeric_type_mismatch_is_flowork_error():
    """类型不符 → FloworkError（经 cel 归一 FormulaError，技术故障语义）。"""
    from flowork.expr.cel import FormulaError, evaluate_formula

    table = {name: item.fn for name, item in contrib.numeric_vocabulary().functions().items()}
    with pytest.raises(FormulaError, match="数值"):
        evaluate_formula('abs("text") == 1', {}, functions=table)
    with pytest.raises(FormulaError, match="列表"):
        evaluate_formula("max(3) == 1", {}, functions=table)
    with pytest.raises(FormulaError, match="字符串"):
        evaluate_formula("split(1, 2) == []", {}, functions=table)
    with pytest.raises(FormulaError, match="同为"):
        evaluate_formula('sort([1, "a"]) == []', {}, functions=table)
