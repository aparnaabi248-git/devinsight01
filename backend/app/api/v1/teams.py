"""Team and code-access management routes.

This is the project-manager surface: create teams, add/remove members, assign team roles,
and grant or revoke per-repository access. Every write is gated on the caller's ability to
manage that specific team, so one project's manager cannot reach another's.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AuthenticatedUser, DbSession
from app.core.errors import ConflictError, NotFoundError, PermissionError_, ValidationError
from app.db.base import AccessGrantType, RepoPermission, TeamRole, UserRole
from app.models.repository import Repository
from app.models.team import RepositoryAccess, Team, TeamMember
from app.models.user import User
from app.schemas.common import Page
from app.schemas.team import (
    AccessGrantCreate,
    AccessGrantOut,
    MemberAdd,
    MemberUpdate,
    MyAccessOut,
    RepositoryAccessSummary,
    TeamCreate,
    TeamDetail,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
    slugify,
)
from app.services import access as A

router = APIRouter(prefix="/teams", tags=["teams & access"])


# ------------------------------------------------------------------- helpers
def _get_team(db, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise NotFoundError(f"team {team_id} does not exist", code="team_not_found")
    return team


def _require_team_view(db, user: User, team_id: int) -> Team:
    team = _get_team(db, team_id)
    if A.team_role_for(db, user, team_id) is None and user.role != UserRole.admin:
        raise PermissionError_("you are not a member of this team", code="not_a_team_member")
    return team


def _require_team_manage(db, user: User, team_id: int) -> Team:
    team = _get_team(db, team_id)
    if not A.can_manage_team(db, user, team_id):
        raise PermissionError_(
            "only this team's project manager can change its members",
            code="not_team_manager",
        )
    return team


def _get_user(db, identifier: str) -> User:
    ident = identifier.strip().lower()
    user = db.execute(
        select(User).where(
            (func.lower(User.username) == ident) | (func.lower(User.email) == ident)
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"user '{identifier}' does not exist", code="user_not_found")
    return user


def _members(db, team_id: int) -> list[TeamMemberOut]:
    rows = db.execute(
        select(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.role, User.username)
    ).all()
    return [
        TeamMemberOut(
            id=member.id,
            user_id=member.user_id,
            username=user.username,
            email=user.email,
            role=member.role,
            scoped_repository_id=member.scoped_repository_id,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]


def _grants(db, team_id: int) -> list[AccessGrantOut]:
    rows = db.execute(
        select(RepositoryAccess, Repository)
        .join(Repository, Repository.id == RepositoryAccess.repository_id)
        .where(RepositoryAccess.team_id == team_id)
        .order_by(Repository.full_name)
    ).all()
    team = db.get(Team, team_id)
    return [
        AccessGrantOut(
            id=grant.id,
            repository_id=repo.id,
            repository_name=repo.full_name,
            grant_type=grant.grant_type.value,
            team_id=team.id,
            team_name=team.name if team else None,
            permission=grant.permission,
            granted_by_id=grant.granted_by_id,
            expires_at=grant.expires_at,
            note=grant.note,
            is_expired=grant.is_expired,
        )
        for grant, repo in rows
    ]


def _team_out(db, team: Team, user: User) -> TeamOut:
    member_count = db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    ).scalar_one()
    repo_count = db.execute(
        select(func.count(RepositoryAccess.id)).where(RepositoryAccess.team_id == team.id)
    ).scalar_one()
    return TeamOut(
        id=team.id,
        name=team.name,
        slug=team.slug,
        description=team.description,
        manager_id=team.manager_id,
        manager_username=db.get(User, team.manager_id).username if team.manager_id else None,
        is_active=team.is_active,
        member_count=member_count,
        repository_count=repo_count,
        # A platform admin is not necessarily a member, but `capabilities` grants them
        # full authority. Reporting `my_role: null` next to those capabilities reads as
        # a contradiction, so admins are reported as managers of every team.
        my_role=(
            TeamRole.manager
            if user.role == UserRole.admin
            else A.team_role_for(db, user, team.id)
        ),
        capabilities=sorted(A.team_capabilities(db, user, team.id)),
        created_at=team.created_at,
    )


# --------------------------------------------------------------------- teams
@router.get("", response_model=Page[TeamOut])
def list_teams(
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    mine: bool = Query(False, description="Only teams I belong to or manage"),
    active_only: bool = True,
) -> Page[TeamOut]:
    """List teams visible to the caller. Non-managers only see their own teams."""
    filters = []
    if active_only:
        filters.append(Team.is_active.is_(True))

    visible = A.managed_team_ids(db, user)
    member_of = {
        t
        for (t,) in db.execute(
            select(TeamMember.team_id).where(TeamMember.user_id == user.id)
        ).all()
    }
    allowed = visible | member_of if user.role != UserRole.admin else None
    if allowed is not None:
        if mine:
            allowed = visible
        if not allowed:
            return Page.build([], 0, page, page_size)
        filters.append(Team.id.in_(allowed))

    total = db.execute(select(func.count(Team.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Team)
            .where(*filters)
            .order_by(Team.name)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([_team_out(db, t, user) for t in rows], total, page, page_size)


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, db: DbSession, user: AuthenticatedUser) -> TeamOut:
    """Create a team. The creator becomes its project manager."""
    manager = _get_user(db, payload.manager_username) if payload.manager_username else user
    slug = slugify(payload.name)
    if db.execute(select(Team).where(Team.slug == slug)).scalar_one_or_none():
        raise ConflictError(
            f"a team named '{payload.name}' already exists", code="team_exists"
        )

    team = Team(
        name=payload.name,
        slug=slug,
        description=payload.description,
        manager_id=manager.id,
        is_active=True,
    )
    db.add(team)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("that team already exists", code="team_exists") from exc

    # The manager is also recorded as a member so membership queries are uniform.
    db.add(
        TeamMember(
            team_id=team.id,
            user_id=manager.id,
            role=TeamRole.manager,
            invited_by_id=user.id,
            joined_at=datetime.now(UTC),
        )
    )
    db.commit()
    db.refresh(team)
    return _team_out(db, team, user)


@router.get("/{team_id}", response_model=TeamDetail)
def get_team(team_id: int, db: DbSession, user: AuthenticatedUser) -> TeamDetail:
    team = _require_team_view(db, user, team_id)
    detail = TeamDetail(**_team_out(db, team, user).model_dump())
    detail.members = _members(db, team_id)
    detail.repositories = _grants(db, team_id)
    return detail


@router.patch("/{team_id}", response_model=TeamOut)
def update_team(
    team_id: int, payload: TeamUpdate, db: DbSession, user: AuthenticatedUser
) -> TeamOut:
    team = _require_team_manage(db, user, team_id)
    if payload.name is not None:
        team.name = payload.name.strip()
    if payload.description is not None:
        team.description = payload.description
    if payload.is_active is not None:
        team.is_active = payload.is_active
    db.commit()
    db.refresh(team)
    return _team_out(db, team, user)


# ------------------------------------------------------------------- members
@router.post(
    "/{team_id}/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(
    team_id: int, payload: MemberAdd, db: DbSession, user: AuthenticatedUser
) -> TeamMemberOut:
    team = _require_team_manage(db, user, team_id)
    member_user = _get_user(db, payload.username)

    existing = db.execute(
        select(TeamMember).where(
            (TeamMember.team_id == team_id) & (TeamMember.user_id == member_user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"{member_user.username} is already a member of {team.name}",
            code="already_a_member",
        )
    if (
        payload.scoped_repository_id is not None
        and db.get(Repository, payload.scoped_repository_id) is None
    ):
        raise NotFoundError(
            f"repository {payload.scoped_repository_id} does not exist",
            code="repository_not_found",
        )

    member = TeamMember(
        team_id=team_id,
        user_id=member_user.id,
        role=payload.role,
        scoped_repository_id=payload.scoped_repository_id,
        invited_by_id=user.id,
        joined_at=datetime.now(UTC),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return TeamMemberOut(
        id=member.id,
        user_id=member_user.id,
        username=member_user.username,
        email=member_user.email,
        role=member.role,
        scoped_repository_id=member.scoped_repository_id,
        joined_at=member.joined_at,
    )


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberOut)
def update_member(
    team_id: int, user_id: int, payload: MemberUpdate, db: DbSession, user: AuthenticatedUser
) -> TeamMemberOut:
    _require_team_manage(db, user, team_id)
    member = db.execute(
        select(TeamMember).where(
            (TeamMember.team_id == team_id) & (TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFoundError("that user is not a member of this team", code="not_a_team_member")
    if payload.role is not None:
        member.role = payload.role
    if payload.scoped_repository_id is not None:
        member.scoped_repository_id = payload.scoped_repository_id
    db.commit()
    db.refresh(member)
    member_user = db.get(User, user_id)
    return TeamMemberOut(
        id=member.id,
        user_id=user_id,
        username=member_user.username,
        email=member_user.email,
        role=member.role,
        scoped_repository_id=member.scoped_repository_id,
        joined_at=member.joined_at,
    )


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(team_id: int, user_id: int, db: DbSession, user: AuthenticatedUser) -> None:
    _require_team_manage(db, user, team_id)
    member = db.execute(
        select(TeamMember).where(
            (TeamMember.team_id == team_id) & (TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFoundError("that user is not a member of this team", code="not_a_team_member")
    db.delete(member)
    db.commit()


# -------------------------------------------------------------------- access
@router.post(
    "/{team_id}/access", response_model=AccessGrantOut, status_code=status.HTTP_201_CREATED
)
def grant_repository_access(
    team_id: int, payload: AccessGrantCreate, db: DbSession, user: AuthenticatedUser
) -> AccessGrantOut:
    """Give a team access to a repository."""
    team = _require_team_manage(db, user, team_id)
    if payload.user_id is not None:
        raise ValidationError("team access grants must not target a user")
    if db.get(Repository, payload.repository_id) is None:
        raise NotFoundError(
            f"repository {payload.repository_id} does not exist", code="repository_not_found"
        )

    grant = db.execute(
        select(RepositoryAccess).where(
            (RepositoryAccess.repository_id == payload.repository_id)
            & (RepositoryAccess.team_id == team.id)
            & (RepositoryAccess.user_id.is_(None))
        )
    ).scalar_one_or_none()
    if grant is not None:
        grant.permission = payload.permission
        grant.expires_at = payload.expires_at
        grant.note = payload.note
    else:
        grant = RepositoryAccess(
            repository_id=payload.repository_id,
            team_id=team.id,
            grant_type=AccessGrantType.team,
            permission=payload.permission,
            granted_by_id=user.id,
            expires_at=payload.expires_at,
            note=payload.note,
        )
        db.add(grant)
    db.commit()
    db.refresh(grant)
    repo = db.get(Repository, payload.repository_id)
    return AccessGrantOut(
        id=grant.id,
        repository_id=repo.id,
        repository_name=repo.full_name,
        grant_type=grant.grant_type.value,
        team_id=team.id,
        team_name=team.name,
        permission=grant.permission,
        granted_by_id=user.id,
        expires_at=grant.expires_at,
        note=grant.note,
        is_expired=grant.is_expired,
    )


@router.delete("/{team_id}/access/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_repository_access(
    team_id: int, grant_id: int, db: DbSession, user: AuthenticatedUser
) -> None:
    """Revoke a team's access to a repository."""
    _require_team_manage(db, user, team_id)
    grant = db.get(RepositoryAccess, grant_id)
    if grant is None or grant.team_id != team_id:
        raise NotFoundError("that access grant does not exist", code="access_grant_not_found")
    db.delete(grant)
    db.commit()


@router.post("/access", response_model=AccessGrantOut, status_code=status.HTTP_201_CREATED)
def grant_user_access(
    payload: AccessGrantCreate, db: DbSession, user: AuthenticatedUser
) -> AccessGrantOut:
    """Grant one user access to one repository, bypassing teams (admins only)."""
    if user.role != UserRole.admin:
        raise PermissionError_(
            "only administrators can grant direct user access", code="insufficient_role"
        )
    if payload.user_id is None:
        raise ValidationError("`user_id` is required for a direct user grant")
    if db.get(Repository, payload.repository_id) is None:
        raise NotFoundError(
            f"repository {payload.repository_id} does not exist", code="repository_not_found"
        )
    if db.get(User, payload.user_id) is None:
        raise NotFoundError(f"user {payload.user_id} does not exist", code="user_not_found")

    grant = db.execute(
        select(RepositoryAccess).where(
            (RepositoryAccess.repository_id == payload.repository_id)
            & (RepositoryAccess.user_id == payload.user_id)
            & (RepositoryAccess.team_id.is_(None))
        )
    ).scalar_one_or_none()
    if grant is not None:
        grant.permission = payload.permission
        grant.expires_at = payload.expires_at
        grant.note = payload.note
    else:
        grant = RepositoryAccess(
            repository_id=payload.repository_id,
            user_id=payload.user_id,
            grant_type=AccessGrantType.user,
            permission=payload.permission,
            granted_by_id=user.id,
            expires_at=payload.expires_at,
            note=payload.note,
        )
        db.add(grant)
    db.commit()
    db.refresh(grant)
    repo = db.get(Repository, payload.repository_id)
    return AccessGrantOut(
        id=grant.id,
        repository_id=repo.id,
        repository_name=repo.full_name,
        grant_type=grant.grant_type.value,
        user_id=payload.user_id,
        permission=grant.permission,
        granted_by_id=user.id,
        expires_at=grant.expires_at,
        note=grant.note,
        is_expired=grant.is_expired,
    )


# ----------------------------------------------------------------- my access
@router.get("/access/me", response_model=MyAccessOut)
def my_access(db: DbSession, user: AuthenticatedUser) -> MyAccessOut:
    """Everything the caller can currently reach, and how they get it."""
    repositories = (
        db.execute(select(Repository).order_by(Repository.full_name)).scalars().all()
    )
    managed = A.managed_team_ids(db, user)
    team_names = dict(db.execute(select(Team.id, Team.name)).all())

    # One query for every (team, repository) grant the caller inherits, so building the
    # per-repository "via teams" list stays O(1) instead of O(teams x repositories).
    memberships = db.execute(
        select(TeamMember.team_id, TeamMember.scoped_repository_id).where(
            TeamMember.user_id == user.id
        )
    ).all()
    scoped = dict(memberships)
    my_teams = {team_id for team_id, _ in memberships}
    grants_by_team: dict[int, set[int]] = {}
    if my_teams:
        for team_id, repo_id in db.execute(
            select(RepositoryAccess.team_id, RepositoryAccess.repository_id).where(
                RepositoryAccess.team_id.in_(list(my_teams))
            )
        ).all():
            grants_by_team.setdefault(team_id, set()).add(repo_id)

    summaries: list[RepositoryAccessSummary] = []
    for repo in repositories:
        if user.role == UserRole.admin:
            summaries.append(
                RepositoryAccessSummary(
                    repository_id=repo.id,
                    full_name=repo.full_name,
                    permission=RepoPermission.admin,
                    source="admin",
                )
            )
            continue

        direct = A.direct_permission(db, user, repo.id)
        via = A.team_permissions(db, user, repo.id)
        effective = A.cap_by_role(A.strongest([direct, *via]), user)
        if effective is None:
            continue

        source = "direct" if (direct and not via) else "team" if via else "direct"
        contributors = {
            team_id
            for team_id, pinned in scoped.items()
            if repo.id in grants_by_team.get(team_id, set())
            and (pinned is None or pinned == repo.id)
        }
        summaries.append(
            RepositoryAccessSummary(
                repository_id=repo.id,
                full_name=repo.full_name,
                permission=effective,
                source=source,
                via_teams=sorted(team_names.get(t, str(t)) for t in contributors),
            )
        )

    return MyAccessOut(
        user_id=user.id,
        username=user.username,
        role=user.role.value,
        manages_teams=len(managed),
        repositories=summaries,
    )
