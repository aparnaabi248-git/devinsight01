"""User account model. Passwords are bcrypt hashes; the plaintext never lands here."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UserRole, sa_enum

if TYPE_CHECKING:
    from app.models.analytics import RefreshJob
    from app.models.ml import Prediction
    from app.models.repository import Repository


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(160))
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        sa_enum(UserRole, "user_role"), nullable=False, default=UserRole.viewer
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    github_login: Mapped[str | None] = mapped_column(String(64), index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_jobs: Mapped[list[RefreshJob]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"
