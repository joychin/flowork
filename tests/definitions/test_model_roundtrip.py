"""模板 JSON 往返测试：load → dump → load 等价（边界 M0 DoD）。"""

from __future__ import annotations

import json

from flowork.definitions.definition import (
    dump_definition,
    load_definition,
    load_definition_file,
)

TEMPLATES = ["01_simple_approval.json", "02_countersign_branch.json", "03_call_activity.json"]


def test_examples_roundtrip_dict(registry, examples_dir):
    for filename in TEMPLATES:
        definition = load_definition_file(examples_dir / filename, registry)
        again = load_definition(dump_definition(definition), registry)
        assert again == definition, f"{filename} 往返不等价"


def test_examples_roundtrip_json_string(registry, examples_dir):
    for filename in TEMPLATES:
        text = (examples_dir / filename).read_text(encoding="utf-8")
        definition = load_definition(text, registry)
        dumped = json.dumps(dump_definition(definition), ensure_ascii=False)
        assert load_definition(dumped, registry) == definition


def test_nodes_resolved_to_spec_classes(registry, examples_dir):
    """加载后节点应是注册的 Spec 类型（UserTaskSpec 等），而非裸 FlowNode。"""
    from flowork.definitions.builtin import UserTaskSpec

    definition = load_definition_file(examples_dir / "01_simple_approval.json", registry)
    manager = definition.node("manager")
    assert manager is not None
    assert type(manager) is UserTaskSpec
    assert manager.assignee == "u_manager"
