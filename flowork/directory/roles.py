"""角色与范围化授权。

范围解析规则（docs/product/engine-v1/01-身份与组织模型.md §2）：
发起人部门链自下而上（含自身）逐层找**显式/默认范围命中该层**的授权，最近命中层胜出
（同层多人全部返回，会签与否由流程定义决定）；链上无命中回落到全局授权；
无上下文（within=None）返回全部持有人。有效范围 = 授权行显式范围 ?? 角色默认范围。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flowork.directory.common import DirectoryError, _validate_code
from flowork.directory.orm import OrgUnitRow, OrgUserRow, RoleAssignmentRow, RoleRow

_MAX_DEPTH = 1000


class RoleDirectoryMixin:
    """挂到 DirectoryStore 上提供角色域方法（依赖 _factory/_unit/_user）。"""

    _factory: sessionmaker[Session]

    def _unit(self, session: Session, code: str) -> OrgUnitRow:
        raise NotImplementedError

    def _user(self, session: Session, user_id: str) -> OrgUserRow:
        raise NotImplementedError

    def _write_done(self) -> None:
        raise NotImplementedError

    # —— 写 ——

    def create_role(
        self,
        code: str,
        *,
        name: str = "",
        default_scope_org_unit: str | None = None,
        acted_by: str = "system",
    ) -> None:
        from flowork.directory.audit import record_audit

        _validate_code("角色", code)
        with self._factory() as session, session.begin():
            if session.get(RoleRow, code) is not None:
                raise DirectoryError(f"角色 {code!r} 已存在")
            if default_scope_org_unit is not None:
                self._unit(session, default_scope_org_unit)
            session.add(
                RoleRow(code=code, name=name, default_scope_org_unit=default_scope_org_unit)
            )
            record_audit(
                session,
                "role.create",
                f"role:{code}",
                {"default_scope": default_scope_org_unit},
                acted_by=acted_by,
            )
        self._write_done()

    def assign_role(
        self,
        user_id: str,
        role_code: str,
        *,
        org_unit_code: str | None = None,
        acted_by: str = "system",
    ) -> None:
        from flowork.directory.audit import record_audit

        with self._factory() as session, session.begin():
            role = session.get(RoleRow, role_code)
            if role is None:
                raise DirectoryError(f"角色 {role_code!r} 不存在")
            self._user(session, user_id)
            if org_unit_code is not None:
                self._unit(session, org_unit_code)
            effective = org_unit_code if org_unit_code is not None else role.default_scope_org_unit
            existing = session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.role_code == role_code,
                    RoleAssignmentRow.user_id == user_id,
                )
            ).all()
            for row in existing:
                row_effective = (
                    row.org_unit_code
                    if row.org_unit_code is not None
                    else role.default_scope_org_unit
                )
                if row_effective == effective:
                    raise DirectoryError(f"用户 {user_id!r} 已持有角色 {role_code!r} 的该范围授权")
            session.add(
                RoleAssignmentRow(user_id=user_id, role_code=role_code, org_unit_code=org_unit_code)
            )
            record_audit(
                session,
                "role.assign",
                f"role:{role_code}",
                {"user": user_id, "scope": effective},
                acted_by=acted_by,
            )
        self._write_done()

    # —— 读 ——

    def users_with_role(self, role: str, *, within: str | None = None) -> list[str]:
        with self._factory() as session:
            role_row = session.get(RoleRow, role)
            if role_row is None:
                return []
            default_scope = role_row.default_scope_org_unit
            assignments = session.scalars(
                select(RoleAssignmentRow)
                .where(RoleAssignmentRow.role_code == role)
                .order_by(RoleAssignmentRow.id)
            ).all()
            active = self._active_users_of(session, [row.user_id for row in assignments])

            def effective(row: RoleAssignmentRow) -> str | None:
                return row.org_unit_code if row.org_unit_code is not None else default_scope

            if within is None:
                ordered = list(dict.fromkeys(row.user_id for row in assignments))
                return [u for u in ordered if u in active]
            chain = self._ancestor_chain(session, within)
            for level in chain:
                hits = [row.user_id for row in assignments if effective(row) == level]
                hits = [u for u in hits if u in active]
                if hits:
                    return list(dict.fromkeys(hits))
            fallback = [row.user_id for row in assignments if effective(row) is None]
            return list(dict.fromkeys(u for u in fallback if u in active))

    # —— 内部 ——

    def _active_users_of(self, session: Session, user_ids: list[str]) -> set[str]:
        """名单里的在册且未停用用户：与 org: 侧
        同口径——此前 role: 组解析不看状态，停用员工经角色候选仍可认领。"""
        if not user_ids:
            return set()
        rows = session.scalars(
            select(OrgUserRow.user_id).where(
                OrgUserRow.user_id.in_(user_ids), OrgUserRow.status == "active"
            )
        ).all()
        return set(rows)

    def _ancestor_chain(self, session: Session, department: str) -> list[str]:
        chain: list[str] = []
        current: str | None = department
        for _ in range(_MAX_DEPTH):
            if current is None:
                return chain
            chain.append(current)
            row = session.get(OrgUnitRow, current)
            if row is None:
                raise DirectoryError(f"部门链上的 {current!r} 不存在（数据损坏）")
            current = row.parent_code
        raise DirectoryError("部门上级链超过最大深度（疑似数据损坏）")
