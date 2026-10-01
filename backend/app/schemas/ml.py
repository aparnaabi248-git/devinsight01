"""ML request/response schemas for the four models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.base import IssueCategory, PriorityLevel, RiskLevel

EffortUnit = Literal["hours", "days"]


# --------------------------------------------------------------------- MODEL 1
class ChangeInput(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    change_type: str = Field("modified", pattern="^(added|modified|deleted|renamed)$")
    additions: int = Field(0, ge=0, le=1_000_000)
    deletions: int = Field(0, ge=0, le=1_000_000)


class DefectRiskRequest(BaseModel):
    """Predict defect risk for a proposed change set (a PR, or a single commit)."""

    repository: str = Field(..., min_length=3, max_length=255, description="owner/name")
    author: str | None = Field(None, max_length=120)
    changes: list[ChangeInput] = Field(..., min_length=1, max_length=2000)
    commit_message: str | None = Field(None, max_length=4000)
    pull_request_size: int | None = Field(None, ge=0)

    @field_validator("repository")
    @classmethod
    def validate_repo(cls, v: str) -> str:
        if "/" not in v or len(v.split("/")) != 2:
            raise ValueError("repository must be in 'owner/name' form")
        return v

    @property
    def total_additions(self) -> int:
        return sum(c.additions for c in self.changes)

    @property
    def total_deletions(self) -> int:
        return sum(c.deletions for c in self.changes)


class DefectRiskResponse(BaseModel):
    risk_level: RiskLevel
    probability: float = Field(..., ge=0.0, le=1.0, description="P(defect)")
    message: str
    contributing_factors: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str
    model_version: str
    model_version_id: int | None = None
    prediction_id: int | None = None
    computed_at: datetime


# --------------------------------------------------------------------- MODEL 2
class ClassifyIssueRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=1024)
    body: str | None = Field(None, max_length=200_000)
    labels: list[str] = Field(default_factory=list, max_length=50)
    repository: str | None = Field(None, max_length=255)

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, v: list[str]) -> list[str]:
        return [l.strip().lower() for l in v if l and l.strip()][:50]


class ClassifyIssueResponse(BaseModel):
    category: IssueCategory
    confidence: float = Field(..., ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)
    top_features: list[str] = Field(default_factory=list)
    model_name: str
    model_version: str
    prediction_id: int | None = None
    computed_at: datetime


# --------------------------------------------------------------------- MODEL 3
class PriorityRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=1024)
    body: str | None = Field(None, max_length=200_000)
    labels: list[str] = Field(default_factory=list, max_length=50)
    comments_count: int = Field(0, ge=0)
    author_association: str | None = Field(None, max_length=40)
    linked_prs: int = Field(0, ge=0, le=1000)
    repository: str | None = Field(None, max_length=255)


class PriorityResponse(BaseModel):
    priority: PriorityLevel
    confidence: float = Field(..., ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)
    contributing_factors: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "This priority is a machine-learning estimate derived from patterns in historical "
        "issues. It is an advisory signal only and must not be treated as an authoritative "
        "priority decision. Human triage remains the source of truth."
    )
    model_name: str
    model_version: str
    prediction_id: int | None = None
    computed_at: datetime


# --------------------------------------------------------------------- MODEL 4
class EffortRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=1024)
    body: str | None = Field(None, max_length=200_000)
    labels: list[str] = Field(default_factory=list, max_length=50)
    comments_count: int = Field(0, ge=0)
    author_association: str | None = Field(None, max_length=40)
    unit: EffortUnit = "hours"
    repository: str | None = Field(None, max_length=255)


class EffortResponse(BaseModel):
    estimated_hours: float
    estimate_low_hours: float
    estimate_high_hours: float
    unit: EffortUnit = "hours"
    confidence: float = Field(..., ge=0.0, le=1.0)
    contributors: dict[str, float] = Field(default_factory=dict)
    methodology_note: str = (
        "Trained on time-to-close (created_at → closed_at) of historical GitHub issues, "
        "because GitHub does not record engineering hours. Time-to-close includes queue and "
        "wait time, so this is a coarse proxy for hands-on effort, not a timesheet."
    )
    model_name: str
    model_version: str
    prediction_id: int | None = None
    computed_at: datetime


# ------------------------------------------------------------------- batch
class BatchClassifyRequest(BaseModel):
    repository_id: int | None = None
    issues: list[ClassifyIssueRequest] = Field(..., min_length=1, max_length=500)
    persist: bool = True


class BatchClassifyResponse(BaseModel):
    results: list[ClassifyIssueResponse]
    summary: dict[str, int]
    processed: int


# ------------------------------------------------------------------- registry
class ModelVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: int
    tag: str
    algorithm: str
    mlflow_run_id: str | None = None
    dataset_version: str | None = None
    data_hash: str | None = None
    n_train: int | None = None
    n_test: int | None = None
    features: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    trained_at: datetime | None = None
    target_description: str | None = None
    limitations: str | None = None


class ModelComparisonRow(BaseModel):
    model: str
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    roc_auc: float | None = None
    mae: float | None = None
    rmse: float | None = None
    r2: float | None = None
    cv_f1_mean: float | None = None
    training_time_seconds: float = 0.0
    selected: bool = False


class ModelComparison(BaseModel):
    name: str
    metric_focus: str
    selection_criterion: str
    rows: list[ModelComparisonRow]
    selected_model: str
    evaluated_at: datetime | None = None


class EvaluationReport(BaseModel):
    name: str
    version: int
    algorithm: str
    task_type: str
    target_description: str
    metrics: dict[str, Any]
    classification_report: dict[str, Any] | None = None
    confusion_matrix: list[list[int]] | None = None
    confusion_matrix_labels: list[str] | None = None
    cv_folds: list[dict[str, Any]] = Field(default_factory=list)
    feature_importance: list[dict[str, Any]] = Field(default_factory=list)
    comparison: list[dict[str, Any]] = Field(default_factory=list)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    dataset: dict[str, Any] = Field(default_factory=dict)
    limitations: str | None = None
    trained_at: datetime | None = None


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_ref: str | None = None
    model_name: str
    model_version_tag: str
    prediction: str
    probability: float | None = None
    input_hash: str | None = None
    explanation: dict[str, Any] | None = None
    latency_ms: float | None = None
    created_at: datetime
