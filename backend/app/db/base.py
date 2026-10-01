"""SQLAlchemy declarative base, shared column mixins and PostgreSQL enums."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import Any, Column, DateTime, MetaData, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable, reversible names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        # SQLAlchemy's stubs model `primary_key` as a bare Iterable of columns, so the
        # `.name` lookup needs a narrowing cast that does not exist at runtime.
        pk_columns = cast("Sequence[Column[Any]]", self.__table__.primary_key)
        pk = next(iter(pk_columns)).name if pk_columns else "id"
        return f"<{self.__class__.__name__} {pk}={getattr(self, pk, None)!r}>"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- enums
class UserRole(str, enum.Enum):
    admin = "admin"
    analyst = "analyst"
    viewer = "viewer"


class SyncStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class IssueState(str, enum.Enum):
    open = "open"
    closed = "closed"


class PRState(str, enum.Enum):
    open = "open"
    closed = "closed"
    merged = "merged"


class ChangeType(str, enum.Enum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"


class RiskLevel(str, enum.Enum):
    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"


class PriorityLevel(str, enum.Enum):
    critical = "CRITICAL"
    high = "HIGH"
    medium = "MEDIUM"
    low = "LOW"


class IssueCategory(str, enum.Enum):
    bug = "BUG"
    feature_request = "FEATURE_REQUEST"
    documentation = "DOCUMENTATION"
    question = "QUESTION"
    enhancement = "ENHANCEMENT"
    other = "OTHER"


class TeamRole(str, enum.Enum):
    """A member's authority inside a team."""

    manager = "manager"  # can manage members and the team's repository access
    member = "member"  # can read/write the repositories the team has access to
    viewer = "viewer"  # read-only


class RepoPermission(str, enum.Enum):
    """Per-repository capability, granted to a user directly or through a team."""

    read = "read"
    write = "write"  # ingest/refresh the repository
    admin = "admin"  # also manage who else can access it


class AccessGrantType(str, enum.Enum):
    user = "user"
    team = "team"


# Ordered low -> high so permissions can be compared numerically.
PERMISSION_ORDER = {
    RepoPermission.read: 1,
    RepoPermission.write: 2,
    RepoPermission.admin: 3,
}
TEAM_ROLE_ORDER = {
    TeamRole.viewer: 1,
    TeamRole.member: 2,
    TeamRole.manager: 3,
}


def sa_enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    """Persist enums by *value* (readable rows) rather than member name."""
    return SAEnum(
        py_enum,
        name=name,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
