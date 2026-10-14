"""随机动作风暴（randomized，默认排除）：任意动作组合都能被确定性办完。

结构固定（knot 会签×驳回×三档时限 / expense 多级审批），随机的是行为——
转办/协办/咨询/加签/撤回/暂停/终止/时钟推进与投票交错。三层 oracle：
① 合法动作只从服务端 available_actions 挑，成功或 FloworkError 领域拒绝；
② 非法探针（不在可用集的动作）必须被 TaskNotAvailableError 拒；
③ 每步账本不变量（批次一）+ 清场后全库 assert_full_ledger。
失败时三段日志（种子+世界+动作序列），FLOWORK_RANDOM_ITER 控制迭代数（默认 30）。
"""

from __future__ import annotations

import os

import pytest

from flowork.engine.engine import ProcessEngine
from tests.suite.invariants import TERMINAL_INSTANCE_STATES, assert_full_ledger
from tests.suite.storm import drain, run_storm

BASE_SEED = 20261014

WORLDS = [
    pytest.param("suite_countersign_knot", "u_dev_wu", {"note": "风暴"}, id="knot"),
    pytest.param(
        "suite_expense", "u_dev_wu", {"amount": 8000, "expense_type": "office"}, id="expense"
    ),
]


def _iter_count() -> int:
    return int(os.environ.get("FLOWORK_RANDOM_ITER", "30"))


@pytest.mark.randomized
@pytest.mark.parametrize("world_code,start_user,variables", WORLDS)
def test_random_storm_converges(
    suite_engine: ProcessEngine, world_code: str, start_user: str, variables: dict
) -> None:
    """任意风暴：随机相打完 → drain 确定性清场 → 终态+全库账本。"""
    engine = suite_engine
    for offset in range(_iter_count()):
        seed = BASE_SEED + offset
        iid, _ = run_storm(
            engine,
            seed=seed,
            world_code=world_code,
            start_user=start_user,
            variables=dict(variables),
        )
        try:
            drain(engine, iid)
        except Exception as exc:  # 清场失败同样转三段日志
            raise AssertionError(
                f"清场失败 seed={seed} world={world_code} iid={iid}：{exc!r}"
            ) from exc
        assert engine.runtime.get_instance(iid).state in TERMINAL_INSTANCE_STATES, (
            f"seed={seed} 清场后未终态"
        )
    assert_full_ledger(engine)  # 含本测试累计的全部风暴实例
