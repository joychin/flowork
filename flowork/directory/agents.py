"""Agent 一等身份（M-A 第三刀，DIR-05）。

Agent 是目录里的一等 principal：负责人（问责链 root）、所属部门、可选"代表某人"
（on_behalf_of，实例归因随 M-B RUN-01 落地）。凭证一次性明文、scrypt 哈希存储、
verify 每次读当前状态——停用即时失效（US-104 GWT②）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from flowork.clock import utc_now
from flowork.directory.common import DirectoryError, _validate_code
from flowork.directory.orm import AgentCredentialRow, AgentRow, OrgUnitRow, OrgUserRow

_TOKEN_PREFIX = "fwk-ag-"
_ACTIVE_STATUSES = {"trial", "active"}
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


@dataclass(frozen=True)
class AgentInfo:
    agent_id: str
    name: str
    owner_user_id: str
    org_unit_code: str | None
    on_behalf_of_user: str | None
    status: str


def _hash_secret(secret: str, salt: str) -> str:
    digest = hashlib.scrypt(
        secret.encode(), salt=salt.encode(), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return digest.hex()


class AgentDirectoryMixin:
    """挂到 DirectoryStore 上提供 Agent 域方法（依赖 _factory/_unit/_user）。"""

    _factory: sessionmaker[Session]

    def _unit(self, session: Session, code: str) -> OrgUnitRow:
        raise NotImplementedError

    def _user(self, session: Session, user_id: str) -> OrgUserRow:
        raise NotImplementedError

    # —— 注册与状态 ——

    def register_agent(
        self,
        agent_id: str,
        *,
        name: str = "",
        owner_user_id: str,
        org_unit_code: str | None = None,
        on_behalf_of_user: str | None = None,
    ) -> AgentInfo:
        _validate_code("Agent", agent_id)
        with self._factory() as session, session.begin():
            if session.get(AgentRow, agent_id) is not None:
                raise DirectoryError(f"Agent {agent_id!r} 已存在")
            self._user(session, owner_user_id)
            if org_unit_code is not None:
                self._unit(session, org_unit_code)
            if on_behalf_of_user is not None:
                self._user(session, on_behalf_of_user)
            session.add(
                AgentRow(
                    agent_id=agent_id,
                    name=name,
                    owner_user_id=owner_user_id,
                    org_unit_code=org_unit_code,
                    on_behalf_of_user=on_behalf_of_user,
                    status="trial",
                )
            )
        return AgentInfo(agent_id, name, owner_user_id, org_unit_code, on_behalf_of_user, "trial")

    def agent_info(self, agent_id: str) -> AgentInfo:
        with self._factory() as session:
            row = self._agent(session, agent_id)
            return self._info(row)

    def agent_owner(self, agent_id: str) -> str | None:
        with self._factory() as session:
            row = session.get(AgentRow, agent_id)
            return row.owner_user_id if row else None

    def activate_agent(self, agent_id: str) -> None:
        self._transition(agent_id, "active", from_={"trial"})

    def suspend_agent(self, agent_id: str) -> None:
        self._transition(agent_id, "suspended", from_={"trial", "active"})

    def resume_agent(self, agent_id: str) -> None:
        self._transition(agent_id, "active", from_={"suspended"})

    # —— 凭证 ——

    def issue_credential(self, agent_id: str) -> str:
        """签发凭证：明文仅此一次返回，库中只存 scrypt 哈希。"""
        secret = secrets.token_urlsafe(24)
        salt = secrets.token_hex(8)
        cred_id = uuid.uuid4().hex[:12]
        with self._factory() as session, session.begin():
            row = self._agent(session, agent_id)
            if row.status not in _ACTIVE_STATUSES:
                raise DirectoryError(f"Agent {agent_id!r} 已停用，不可签发凭证")
            session.add(
                AgentCredentialRow(
                    id=cred_id,
                    agent_id=agent_id,
                    secret_hash=_hash_secret(secret, salt),
                    salt=salt,
                )
            )
        return f"{_TOKEN_PREFIX}{cred_id}-{secret}"

    def verify_credential(self, token: str) -> str | None:
        """校验凭证，返回 agent_id；未知/已吊销/已停用一律 None（不泄露原因）。"""
        remainder = token.removeprefix(_TOKEN_PREFIX)
        cred_id, _, secret = remainder.partition("-")
        if not cred_id or not secret:
            return None
        with self._factory() as session:
            cred = session.get(AgentCredentialRow, cred_id)
            if cred is None or cred.revoked_at is not None:
                return None
            expected = _hash_secret(secret, cred.salt)
            if not hmac.compare_digest(expected, cred.secret_hash):
                return None
            agent = session.get(AgentRow, cred.agent_id)
            if agent is None or agent.status not in _ACTIVE_STATUSES:
                return None
            return agent.agent_id

    def revoke_credential(self, token: str) -> None:
        remainder = token.removeprefix(_TOKEN_PREFIX)
        cred_id, _, _ = remainder.partition("-")
        with self._factory() as session, session.begin():
            cred = session.get(AgentCredentialRow, cred_id)
            if cred is not None and cred.revoked_at is None:
                cred.revoked_at = utc_now()

    # —— 内部 ——

    def _agent(self, session: Session, agent_id: str) -> AgentRow:
        row = session.get(AgentRow, agent_id)
        if row is None:
            raise DirectoryError(f"Agent {agent_id!r} 不存在")
        return row

    def _transition(self, agent_id: str, to_status: str, *, from_: set[str]) -> None:
        with self._factory() as session, session.begin():
            row = self._agent(session, agent_id)
            if row.status not in from_:
                raise DirectoryError(f"Agent {agent_id!r} 状态 {row.status} 不可转为 {to_status}")
            row.status = to_status

    @staticmethod
    def _info(row: AgentRow) -> AgentInfo:
        return AgentInfo(
            row.agent_id,
            row.name,
            row.owner_user_id,
            row.org_unit_code,
            row.on_behalf_of_user,
            row.status,
        )
