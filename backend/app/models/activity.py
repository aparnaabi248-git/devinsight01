"""Activity models: commits, per-file diffstats, pull requests, releases."""

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

from app.db.base import Base, ChangeType, PRState, TimestampMixin, sa_enum

if TYPE_CHECKING:
    from app.models.repository import Repository


class Commit(Base, TimestampMixin):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="repository_id_sha"),
        Index("ix_commits_repo_authored", "repository_id", "authored_at"),
        Index("ix_commits_repo_author", "repository_id", "author_login"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="commits")

    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_sha: Mapped[str | None] = mapped_column(String(40))
    author_login: Mapped[str | None] = mapped_column(String(120), index=True)
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_email: Mapped[str | None] = mapped_column(String(320))
    message: Mapped[str | None] = mapped_column(Text)
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_merge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_bugfix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    file_changes: Mapped[list[FileChange]] = relationship(
        back_populates="commit", cascade="all, delete-orphan"
    )

    @property
    def churn(self) -> int:
        return self.additions + self.deletions


class FileChange(Base, TimestampMixin):
    """One row per file per commit — the diffstat, normalised (never a JSON blob)."""

    __tablename__ = "file_changes"
    __table_args__ = (
        Index("ix_file_changes_commit", "commit_id"),
        Index("ix_file_changes_path", "path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    commit_id: Mapped[int] = mapped_column(
        ForeignKey("commits.id", ondelete="CASCADE"), nullable=False
    )
    commit: Mapped[Commit] = relationship(back_populates="file_changes")

    path: Mapped[str] = mapped_column(String(512), nullable=False)
    old_path: Mapped[str | None] = mapped_column(String(512))
    change_type: Mapped[ChangeType] = mapped_column(
        sa_enum(ChangeType, "change_type"), nullable=False, default=ChangeType.modified
    )
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    similarity: Mapped[float | None] = mapped_column()


class PullRequest(Base, TimestampMixin):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "number", name="uq_pull_requests_repository_id_number"
        ),
        Index("ix_pr_repo_created", "repository_id", "created_at"),
        Index("ix_pr_repo_merged", "repository_id", "merged"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="pull_requests")

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[PRState] = mapped_column(
        sa_enum(PRState, "pr_state"), nullable=False, default=PRState.open
    )
    merged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    author_login: Mapped[str | None] = mapped_column(String(120), index=True)
    merged_by: Mapped[str | None] = mapped_column(String(120))
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    html_url: Mapped[str | None] = mapped_column(String(512))

    @property
    def merge_duration_hours(self) -> float | None:
        if self.merged_at and self.created_at:
            return round((self.merged_at - self.created_at).total_seconds() / 3600, 2)
        return None

    @property
    def size(self) -> int:
        return self.additions + self.deletions


class Release(Base, TimestampMixin):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint("repository_id", "tag_name", name="repository_id_tag_name"),
        Index("ix_releases_repo_published", "repository_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="releases")

    tag_name: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    author_login: Mapped[str | None] = mapped_column(String(120))
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_prerelease: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    html_url: Mapped[str | None] = mapped_column(String(512))
