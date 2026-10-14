"""审批委托（engine-v1 01 §5）：org_delegation 表 + 查询面。

语义：``delegate_from`` 在 ``[since, until]`` 窗口内的待办转给 ``delegate_to``
（resolver 的 ``resolve_with_delegation`` 单跳替换，不递归）；窗口端点 None=
该侧无限；同 from 多条有效时最新一条胜。撤销＝置 ``revoked_at``（留痕）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flowork.clock import utc_now
from flowork.directory.audit import record_audit
from flowork.directory.common import DirectoryError
from flowork.directory.orm import DelegationRow, OrgUserRow


@dataclass(frozen=True)
class DelegationInfo:
    """一条委托的展示形状（delegations_of 查询面）。"""

    delegate_from: str
    delegate_to: str
    since: datetime | None
    until: datetime | None
    reason: str
    revoked: bool


class DelegationDirectoryMixin:
    """挂到 DirectoryStore 上提供委托读写（依赖 _factory/_user/_cache/_write_done）。"""

    _factory: sessionmaker[Session]

    def _user(self, session: Session, user_id: str) -> OrgUserRow:
        raise NotImplementedError

    def _write_done(self) -> None:
        raise NotImplementedError

    # —— 写 ——

    def create_delegation(
        self,
        delegate_from: str,
        delegate_to: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        reason: str = "",
        acted_by: str = "system",
    ) -> DelegationInfo:
        if delegate_from == delegate_to:
            raise DirectoryError("委托人与受托人不能是同一人")
        if since is not None and until is not None and since > until:
            raise DirectoryError("委托窗口起点晚于终点")
        with self._factory() as session, session.begin():
            self._user(session, delegate_from)
            self._user(session, delegate_to)
            row = DelegationRow(
                delegate_from=delegate_from,
                delegate_to=delegate_to,
                since=since,
                until=until,
                reason=reason,
            )
            session.add(row)
            session.flush()
            record_audit(
                session,
                "delegation.create",
                f"user:{delegate_from}",
                {"to": delegate_to, "since": str(since), "until": str(until), "reason": reason},
                acted_by=acted_by,
            )
            info = DelegationInfo(delegate_from, delegate_to, since, until, reason, False)
        self._write_done()
        return info

    def revoke_delegation(
        self, delegate_from: str, delegate_to: str, *, acted_by: str = "system"
    ) -> int:
        """撤销该方向最新一条未撤销委托（置 revoked_at 留痕）；返回撤销条数。"""
        with self._factory() as session, session.begin():
            row = session.scalars(
                select(DelegationRow)
                .where(
                    DelegationRow.delegate_from == delegate_from,
                    DelegationRow.delegate_to == delegate_to,
                    DelegationRow.revoked_at.is_(None),
                )
                .order_by(DelegationRow.id.desc())
                .limit(1)
            ).first()
            if row is None:
                raise DirectoryError(f"未找到 {delegate_from} → {delegate_to} 的有效委托")
            row.revoked_at = utc_now()
            record_audit(
                session,
                "delegation.revoke",
                f"user:{delegate_from}",
                {"to": delegate_to},
                acted_by=acted_by,
            )
        self._write_done()
        return 1

    # —— 读（不缓存：窗口随时间滑动，频率低） ——

    def delegation_of(self, user_id: str, when: datetime | None = None) -> str | None:
        """窗口内最新一条有效委托的受托人；无则 None。

        窗口判定下推 SQL：此前固定取最近 50 条
        再内存过滤——历史攒多后当前窗口内的委托可能排在 50 名之外被静默
        漏掉（表现为「委托没生效」）。下推后命中的至多一行，无截断面。
        """
        moment = when or utc_now()
        with self._factory() as session:
            row = session.scalars(
                select(DelegationRow)
                .where(
                    DelegationRow.delegate_from == user_id,
                    DelegationRow.revoked_at.is_(None),
                    (DelegationRow.since.is_(None)) | (DelegationRow.since <= moment),
                    (DelegationRow.until.is_(None)) | (moment <= DelegationRow.until),
                )
                .order_by(DelegationRow.id.desc())
                .limit(1)
            ).first()
            return row.delegate_to if row is not None else None

    def delegations_of(self, user_id: str) -> list[DelegationInfo]:
        """该用户全部委托（含已撤销，倒序）——管理/展示查询面（按人取全，
        截断会让管理面看不见旧委托）。"""
        with self._factory() as session:
            rows = session.scalars(
                select(DelegationRow)
                .where(DelegationRow.delegate_from == user_id)
                .order_by(DelegationRow.id.desc())
            ).all()
            return [
                DelegationInfo(
                    r.delegate_from,
                    r.delegate_to,
                    r.since,
                    r.until,
                    r.reason,
                    r.revoked_at is not None,
                )
                for r in rows
            ]
