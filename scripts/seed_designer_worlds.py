"""把 tests/suite 的测试世界流程灌入设计器数据库（wf_designer_draft 工作副本）。

套件流程以 Python DSL 定义在 tests/suite/world_flows*.py，设计器只认数据库——
本脚本把选中世界 build 后 update_draft，设计器列表即可读取与编辑（节点缺
layout 时前端自动分层布局，无需预置坐标）。

用法：
    uv run python scripts/seed_designer_worlds.py              # 九个业务流程（缺省）
    uv run python scripts/seed_designer_worlds.py --all        # 全部世界（含构造矩阵）
    uv run python scripts/seed_designer_worlds.py suite_expense suite_purchase
    uv run python scripts/seed_designer_worlds.py --db /tmp/f.db

幂等可重跑：update_draft 就地覆盖。DB 解析与 web 后端同源（--db > FLOWORK_DB > designer.db）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# 业务流程世界（真实蓝图六世界 + 套件经典三件）；构造矩阵/故障注入世界走 --all
BUSINESS_CODES = [
    "suite_expense",
    "suite_supplier_check",
    "suite_purchase",
    "suite_material_master",
    "suite_task_publish",
    "suite_onboard_task",
    "suite_contract_sign",
    "suite_receipt",
    "suite_payment",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="*", help="世界 code（缺省=九个业务流程）")
    parser.add_argument("--all", action="store_true", help="全部世界（含构造矩阵/故障注入）")
    parser.add_argument("--db", default=None, help="SQLite 文件（缺省 FLOWORK_DB/designer.db）")
    args = parser.parse_args()

    from flowork.api import EngineConfig, ProcessEngine
    from tests.suite.world_catalog import BUILDERS, build_definition

    codes = sorted(BUILDERS) if args.all else (args.codes or BUSINESS_CODES)
    unknown = [code for code in codes if code not in BUILDERS]
    if unknown:
        print(f"未知世界 code：{unknown}；可选：{sorted(BUILDERS)}")
        return 1

    db_path = Path(args.db or os.environ.get("FLOWORK_DB", "designer.db"))
    engine = ProcessEngine(EngineConfig(db_file=db_path))
    try:
        for code in codes:
            definition = build_definition(code)
            seq = engine.repository.update_draft(definition)
            print(f"{code:<26} {definition.name:<14} 草稿 #{seq} → {db_path}")
    finally:
        engine.close()
    print(f"共 {len(codes)} 个世界已写入（设计器刷新列表即可见）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
