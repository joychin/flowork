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
        assert callable(getattr(contrib, "org_vocabulary", None))


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
        .user_task("a", candidate_groups=["role:finance", "org:d_eng"])
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


class _FakeInstance:
    def __init__(self, start_user: str) -> None:
        self.start_user = start_user


class _FakeCtx:
    """contrib 词汇所需的最小上下文（instance.start_user + variables）。"""

    def __init__(self, start_user: str, variables: dict[str, str] | None = None) -> None:
        self.instance = _FakeInstance(start_user)
        self.variables = variables or {}


def test_contrib_has_role_predicate():
    """contrib has_role(one_user, string)→bool：谓词语义（条件位）。"""
    functions = contrib.org_vocabulary()
    ctx = _FakeCtx("u_bob")
    assert functions.call("has_role", ctx, ORG, ["u_bob", "eng_lead"]) is True
    assert functions.call("has_role", ctx, ORG, ["u_zhang", "eng_lead"]) is False


@pytest.mark.parametrize("subject", ["role:finance", "role:nope"])
def test_contrib_manager_of_rejects_role(subject):
    functions = contrib.org_vocabulary()
    ctx = _FakeCtx("u_x", {"x": subject})
    with pytest.raises(Exception, match="角色主体"):
        functions.call("manager_of", ctx, ORG, ["x"])
