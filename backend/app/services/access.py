"""Access resolution: what can this user do with this repository?

Two independent inputs decide the answer:

1. the **global role** on the user, which sets the ceiling
   (`admin` sees everything; `analyst` may write; `viewer` is read-only), and
2. the **repository grants**, which decide *which* repositories are visible at all.

Grants arrive either directly (`RepositoryAccess.user_id`) or through a team
(`RepositoryAccess.team_id`, optionally narrowed to one repository by a scoped
`TeamMember` row). The effective permission is the strongest of all applicable grants,
capped by the global role.

Every function here is a single small query so authorisation cannot become an N+1 trap.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.base import PERMISSION_ORDER, TEAM_ROLE_ORDER, RepoPermission, TeamRole, UserRole
from app.models.repository import Repository
from app.models.team import RepositoryAccess, Team, TeamMember
from app.models.user import User

# The most a given global role can ever do on a repository.
ROLE_CEILING: dict[UserRole, RepoPermission | None] = {
    UserRole.admin: RepoPermission.admin,
    UserRole.analyst: RepoPermission.write,
    UserRole.viewer: RepoPermission.read,
}

# A team role implies this capability on every repository the team can reach.
TEAM_ROLE_PERMISSION: dict[TeamRole, RepoPermission] = {
    TeamRole.manager: RepoPermission.admin,
    TeamRole.member: RepoPermission.write,
    TeamRole.viewer: RepoPermission.read,
}

# What a team role lets the holder *do about access*, independent of any repository.
TEAM_ROLE_CAPABILITIES: dict[TeamRole, frozenset[str]] = {
    TeamRole.manager: frozenset({"view", "manage_members", "manage_access"}),
    TeamRole.member: frozenset({"view"}),
    TeamRole.viewer: frozenset({"view"}),
}
ALL_CAPABILITIES = frozenset({"view", "manage_members", "manage_access"})

# Whoever brought a repository into the platform can always see and refresh it.
#
# Without this, a newly registered user is locked out of their own account: they may
# call `analyze` (analysts can), the repository is created carrying their `owner_id`, but
# with access granted only through teams they would then be unable to see the thing they
# just added. Ownership is the bootstrap that makes self-service ingestion coherent, and
# it is still capped by the global role like every other path.
OWNER_PERMISSION = RepoPermission.write


def now_utc() -> datetime:
    return datetime.now(UTC)


def _expiry_filter(now: datetime) -> list:
    """SQL predicate matching grants that have not lapsed."""
    return [RepositoryAccess.expires_at.is_(None), RepositoryAccess.expires_at > now]


def strongest(permissions: Iterable[RepoPermission | None]) -> RepoPermission | None:
    """The highest-ranked permission, or None when the set is empty."""
    ranked = [p for p in permissions if p is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda p: PERMISSION_ORDER[p])


def weaker(left: RepoPermission, right: RepoPermission) -> RepoPermission:
    """The lower-ranked of two permissions - used to cap a role by a grant."""
    return left if PERMISSION_ORDER[left] <= PERMISSION_ORDER[right] else right


def cap_by_role(permission: RepoPermission | None, user: User) -> RepoPermission | None:
    """Clamp a grant by the user's global role ceiling."""
    if permission is None:
        return None
    ceiling = ROLE_CEILING.get(user.role)
    if ceiling is None:
        return None
    return permission if PERMISSION_ORDER[permission] <= PERMISSION_ORDER[ceiling] else ceiling


# ----------------------------------------------------------------- user grants
def direct_permission(db: Session, user: User, repository_id: int) -> RepoPermission | None:
    """Strongest unexpired grant addressed to this user directly, plus ownership."""
    grants = (
        db.execute(
            select(RepositoryAccess.permission).where(
                and_(
                    RepositoryAccess.repository_id == repository_id,
                    RepositoryAccess.user_id == user.id,
                    or_(*_expiry_filter(now_utc())),
                )
            )
        )
        .scalars()
        .all()
    )
    permission = strongest(grants)
    # You always retain the repository you brought in yourself - see OWNER_PERMISSION.
    owns = db.execute(
        select(Repository.id).where(
            and_(Repository.id == repository_id, Repository.owner_id == user.id)
        )
    ).first()
    if owns is not None:
        permission = strongest([permission, OWNER_PERMISSION])
    return permission


# ---------------------------------------------------------------- team grants
def _team_grants(
    db: Session, repository_id: int, team_ids: Sequence[int]
) -> dict[int, RepoPermission]:
    """Strongest grant per team for one repository, in a single query."""
    if not team_ids:
        return {}
    grants = db.execute(
        select(RepositoryAccess.team_id, RepositoryAccess.permission).where(
            and_(
                RepositoryAccess.repository_id == repository_id,
                RepositoryAccess.team_id.in_(list(team_ids)),
                or_(*_expiry_filter(now_utc())),
            )
        )
    ).all()
    out: dict[int, RepoPermission] = {}
    for team_id, permission in grants:
        best = out.get(team_id)
        if best is None or PERMISSION_ORDER[permission] > PERMISSION_ORDER[best]:
            out[team_id] = permission
    return out


def team_permissions(db: Session, user: User, repository_id: int) -> list[RepoPermission]:
    """Permissions inherited from the teams the user belongs to."""
    memberships = db.execute(
        select(TeamMember.team_id, TeamMember.role, TeamMember.scoped_repository_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(and_(TeamMember.user_id == user.id, Team.is_active.is_(True)))
    ).all()
    if not memberships:
        return []

    grants = _team_grants(db, repository_id, [m[0] for m in memberships])

    permissions: list[RepoPermission] = []
    for team_id, role, scoped_repo in memberships:
        if scoped_repo is not None and scoped_repo != repository_id:
            continue  # this member's access is pinned to a different repository
        grant = grants.get(team_id)
        if grant is None:
            # The team was never granted this repository, so no team role - not even
            # manager - confers access. A manager administers the team's repositories,
            # which is exactly the set the team was granted.
            continue
        # The team's grant is the ceiling: a manager whose team only has `read` reaches
        # the repository as a reader (they still manage the team in `manage_access`),
        # and a plain member never administers a repository however the team is granted.
        permissions.append(weaker(TEAM_ROLE_PERMISSION[role], grant))
    return permissions


# ----------------------------------------------------------- effective access
def effective_permission(db: Session, user: User, repository_id: int) -> RepoPermission | None:
    """The user's effective permission, or None when they cannot see the repository."""
    if user.role == UserRole.admin:
        return RepoPermission.admin
    candidates = [direct_permission(db, user, repository_id)]
    candidates.extend(team_permissions(db, user, repository_id))
    return cap_by_role(strongest(candidates), user)


def can_read(db: Session, user: User, repository_id: int) -> bool:
    return effective_permission(db, user, repository_id) is not None


def _at_least(permission: RepoPermission | None, required: RepoPermission) -> bool:
    return (
        permission is not None and PERMISSION_ORDER[permission] >= PERMISSION_ORDER[required]
    )


def can_write(db: Session, user: User, repository_id: int) -> bool:
    return _at_least(effective_permission(db, user, repository_id), RepoPermission.write)


def can_admin(db: Session, user: User, repository_id: int) -> bool:
    return effective_permission(db, user, repository_id) == RepoPermission.admin


# --------------------------------------------------------------------- scoping
def accessible_repository_ids(db: Session, user: User) -> set[int] | None:
    """Repository ids the user may read. `None` means "unrestricted" (admin)."""
    if user.role == UserRole.admin:
        return None

    ids: set[int] = {
        repo_id
        for (repo_id,) in db.execute(
            select(RepositoryAccess.repository_id).where(RepositoryAccess.user_id == user.id)
        ).all()
    }
    # A repository stays visible to whoever submitted its link. Ownership is recorded as
    # `repositories.owner_id` at ingestion time, so "my projects" needs no extra grant.
    ids.update(
        repo_id
        for (repo_id,) in db.execute(
            select(Repository.id).where(Repository.owner_id == user.id)
        ).all()
    )

    memberships = db.execute(
        select(TeamMember.team_id, TeamMember.role, TeamMember.scoped_repository_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(and_(TeamMember.user_id == user.id, Team.is_active.is_(True)))
    ).all()

    for team_id, _role, scoped_repo in memberships:
        team_repos = {
            repo_id
            for (repo_id,) in db.execute(
                select(RepositoryAccess.repository_id).where(
                    RepositoryAccess.team_id == team_id
                )
            ).all()
        }
        if scoped_repo is not None:
            if scoped_repo in team_repos:
                ids.add(scoped_repo)
        else:
            ids.update(team_repos)

    return ids


def apply_repository_scope(
    stmt: Select, db: Session, user: User, column=Repository.id
) -> Select:
    """Constrain a `select(Repository)` statement to what the user may read."""
    ids = accessible_repository_ids(db, user)
    if ids is None:
        return stmt
    if not ids:
        # An empty IN () is invalid in PostgreSQL, so use an always-false predicate.
        return stmt.where(func.nullif(1, 1) == 1)
    return stmt.where(column.in_(ids))


# ------------------------------------------------------------------ team roles
def team_role_for(db: Session, user: User, team_id: int) -> TeamRole | None:
    """The user's role in a team, or None when they are not a member."""
    manager_id = db.execute(
        select(Team.manager_id).where(Team.id == team_id)
    ).scalar_one_or_none()
    if manager_id is not None and manager_id == user.id:
        return TeamRole.manager
    return db.execute(
        select(TeamMember.role).where(
            and_(TeamMember.team_id == team_id, TeamMember.user_id == user.id)
        )
    ).scalar_one_or_none()


def team_capabilities(db: Session, user: User, team_id: int) -> frozenset[str]:
    if user.role == UserRole.admin:
        # Platform admins administer every team, membership or not. This check must
        # come first: `team_role_for` returns None for a non-member, which would
        # otherwise short-circuit an admin out of their own capabilities.
        return ALL_CAPABILITIES
    role = team_role_for(db, user, team_id)
    if role is None:
        return frozenset()
    return TEAM_ROLE_CAPABILITIES[role]


def can_manage_team(db: Session, user: User, team_id: int) -> bool:
    return "manage_members" in team_capabilities(db, user, team_id)


def can_manage_access(db: Session, user: User, team_id: int) -> bool:
    return "manage_access" in team_capabilities(db, user, team_id)


def managed_team_ids(db: Session, user: User) -> set[int]:
    """Teams where the user is the project manager (platform admins manage all)."""
    if user.role == UserRole.admin:
        return {
            t for (t,) in db.execute(select(Team.id).where(Team.is_active.is_(True))).all()
        }
    ids = {
        t
        for (t,) in db.execute(
            select(Team.id).where(and_(Team.manager_id == user.id, Team.is_active.is_(True)))
        ).all()
    }
    ids.update(
        t
        for (t,) in db.execute(
            select(TeamMember.team_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                and_(
                    TeamMember.user_id == user.id,
                    TeamMember.role == TeamRole.manager,
                    Team.is_active.is_(True),
                )
            )
        ).all()
    )
    return ids


def manages_any_team(db: Session, user: User) -> bool:
    return bool(managed_team_ids(db, user))


def rank(role: TeamRole) -> int:
    return TEAM_ROLE_ORDER[role]
