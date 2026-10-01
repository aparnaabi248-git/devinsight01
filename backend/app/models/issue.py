"""Issue models: normalised labels, M2M map, comments."""

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

from app.db.base import Base, IssueCategory, IssueState, TimestampMixin, sa_enum

if TYPE_CHECKING:
    from app.models.repository import Repository


class Issue(Base, TimestampMixin):
    """GitHub issues. PRs also surface as issues on GitHub, hence `is_pull_request`."""

    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_issues_repository_id_number"),
        Index("ix_issues_repo_created", "repository_id", "created_at"),
        Index("ix_issues_repo_state", "repository_id", "state"),
        Index("ix_issues_repo_category", "repository_id", "category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository: Mapped[Repository] = relationship(back_populates="issues")

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[IssueState] = mapped_column(
        sa_enum(IssueState, "issue_state"), nullable=False, default=IssueState.open
    )
    is_pull_request: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    author_login: Mapped[str | None] = mapped_column(String(120), index=True)
    author_association: Mapped[str | None] = mapped_column(String(40))
    assignee_login: Mapped[str | None] = mapped_column(String(120))
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[IssueCategory | None] = mapped_column(
        sa_enum(IssueCategory, "issue_category"), nullable=True
    )
    category_confidence: Mapped[float | None] = mapped_column()
    priority: Mapped[str | None] = mapped_column(String(16))
    effort_hours: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    html_url: Mapped[str | None] = mapped_column(String(512))

    labels: Mapped[list[IssueLabel]] = relationship(
        secondary="issue_label_map", back_populates="issues", lazy="selectin"
    )
    comments: Mapped[list[IssueComment]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def resolution_hours(self) -> float | None:
        """Time-to-close proxy for development effort (see ml/docs/LABELS.md)."""
        if self.closed_at and self.created_at:
            return round((self.closed_at - self.created_at).total_seconds() / 3600, 2)
        return None


class IssueLabel(Base, TimestampMixin):
    __tablename__ = "issue_labels"
    __table_args__ = (Index("ix_issue_labels_name", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    colour: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)

    issues: Mapped[list[Issue]] = relationship(
        secondary="issue_label_map", back_populates="labels"
    )


class IssueLabelMap(Base):
    """Explicit M2M join table (association object) — queried directly for analytics."""

    __tablename__ = "issue_label_map"
    __table_args__ = (Index("ix_issue_label_map_label", "label_id"),)

    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[int] = mapped_column(
        ForeignKey("issue_labels.id", ondelete="CASCADE"), primary_key=True
    )


class IssueComment(Base, TimestampMixin):
    __tablename__ = "issue_comments"
    __table_args__ = (Index("ix_issue_comments_issue", "issue_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    issue: Mapped[Issue] = relationship(back_populates="comments")
    author_login: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
