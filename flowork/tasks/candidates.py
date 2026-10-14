"""候选制：候选关联的命中判定与查询。

候选名单（``wf_identity_link``，link_type=candidate）混存两种东西：具体人 id
（含 ``user:`` 前缀写法）与**显式**组引用（``role:`` / ``org:`` 前缀；
设计期 candidate_groups.bare_reference 强制——「角色就是角色，人名就是
人名」：裸名只按人解释，不再溢出为角色名）。**组引用保留不展开**（「两个槽不可拍平」：
明天入组的人能认领明天的待办），在认领与查询时逐条问目录归属——这是槽位表
「groups 保留引用，认领时展开」的明文行为（此前引擎从未实现，``is_member`` 端口
全仓只有 contrib 词汇在调）。

名单**内容**来自候选槽的 CEL 求值：
``candidate_users`` / ``candidate_groups`` 每项是公式，与取人位同一求值面
（`participants.resolver.resolve_candidate_subjects`）——本模块只管**判定**。

判定两式：① 人 key 对齐（剥 ``user:`` 前缀后相等，两种写法互认；**裸名唯一
解释即人名**）；② 显式组引用的目录归属（``role:``/``org:``；目录未配置时该式
恒假——内核不认组，组候选无目录即无人可认领）。
认领守卫与候选查询维度共用本实现——两处各写一份必然漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from flowork.errors import TaskNotAvailableError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "CandidateDirectory",
    "candidate_hit",
    "candidate_links",
    "person_key",
    "require_candidate",
]


class CandidateDirectory(Protocol):
    """候选判定所需的目录面（OrgDirectoryLike 的结构子集，鸭子类型）。"""

    def is_member(self, user: str, group: str) -> bool: ...


def person_key(subject: str) -> str:
    """人 key：剥 ``user:`` 前缀（前缀写法与裸 id 互认）。

    主体一律裸 id（``agent:`` 写法已零兼容删除——AI 与人同路，id 不分命名
    空间），只有 ``user:`` 是同一个人的两种写法。
    """
    return subject.removeprefix("user:")


def candidate_hit(user: str, candidate: str, org: CandidateDirectory | None) -> bool:
    """候选命中：① 人 key 对齐，或 ② 显式组引用的目录归属（无目录恒假）。

    槽位定解释（「角色就是角色，人名就是
    人名」）——**裸名只按人解释**：此前裸名会同时被当角色名问目录（"裸名
    视同 role" 约定溢出到人名单），与人撞名的角色会让认领面静默扩大。
    组一律显式 ``role:`` / ``org:`` 前缀（设计期 candidate_groups.bare_reference
    强制），混合集公式返回值须自带前缀。
    """
    key = person_key(user)
    if key == person_key(candidate):
        return True
    if not candidate.startswith(("role:", "org:")):
        return False  # 裸名＝人名（唯一解释），不再溢出为角色名
    return org is not None and org.is_member(key, candidate)


def candidate_links(session: Session, task_id: str) -> list[str]:
    """任务的候选关联原样（人 id 与组引用混存；保留引用不展开）。"""
    from sqlalchemy import select

    from flowork.persistence.orm import IdentityLinkRow

    stmt = select(IdentityLinkRow).where(IdentityLinkRow.task_id == task_id)
    return [row.user_id for row in session.scalars(stmt)]


def require_candidate(
    session: Session, task_id: str, user: str, org: CandidateDirectory | None
) -> None:
    """认领守卫：命中放行，未命中拒绝（缺目录而候选是显式组引用时点明原因）。"""
    links = candidate_links(session, task_id)
    if any(candidate_hit(user, candidate, org) for candidate in links):
        return
    groups = [c for c in links if c.startswith(("role:", "org:"))]
    if groups and org is None:
        raise TaskNotAvailableError(
            f"用户 {user} 不是任务 {task_id} 的候选人"
            f"（候选含组引用 {groups}，但未配置组织目录——EngineConfig.org）"
        )
    raise TaskNotAvailableError(f"用户 {user} 不是任务 {task_id} 的候选人")


def candidate_task_ids(session: Session, user: str, org: CandidateDirectory | None) -> list[str]:
    """候选查询维度：全部候选关联里命中该用户的任务 id（未去重）。

    全表走关联行——候选量级小，且本维度先落 Python API（索引化随 REST /v2 搜索面）。
    """
    from sqlalchemy import select

    from flowork.persistence.orm import IdentityLinkRow

    return [
        row.task_id
        for row in session.scalars(select(IdentityLinkRow))
        if candidate_hit(user, row.user_id, org)
    ]
