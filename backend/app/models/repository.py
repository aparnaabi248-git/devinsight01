"""Repository + contributor models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
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

from app.db.base import Base, SyncStatus, TimestampMixin, sa_enum

if TYPE_CHECKING:
    from app.models.activity import Commit, PullRequest, Release
    from app.models.analytics import AnalyticsSnapshot, RefreshJob
    from app.models.issue import Issue
    from app.models.ml import Prediction
    from app.models.user import User


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("owner_name", "name", name="uq_repositories_owner_name"),
        Index("ix_repositories_full_name", "full_name"),
        Index("ix_repositories_sync_state", "sync_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner: Mapped[User | None] = relationship(back_populates="repositories")

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "pallets/click"
    owner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    html_url: Mapped[str] = mapped_column(String(512), nullable=False)
    clone_url: Mapped[str | None] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(120), nullable=False, default="main")
    language: Mapped[str | None] = mapped_column(String(80), index=True)
    stars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watchers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_issues_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_kb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    license_spdx: Mapped[str | None] = mapped_column(String(64))
    is_fork: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sync_status: Mapped[SyncStatus] = mapped_column(
        sa_enum(SyncStatus, "sync_status"), nullable=False, default=SyncStatus.pending
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_commits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Newest commit timestamp actually ingested. Maintained by the ingestion service.
    # Lets the client pick a meaningful default repository without aggregating commits.
    latest_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    commits: Mapped[list[Commit]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    issues: Mapped[list[Issue]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    releases: Mapped[list[Release]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    contributors: Mapped[list[Contributor]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list[AnalyticsSnapshot]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    refresh_jobs: Mapped[list[RefreshJob]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    @property
    def health_score(self) -> float:
        """Composite 0-100 activity score: commit recency, contributor breadth, issue flow."""
        recency = 100.0 if self.last_synced_at else 0.0
        breadth = min(100.0, len(self.contributors) * 4.0)
        flow = 100.0 - min(100.0, self.open_issues_count * 0.5)
        return round(0.4 * recency + 0.3 * breadth + 0.3 * flow, 2)


class Contributor(Base, TimestampMixin):
    """Aggregate per-repository contributor statistics."""

    __tablename__ = "contributors"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_login", name="repository_id_github_login"),
        Index("ix_contributors_repo_commits", "repository_id", "commits_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="contributors")

    github_login: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger)
    display_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(512))

    commits_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    files_touched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_commented: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prs_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prs_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviews_given: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    @property
    def total_churn(self) -> int:
        return self.additions + self.deletions
