"""一次性迁移：gateway 三态网关 → branch 条件分支 + advance_policy 开始条件。

规则（语义对照见 out/网关去技术化改造计划.md）：
- exclusive/inclusive（仅分流角色）：保留节点，kind→branch，route_mode=first_match/all_match；
- 并行分流（parallel，单入多出）：删除节点，出边改挂来源并继承入边条件；
  来源若为 first_match 分支且由此获得 ≥2 条改挂出边 → 升级 all_match（保并行扇出）；
- 汇聚角色（≥2 入 1 出，任意 kind）：删除节点，入边直连后继；
  parallel→后继 advance_policy="all"，exclusive/inclusive→"any"；
- 收尾：≥2 入边节点未显式设 advance_policy → 补 "any"（缺省语义显式化，消除告警）。

用法：uv run python scripts/migrate_gateway_to_branch.py [json 路径…]
默认迁移 examples/**/*.json 与 designer.db(wf_process_definition.content)。幂等可重跑。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _is_first_match(node: dict) -> bool:
    if node.get("kind") == "branch":
        return node.get("route_mode", "first_match") == "first_match"
    return node.get("kind") == "gateway" and node.get("gateway_kind", "exclusive") == "exclusive"


def migrate_definition(doc: dict) -> tuple[dict, bool]:
    """迁移一份定义 JSON，返回 (新文档, 是否有变更)。"""
    nodes: list[dict] = doc.get("nodes") or []
    flows: list[dict] = doc.get("flows") or []
    gateways = [n for n in nodes if n.get("kind") == "gateway"]
    if not gateways:
        return doc, False

    node_by_code = {n["code"]: n for n in nodes}

    # 角色按原图度数分类（汇聚删除会改挂入边，必须先分类后变换）
    roles: dict[str, tuple[str, list[dict], list[dict]]] = {}
    for gw in gateways:
        gk = gw.get("gateway_kind", "exclusive")
        code = gw["code"]
        in_edges = [f for f in flows if f.get("target") == code]
        out_edges = [f for f in flows if f.get("source") == code]
        if len(in_edges) >= 2 and len(out_edges) == 1:
            role = "join"
        elif len(in_edges) == 1 and len(out_edges) >= 1:
            role = "split_parallel" if gk == "parallel" else "split_conditional"
        else:
            raise SystemExit(
                f"无法迁移混合角色网关：{code}（入 {len(in_edges)} / 出 {len(out_edges)} / {gk}）"
            )
        roles[code] = (role, in_edges, out_edges)

    for gw in gateways:
        gk = gw.pop("gateway_kind", "exclusive")
        code = gw["code"]
        role, in_edges, out_edges = roles[code]
        join_role = role == "join"
        parallel_split = role == "split_parallel"

        if join_role:
            # 汇聚角色：入边直连唯一后继，后继落开始条件
            target = out_edges[0]["target"]
            for edge in in_edges:
                edge["target"] = target
            flows.remove(out_edges[0])
            nodes.remove(gw)
            policy = "all" if gk == "parallel" else "any"
            node_by_code[target]["advance_policy"] = policy
            print(f"  - 删除汇聚网关 {code} → {target}.advance_policy={policy}")
        elif parallel_split:
            # 并行分流：出边改挂来源，继承入边条件；first_match 来源升级 all_match
            src_edge = in_edges[0]
            src = src_edge["source"]
            for edge in out_edges:
                edge["source"] = src
                if src_edge.get("condition") is not None and edge.get("condition") is None:
                    edge["condition"] = src_edge["condition"]
                if src_edge.get("is_default") and len(out_edges) == 1:
                    edge["is_default"] = True
            flows.remove(src_edge)
            nodes.remove(gw)
            src_node = node_by_code.get(src)
            if src_node is not None and len(out_edges) >= 2 and _is_first_match(src_node):
                src_node["_promote_all_match"] = True
            print(f"  - 删除并行分流 {code} → {len(out_edges)} 条出边改挂 {src}")
        else:
            # 分流角色：原节点升级为条件分支
            gw["kind"] = "branch"
            gw["route_mode"] = "first_match" if gk == "exclusive" else "all_match"
            print(f"  - 网关 {code} → branch({gw['route_mode']})")

    # 因并行出边改挂而升级的 first_match 分支（仅限显式标记，避免误伤互斥多臂分支）
    for node in nodes:
        if node.pop("_promote_all_match", False):
            node["route_mode"] = "all_match"
            print(f"  - 分支 {node['code']} first_match → all_match（并行扇出改挂）")

    # ≥2 入边节点显式补 any（缺省语义显式化）
    in_deg: dict[str, int] = {}
    for f in flows:
        in_deg[f["target"]] = in_deg.get(f["target"], 0) + 1
    for node in nodes:
        if in_deg.get(node["code"], 0) >= 2 and not node.get("advance_policy"):
            node["advance_policy"] = "any"

    doc["nodes"] = nodes
    doc["flows"] = flows
    return doc, True


def migrate_json_file(path: Path) -> bool:
    doc = json.loads(path.read_text(encoding="utf-8"))
    new_doc, changed = migrate_definition(doc)
    if changed:
        path.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[json] {path}")
    return changed


def migrate_sqlite(db: Path) -> None:
    if not db.exists():
        print(f"[db] 跳过（不存在）：{db}")
        return
    backup = db.with_name(db.name + ".bak-gateway")
    if not backup.exists():
        backup.write_bytes(db.read_bytes())
        print(f"[db] 已备份 → {backup}")
    conn = sqlite3.connect(db)
    rows = conn.execute("select id, code, version, content from wf_process_definition").fetchall()
    for row_id, code, version, content in rows:
        doc = json.loads(content)
        new_doc, changed = migrate_definition(doc)
        if changed:
            conn.execute(
                "update wf_process_definition set content=? where id=?",
                (json.dumps(new_doc, ensure_ascii=False), row_id),
            )
            print(f"[db] {code} v{version}")
    conn.commit()
    conn.close()


def main() -> None:
    targets = [Path(a) for a in sys.argv[1:]] or sorted((REPO / "examples").glob("**/*.json"))
    for path in targets:
        migrate_json_file(path)
    migrate_sqlite(REPO / "designer.db")


if __name__ == "__main__":
    main()
