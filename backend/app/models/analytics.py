"""Analytics snapshots and the ingestion job ledger."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JobStatus, TimestampMixin, sa_enum

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.user import User


class AnalyticsSnapshot(Base, TimestampMixin):
    """Materialised daily metrics so dashboard reads never scan the raw fact tables."""

    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "snapshot_date", "granularity", name="repo_date_gran"
        ),
        Index("ix_snapshots_repo_gran", "repository_id", "granularity", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="snapshots")
    snapshot_date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO date
    granularity: Mapped[str] = mapped_column(String(10), nullable=False, default="daily")
    commits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prs_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prs_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    releases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bug_fixes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_contributors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RefreshJob(Base, TimestampMixin):
    """Idempotent ingestion ledger — prevents duplicate GitHub work for the same target."""

    __tablename__ = "refresh_jobs"
    __table_args__ = (Index("ix_refresh_jobs_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    repository: Mapped[Repository | None] = relationship(back_populates="refresh_jobs")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user: Mapped[User | None] = relationship(back_populates="refresh_jobs")

    job_type: Mapped[str] = mapped_column(String(40), nullable=False, default="ingest")
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        sa_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.queued
    )
    rows_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    api_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
