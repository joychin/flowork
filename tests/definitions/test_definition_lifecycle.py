"""P1-6（kernel-v2 spec §6.5/§16）：定义生命周期测试——retire/purge/drift/版本过滤。

验收：purge 拒绝被引用版本；drift 三清单；retired 不接新实例（报
DefinitionRetired，重新 deploy 恢复）；「多少在跑实例钉旧版」可查。
"""

from __future__ import annotations

import json

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import DefinitionNotPublishedError, DefinitionRetiredError


def _linear(code: str = "lc"):
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )


def test_retire_blocks_new_instances_and_redeploy_restores():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    engine.repository.retire("lc", actor="admin", reason="下线")

    with pytest.raises(DefinitionRetiredError):
        engine.runtime.start_process("lc", start_user="u_s")

    revised = _linear("lc")
    revised.name = "重新上线"
    verdict = engine.repository.deploy(revised)  # 恢复＝重新 deploy 为新版本
    assert (verdict.next_major, verdict.next_revision) == (1, 2)
    engine.runtime.start_process("lc", start_user="u_s")  # 新实例可发


def test_retire_keeps_inflight_running():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    instance_id = engine.runtime.start_process("lc", start_user="u_s")
    engine.repository.retire("lc", actor="admin")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")  # 在跑跑完
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_purge_requires_force_and_rejects_referenced():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    with pytest.raises(ValueError, match="force"):
        engine.repository.purge("lc")

    engine.runtime.start_process("lc", start_user="u_s")  # 有实例引用
    with pytest.raises(DefinitionNotPublishedError, match="引用"):
        engine.repository.purge("lc", force=True)

    # 无引用场景：换 code 部署后立即 purge
    engine.repository.deploy(_linear("lc2"))
    assert engine.repository.purge("lc2", force=True) == 1


def test_definition_drift_three_lists(tmp_path):
    engine = ProcessEngine()
    engine.repository.deploy(_linear("kept"))  # 文件=库
    engine.repository.deploy(_linear("changed"))  # 库有，文件将改内容 → 库落后
    engine.repository.deploy(_linear("orphan"))  # 库有，目录无文件 → 库多余

    from flowork.definitions.definition import dump_definition

    (tmp_path / "kept.json").write_text(
        json.dumps(dump_definition(_linear("kept"))), encoding="utf-8"
    )
    changed = _linear("changed")
    changed.name = "改过了"
    (tmp_path / "changed.json").write_text(json.dumps(dump_definition(changed)), encoding="utf-8")
    fresh = _linear("fresh")
    (tmp_path / "fresh.json").write_text(json.dumps(dump_definition(fresh)), encoding="utf-8")

    drift = engine.repository.definition_drift(str(tmp_path))
    assert [item["code"] for item in drift["undeployed"]] == ["fresh"]
    assert [item["code"] for item in drift["extra_in_db"]] == ["orphan"]
    assert [item["code"] for item in drift["stale_in_db"]] == ["changed"]


def test_instances_filterable_by_version():
    """「多少在跑实例钉旧版」：按 (code, major, revision) 过滤实例（spec §6.5）。"""
    engine = ProcessEngine()
    engine.repository.deploy(_linear("vf"))
    first = engine.runtime.start_process("vf", start_user="u_s")

    revised = _linear("vf")
    revised.name = "修订"
    engine.repository.deploy(revised)
    second = engine.runtime.start_process("vf", start_user="u_s")

    old = engine.runtime.list_instances(definition_code="vf", major=1, revision=1)
    new = engine.runtime.list_instances(definition_code="vf", major=1, revision=2)
    assert [i.id for i in old] == [first]
    assert [i.id for i in new] == [second]
