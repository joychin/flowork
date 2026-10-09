"""套件自建组织世界：一份规格，两种物化。

- 持久化 DirectoryStore（独立文件库）：部门树 CRUD、范围化角色、Agent 一等身份；
- 静态 OrgDirectory（内存）：同一批人马 + 委托/管理员（持久化版此两项为占位）。

人设：华信智造 hq —— 总经办 ga / 研发中心 rd（后端 rd_be、前端 rd_fe）/
财务部 fin（会计组 fin_gl）/ 采购部 pur。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from flowork.directory.store import DirectoryStore
from flowork.participants.org_directory import Delegation, OrgDirectory
from flowork.persistence.db import create_db_engine, file_url, session_factory
from tests.suite.world_org_blueprint import (
    ORG_UNITS_BLUEPRINT,
    ROLE_ASSIGNMENTS_BLUEPRINT,
    ROLES_BLUEPRINT,
    USERS_BLUEPRINT,
)

# —— 部门树：(code, 名称, 上级, 部门主管) ——
ORG_UNITS: list[tuple[str, str, str | None, str | None]] = [
    ("hq", "华信智造", None, "u_ceo"),
    ("ga", "总经办", "hq", "u_ceo"),
    ("rd", "研发中心", "hq", "u_cto"),
    ("rd_be", "后端组", "rd", "u_lead_be"),
    ("rd_fe", "前端组", "rd", "u_lead_fe"),
    ("fin", "财务部", "hq", "u_cfo"),
    ("fin_gl", "会计组", "fin", "u_chief_acc"),
    ("pur", "采购部", "hq", "u_purch_head"),
    *ORG_UNITS_BLUEPRINT,  # 金赛 + BPO 两棵树（真实蓝图世界）
]

# —— 用户：(id, 姓名, 主属部门) ——
USERS: list[tuple[str, str, str]] = [
    ("u_ceo", "张一诺", "ga"),
    ("u_cto", "李工", "rd"),
    ("u_lead_be", "王工", "rd_be"),
    ("u_lead_fe", "赵敏", "rd_fe"),
    ("u_dev_wu", "吴开发", "rd_be"),
    ("u_dev_zheng", "郑开发", "rd_be"),
    ("u_dev_liu", "刘开发", "rd_fe"),
    ("u_cfo", "钱多多", "fin"),
    ("u_chief_acc", "孙会计", "fin_gl"),
    ("u_cash", "周出纳", "fin_gl"),
    ("u_purch_head", "吴总", "pur"),
    ("u_buyer", "郑采购", "pur"),
    ("u_compliance", "王合规", "pur"),
    *USERS_BLUEPRINT,
]

# —— 角色：(code, 名称, 默认范围)；授权：(用户, 角色, 显式范围|None=用默认) ——
ROLES: list[tuple[str, str, str | None]] = [
    ("ceo", "决策人", None),
    ("dept_manager", "部门负责人", None),
    ("finance_approver", "财务审批人", None),
    ("compliance_officer", "合规专员", "pur"),
    *ROLES_BLUEPRINT,
]
ROLE_ASSIGNMENTS: list[tuple[str, str, str | None]] = [
    ("u_ceo", "ceo", None),
    ("u_cto", "dept_manager", "rd"),
    ("u_cfo", "dept_manager", "fin"),
    ("u_purch_head", "dept_manager", "pur"),
    ("u_chief_acc", "finance_approver", None),
    ("u_cash", "finance_approver", None),
    ("u_compliance", "compliance_officer", None),
    *ROLE_ASSIGNMENTS_BLUEPRINT,
]

# —— Agent：(id, 名称, 问责 owner, 部门, 代言用户) ——
AGENTS: list[tuple[str, str, str, str, str]] = [
    ("ag_expense_bot", "报销助手", "u_cash", "fin_gl", "u_chief_acc"),
]

FINANCE_APPROVERS = ["u_chief_acc", "u_cash"]
DELEGATION = ("u_lead_fe", "u_lead_be")  # 赵敏休假 → 王工代办
ADMINS = ["u_ceo"]


def build_directory_store(db_path: Path) -> DirectoryStore:
    """物化一：持久化目录（独立文件库，与引擎库完全分离）。"""
    store = DirectoryStore(session_factory(create_db_engine(file_url(db_path))))
    for code, name, parent, manager in ORG_UNITS:
        store.create_org_unit(code, name=name, parent_code=parent, manager_user_id=manager)
    for user_id, name, dept in USERS:
        store.create_user(user_id, name=name, org_unit_code=dept)
    for code, name, scope in ROLES:
        store.create_role(code, name=name, default_scope_org_unit=scope)
    for user_id, role, scope in ROLE_ASSIGNMENTS:
        store.assign_role(user_id, role, org_unit_code=scope)
    for agent_id, name, owner, dept, on_behalf in AGENTS:
        store.register_agent(
            agent_id,
            name=name,
            owner_user_id=owner,
            org_unit_code=dept,
            on_behalf_of_user=on_behalf,
        )
    return store


def build_static_org() -> OrgDirectory:
    """物化二：静态目录（同批人马 + 委托 + 管理员）。"""
    head_of = {code: manager for code, _name, _parent, manager in ORG_UNITS}
    users: dict[str, dict[str, object]] = {}
    for user_id, _name, dept in USERS:
        users[user_id] = {
            "department": dept,
            "manager": head_of.get(dept),
            "roles": [role for holder, role, _scope in ROLE_ASSIGNMENTS if holder == user_id],
        }
    departments = {
        code: {"head": manager, "parent": parent} for code, _name, parent, manager in ORG_UNITS
    }
    return OrgDirectory(
        users=users,
        departments=departments,
        delegations=[
            Delegation(
                delegate_from=DELEGATION[0], delegate_to=DELEGATION[1], until=date(2099, 12, 31)
            )
        ],
        admins=list(ADMINS),
    )
