"""Team and code-access models.

DevInsight's authorisation has two layers:

* a **global role** on the user (`admin` / `analyst` / `viewer`) that sets the ceiling of
  what someone can do anywhere, and
* a **per-repository grant** that decides what they can actually do to *this* repository.

A grant is either direct (`RepositoryAccess.user_id`) or inherited from a team
(`RepositoryAccess.team_id`). Both are stored in one table so "who can touch this repo"
is a single query, and a team grant can be revoked without touching individual users.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    AccessGrantType,
    Base,
    RepoPermission,
    TeamRole,
    TimestampMixin,
    sa_enum,
)

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.user import User


class Team(Base, TimestampMixin):
    """A group of people who share access to a set of repositories."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_teams_slug"),
        Index("ix_teams_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # The project manager accountable for this team. Kept denormalised so the manager
    # can be resolved without a scan over team_members.
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    manager: Mapped[User | None] = relationship(foreign_keys=[manager_id])
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    members: Mapped[list[TeamMember]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    grants: Mapped[list[RepositoryAccess]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Base, TimestampMixin):
    """Membership + the member's role within the team."""

    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_id_user_id"),
        Index("ix_team_members_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[TeamRole] = mapped_column(
        sa_enum(TeamRole, "team_role"), nullable=False, default=TeamRole.member
    )
    # A project manager can pin a member to one repository within the team, e.g. review
    # access to a single service. NULL means the member's team role applies everywhere.
    scoped_repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    invited_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(foreign_keys=[user_id])

    @property
    def is_scoped(self) -> bool:
        return self.scoped_repository_id is not None


class RepositoryAccess(Base, TimestampMixin):
    """A capability grant on one repository, to a user or to a whole team.

    Exactly one of `user_id` / `team_id` is set, enforced by a check constraint.
    """

    __tablename__ = "repository_access"
    __table_args__ = (
        UniqueConstraint("repository_id", "user_id", "team_id", name="uq_repo_access_scope"),
        Index("ix_repo_access_lookup", "repository_id", "grant_type"),
        Index("ix_repo_access_user", "user_id"),
        Index("ix_repo_access_team", "team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    grant_type: Mapped[AccessGrantType] = mapped_column(
        sa_enum(AccessGrantType, "access_grant_type"), nullable=False
    )
    permission: Mapped[RepoPermission] = mapped_column(
        sa_enum(RepoPermission, "repo_permission"),
        nullable=False,
        default=RepoPermission.read,
    )
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    repository: Mapped[Repository] = relationship()
    team: Mapped[Team | None] = relationship(back_populates="grants")
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False

        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires < datetime.now(UTC)
