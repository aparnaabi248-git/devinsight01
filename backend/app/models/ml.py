"""ML registry + prediction audit models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.user import User


class ModelVersion(Base, TimestampMixin):
    """Registry mirror of the winning MLflow run, so the API needs no MLflow round-trip."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="name_version"),
        Index("ix_model_versions_name_active", "name", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(120), nullable=False)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    mlflow_uri: Mapped[str | None] = mapped_column(String(512))
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    data_hash: Mapped[str | None] = mapped_column(String(64))
    dataset_version: Mapped[str | None] = mapped_column(String(120))
    n_train: Mapped[int | None] = mapped_column(Integer)
    n_test: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[list[str]] = mapped_column(JSON, default=list)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comparison: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    target_description: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    predictions: Mapped[list[Prediction]] = relationship(back_populates="model_version")

    @property
    def tag(self) -> str:
        return f"{self.name}-v{self.version}"


class Prediction(Base, TimestampMixin):
    """Every inference is persisted with full provenance for auditability."""

    __tablename__ = "predictions"
    __table_args__ = (
        Index("ix_predictions_repo_created", "repository_id", "created_at"),
        Index("ix_predictions_target", "target_type"),
        Index("ix_predictions_model_version", "model_version_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    repository: Mapped[Repository | None] = relationship(back_populates="predictions")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user: Mapped[User | None] = relationship(back_populates="predictions")
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    model_version: Mapped[ModelVersion | None] = relationship(back_populates="predictions")

    target_type: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # defect|priority|effort|category
    target_ref: Mapped[str | None] = mapped_column(String(255))  # e.g. "pallets/click@abc1234"
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version_tag: Mapped[str] = mapped_column(String(80), nullable=False)
    prediction: Mapped[str] = mapped_column(String(64), nullable=False)
    probability: Mapped[float | None] = mapped_column(Float)
    probability_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[float | None] = mapped_column(Float)


class MlRun(Base, TimestampMixin):
    """Every training run (candidate + final) is recorded, not just the winner."""

    __tablename__ = "ml_runs"
    __table_args__ = (Index("ix_ml_runs_name_created", "name", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    data_hash: Mapped[str | None] = mapped_column(String(64))
    dataset_rows: Mapped[int | None] = mapped_column(Integer)
    train_seconds: Mapped[float | None] = mapped_column(Float)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cv_scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
