"""ML routes: the four prediction endpoints plus registry/evaluation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AuthenticatedUser, DbSession
from app.core.errors import ModelNotAvailableError
from app.models.issue import Issue
from app.models.ml import Prediction
from app.schemas.common import Page
from app.schemas.ml import (
    BatchClassifyRequest,
    BatchClassifyResponse,
    ClassifyIssueRequest,
    ClassifyIssueResponse,
    DefectRiskRequest,
    DefectRiskResponse,
    EffortRequest,
    EffortResponse,
    EvaluationReport,
    ModelComparison,
    ModelVersionOut,
    PredictionOut,
    PriorityRequest,
    PriorityResponse,
)
from app.services import ml_service as svc

router = APIRouter(prefix="/ml", tags=["machine-learning"])


def _now() -> datetime:
    return datetime.now(UTC)


def _model_version_payload(entry: dict) -> ModelVersionOut:
    return ModelVersionOut(
        id=0,  # registry rows come from artefacts, not the DB mirror
        name=entry["name"],
        version=entry["version"],
        tag=entry["tag"],
        algorithm=entry.get("algorithm") or "",
        mlflow_run_id=entry.get("mlflow_run_id"),
        dataset_version=entry.get("data_version"),
        data_hash=entry.get("data_hash"),
        n_train=(entry.get("dataset") or {}).get("n_train"),
        n_test=(entry.get("dataset") or {}).get("n_test"),
        features=entry.get("features", []) or [],
        metrics=entry.get("metrics", {}) or {},
        is_active=entry.get("is_active", False),
        trained_at=_parse_iso(entry.get("trained_at")),
        target_description=entry.get("target_description"),
        limitations=entry.get("limitations"),
    )


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------- MODEL 1
@router.post("/defect-risk", response_model=DefectRiskResponse)
def defect_risk(
    payload: DefectRiskRequest, db: DbSession, user: AuthenticatedUser
) -> DefectRiskResponse:
    """Predict LOW/MEDIUM/HIGH defect risk plus a probability for a proposed change set."""
    result = svc.predict_defect_risk(
        db,
        user_id=user.id,
        repository=payload.repository,
        changes=[c.model_dump() for c in payload.changes],
        author=payload.author,
        commit_message=payload.commit_message,
        pull_request_size=payload.pull_request_size,
    )
    return DefectRiskResponse(
        risk_level=result["risk_level"],
        probability=result["probability"],
        message=result["message"],
        contributing_factors=result.get("contributing_factors", []),
        model_name=result["model_name"],
        model_version=result["model_version"],
        prediction_id=result.get("prediction_id"),
        computed_at=_now(),
    )


# ------------------------------------------------------------------- MODEL 2
@router.post("/classify-issue", response_model=ClassifyIssueResponse)
def classify_issue(
    payload: ClassifyIssueRequest, db: DbSession, user: AuthenticatedUser
) -> ClassifyIssueResponse:
    """Classify an issue into BUG / FEATURE_REQUEST / DOCUMENTATION / QUESTION /
    ENHANCEMENT / OTHER with a confidence and the full probability distribution."""
    result = svc.classify_issue(
        db,
        user_id=user.id,
        title=payload.title,
        body=payload.body,
        labels=payload.labels,
    )
    return ClassifyIssueResponse(
        category=result["category"],
        confidence=result["confidence"],
        probabilities=result.get("probabilities", {}),
        top_features=result.get("top_features", []),
        model_name=result["model_name"],
        model_version=result["model_version"],
        prediction_id=result.get("prediction_id"),
        computed_at=_now(),
    )


# ------------------------------------------------------------------- MODEL 3
@router.post("/predict-priority", response_model=PriorityResponse)
def predict_priority(
    payload: PriorityRequest, db: DbSession, user: AuthenticatedUser
) -> PriorityResponse:
    """Advisory CRITICAL/HIGH/MEDIUM/LOW estimate. Not an authoritative decision."""
    result = svc.predict_priority(
        db,
        user_id=user.id,
        title=payload.title,
        body=payload.body,
        labels=payload.labels,
        comments_count=payload.comments_count,
        author_association=payload.author_association,
        linked_prs=payload.linked_prs,
    )
    return PriorityResponse(
        priority=result["priority"],
        confidence=result["confidence"],
        probabilities=result.get("probabilities", {}),
        contributing_factors=result.get("contributing_factors", []),
        disclaimer=result["disclaimer"],
        model_name=result["model_name"],
        model_version=result["model_version"],
        prediction_id=result.get("prediction_id"),
        computed_at=_now(),
    )


# ------------------------------------------------------------------- MODEL 4
@router.post("/estimate-effort", response_model=EffortResponse)
def estimate_effort(
    payload: EffortRequest, db: DbSession, user: AuthenticatedUser
) -> EffortResponse:
    """Estimate resolution effort in hours, with an interval from the residual spread."""
    result = svc.estimate_effort(
        db,
        user_id=user.id,
        title=payload.title,
        body=payload.body,
        labels=payload.labels,
        comments_count=payload.comments_count,
        author_association=payload.author_association,
    )
    return EffortResponse(
        estimated_hours=result["estimated_hours"],
        estimate_low_hours=result["estimate_low_hours"],
        estimate_high_hours=result["estimate_high_hours"],
        unit=result.get("unit", "hours"),
        confidence=result["confidence"],
        contributors=result.get("contributors", {}),
        methodology_note=result["methodology_note"],
        model_name=result["model_name"],
        model_version=result["model_version"],
        prediction_id=result.get("prediction_id"),
        computed_at=_now(),
    )


# --------------------------------------------------------------------- batch
@router.post("/classify-batch", response_model=BatchClassifyResponse)
def classify_batch(
    payload: BatchClassifyRequest, db: DbSession, user: AuthenticatedUser
) -> BatchClassifyResponse:
    """Classify up to 500 issues at once; optionally persist categories back to the DB."""
    results: list[ClassifyIssueResponse] = []
    summary: dict[str, int] = {}

    for item in payload.issues:
        result = svc.classify_issue(
            db,
            user_id=user.id,
            repository_id=payload.repository_id,
            title=item.title,
            body=item.body,
            labels=item.labels,
        )
        summary[result["category"]] = summary.get(result["category"], 0) + 1
        results.append(
            ClassifyIssueResponse(
                category=result["category"],
                confidence=result["confidence"],
                probabilities=result.get("probabilities", {}),
                top_features=result.get("top_features", []),
                model_name=result["model_name"],
                model_version=result["model_version"],
                prediction_id=result.get("prediction_id"),
                computed_at=_now(),
            )
        )

    persisted = 0
    if payload.persist and payload.repository_id:
        by_number: dict[int, ClassifyIssueResponse] = {}
        for item, res in zip(payload.issues, results):
            try:
                by_number[int(item.title)] = res  # title may carry "#123"
            except (TypeError, ValueError):
                continue
        for item, res in zip(payload.issues, results):
            number = _issue_number(item.title, item.body)
            if number is None:
                continue
            row = db.execute(
                select(Issue).where(
                    Issue.repository_id == payload.repository_id, Issue.number == number
                )
            ).scalar_one_or_none()
            if row is not None:
                row.category = res.category
                row.category_confidence = res.confidence
                persisted += 1
        if persisted:
            db.commit()

    return BatchClassifyResponse(results=results, summary=summary, processed=len(results))


def _issue_number(title: str, body: str | None) -> int | None:
    import re

    m = re.search(r"#(\d+)", title or "") or re.search(r"#(\d+)", (body or "")[:200])
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ registry
@router.get("/models")
def list_models(user: AuthenticatedUser) -> dict:
    """Every trained model version with its real, measured metrics."""
    versions = svc.all_model_versions()
    return {
        "items": [_model_version_payload(v).model_dump() for v in versions],
        "loaded": svc.available_models(),
        "total": len(versions),
    }


@router.get("/models/reload")
def reload_models(user: AuthenticatedUser) -> dict:
    """Re-scan the artefact directory (used after a CI job retrains a model)."""
    return svc.reload_models()


@router.get("/models/comparison")
def model_comparison(user: AuthenticatedUser) -> list[ModelComparison]:
    """Cross-model comparison tables, one per target, from the actual training runs."""
    out = []
    for name in svc.available_models():
        try:
            out.append(ModelComparison(**svc.model_comparison(name)))
        except ModelNotAvailableError:
            continue
    return out


@router.get("/models/{name}/versions")
def model_versions(name: str, user: AuthenticatedUser) -> list[dict]:
    return [v for v in svc.all_model_versions() if v["name"] == name]


@router.get("/evaluation/{name}", response_model=EvaluationReport)
def evaluation(name: str, user: AuthenticatedUser) -> EvaluationReport:
    """Full evaluation report: metrics, confusion matrix, per-class report, CV folds."""
    report = svc.evaluation_report(name)
    return EvaluationReport(**report)


@router.get("/predictions", response_model=Page[PredictionOut])
def prediction_log(
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    target_type: str | None = Query(None, max_length=40),
    repository_id: int | None = None,
) -> Page[PredictionOut]:
    """Audit log of every inference the platform has served."""
    filters = []
    if target_type:
        filters.append(Prediction.target_type == target_type)
    if repository_id:
        filters.append(Prediction.repository_id == repository_id)

    total = db.execute(select(func.count(Prediction.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Prediction)
            .where(*filters)
            .order_by(Prediction.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([PredictionOut.model_validate(p) for p in rows], total, page, page_size)
