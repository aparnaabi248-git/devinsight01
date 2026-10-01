"""Runtime ML service. Loads ml/inference into the backend process and persists every
prediction with its model version. The backend never imports sklearn directly.
"""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ModelNotAvailableError
from app.core.logging import get_logger
from app.models.ml import ModelVersion, Prediction

log = get_logger("api.ml_service")

_inference: Any = None
_registry: Any = None


def _load_inference():
    """Import the ml package lazily so the API boots even with no models trained."""
    global _inference, _registry
    if _inference is not None:
        return _inference, _registry
    ml_root = settings.MODEL_DIR.parent  # .../ml
    repo_root = ml_root.parent
    for path in (str(repo_root), str(ml_root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        inference = importlib.import_module("ml.inference.predictor")
        registry_mod = importlib.import_module("ml.inference.registry")
        _registry = registry_mod.get_registry(reload=True)
        _inference = inference
        log.info(
            "ml inference loaded", extra={"context": {"models": _registry.loaded_summary()}}
        )
    except Exception as exc:
        _inference, _registry = None, None
        log.warning(
            "ml inference unavailable",
            extra={"context": {"error": f"{type(exc).__name__}: {exc}"}},
        )
    return _inference, _registry


def get_registry():
    _, registry = _load_inference()
    if registry is None:
        raise ModelNotAvailableError(
            "the ML package could not be imported — check that ml/ is on the path"
        )
    return registry


def require_model(name: str):
    """Fetch a loaded model, mapping a missing artefact onto a 503, not a 500."""
    try:
        return get_registry().require(name)
    except KeyError as exc:
        raise ModelNotAvailableError(str(exc).strip('"')) from exc


def reload_models() -> dict[str, Any]:
    _, registry = _load_inference()
    if registry is None:
        raise ModelNotAvailableError("ML package is not importable")
    return {"loaded": registry.loaded_summary(), "names": registry.names()}


def available_models() -> list[str]:
    _, registry = _load_inference()
    return registry.names() if registry else []


def _run(fn_name: str, **kwargs: Any) -> tuple[dict[str, Any], float]:
    inference, registry = _load_inference()
    if inference is None or registry is None:
        raise ModelNotAvailableError(
            "No trained models are available. Run `python ml/training/run_all.py` first."
        )
    fn: Callable[..., dict] = getattr(inference, fn_name, None)
    if fn is None:
        raise ModelNotAvailableError(f"inference function '{fn_name}' not found")
    # Probe first so an untrained model surfaces as 503 rather than an opaque 500.
    require_model(MODEL_FOR_INFERENCE.get(fn_name, ""))
    start = time.perf_counter()
    result = fn(registry=registry, **kwargs)
    return result, round((time.perf_counter() - start) * 1000, 3)


MODEL_FOR_INFERENCE = {
    "predict_defect_risk": "defect_risk",
    "classify_issue": "issue_classifier",
    "predict_priority": "issue_priority",
    "estimate_effort": "issue_effort",
}


# ---------------------------------------------------------------- predictions
def _sync_model_version(db: Session, name: str, model_tag: str) -> ModelVersion | None:
    """Mirror a trained model into `model_versions` so the API can report its provenance."""
    version = int(model_tag.rsplit("v", 1)[-1])
    existing = db.execute(
        select(ModelVersion).where(ModelVersion.name == name, ModelVersion.version == version)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return None


def record_prediction(
    db: Session,
    *,
    target_type: str,
    result: dict[str, Any],
    user_id: int | None = None,
    repository_id: int | None = None,
    target_ref: str | None = None,
    latency_ms: float | None = None,
) -> Prediction:
    """Persist one inference with full provenance. Never fails the request."""
    try:
        model_name = result.get("model_name", target_type)
        model_tag = result.get("model_version", "unknown")
        version = _sync_model_version(db, model_name, model_tag)
        model_version_id = version.id if version is not None else None

        probability = result.get("probability")
        prediction_value = (
            result.get("risk_level")
            or result.get("category")
            or result.get("priority")
            or (
                str(result.get("estimated_hours")) + " h"
                if result.get("estimated_hours") is not None
                else ""
            )
        )
        prediction = Prediction(
            repository_id=repository_id,
            user_id=user_id,
            model_version_id=model_version_id,
            target_type=target_type,
            target_ref=target_ref,
            model_name=model_name,
            model_version_tag=model_tag,
            prediction=str(prediction_value),
            probability=float(probability) if probability is not None else None,
            probability_json=result.get("probabilities"),
            input_hash=result.get("input_hash"),
            explanation={
                "message": result.get("message"),
                "contributing_factors": result.get("contributing_factors"),
                "contributors": result.get("contributors"),
                "methodology_note": result.get("methodology_note"),
                "note": result.get("note"),
                "disclaimer": result.get("disclaimer"),
                "estimated_hours": result.get("estimated_hours"),
            },
            latency_ms=latency_ms,
        )
        db.add(prediction)
        db.commit()
        db.refresh(prediction)
        return prediction
    except Exception as exc:  # provenance is valuable but must not break inference
        db.rollback()
        log.warning(
            "could not record prediction",
            extra={"context": {"error": str(exc), "target": target_type}},
        )
        return None  # type: ignore[return-value]


# ------------------------------------------------------------------- wrappers
def predict_defect_risk(db: Session, *, user_id=None, repository_id=None, **kwargs):
    result, latency = _run("predict_defect_risk", **kwargs)
    prediction = record_prediction(
        db,
        target_type="defect_risk",
        result=result,
        user_id=user_id,
        repository_id=repository_id,
        target_ref=kwargs.get("repository"),
        latency_ms=latency,
    )
    result["prediction_id"] = prediction.id if prediction else None
    return result


def classify_issue(db: Session, *, user_id=None, repository_id=None, **kwargs):
    result, latency = _run("classify_issue", **kwargs)
    prediction = record_prediction(
        db,
        target_type="issue_category",
        result=result,
        user_id=user_id,
        repository_id=repository_id,
        latency_ms=latency,
    )
    result["prediction_id"] = prediction.id if prediction else None
    return result


def predict_priority(db: Session, *, user_id=None, repository_id=None, **kwargs):
    result, latency = _run("predict_priority", **kwargs)
    prediction = record_prediction(
        db,
        target_type="issue_priority",
        result=result,
        user_id=user_id,
        repository_id=repository_id,
        latency_ms=latency,
    )
    result["prediction_id"] = prediction.id if prediction else None
    return result


def estimate_effort(db: Session, *, user_id=None, repository_id=None, **kwargs):
    result, latency = _run("estimate_effort", **kwargs)
    prediction = record_prediction(
        db,
        target_type="issue_effort",
        result=result,
        user_id=user_id,
        repository_id=repository_id,
        latency_ms=latency,
    )
    result["prediction_id"] = prediction.id if prediction else None
    return result


def evaluation_report(name: str) -> dict[str, Any]:
    require_model(name)
    return get_registry().report(name)


def model_comparison(name: str) -> dict[str, Any]:
    require_model(name)
    return get_registry().comparison(name)


def all_model_versions() -> list[dict[str, Any]]:
    return get_registry().all_versions()
