"""Shared training machinery: splits, cross-validation, model sweep, MLflow logging.

`train_and_select` is used by all four training scripts so model selection is uniform:
fit every candidate, score with stratified cross-validation on the declared primary
metric, select the winner, then refit and report honest held-out test metrics.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.base import BaseEstimator
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from config import ARTIFACT_DIR, CV_FOLDS, RANDOM_SEED, REPORT_DIR
from evaluation import metrics as M


@dataclass
class SweepResult:
    """Outcome of comparing every candidate model for one task."""

    name: str
    task: str
    primary_metric: str
    selection_criterion: str
    candidates: dict[str, Any] = field(default_factory=dict)  # name -> fitted estimator
    rows: list[dict[str, Any]] = field(default_factory=list)  # comparison table
    selected: str = ""
    estimator: BaseEstimator | None = None
    test_metrics: dict[str, Any] = field(default_factory=dict)
    cv_summary: dict[str, Any] = field(default_factory=dict)
    test_predictions: Any = None
    test_proba: Any = None
    classes: list[Any] = field(default_factory=list)
    classes_proba: list[Any] = field(default_factory=list)
    dataset: dict[str, Any] = field(default_factory=dict)
    training_seconds: float = 0.0
    # Per-candidate bookkeeping, kept out of `candidates` so selection stays clean.
    cv_by_candidate: dict[str, dict[str, Any]] = field(default_factory=dict)
    metrics_by_candidate: dict[str, dict[str, Any]] = field(default_factory=dict)
    seconds_by_candidate: dict[str, float] = field(default_factory=dict)
    preds_by_candidate: dict[str, tuple[Any, Any, Any]] = field(default_factory=dict)


def data_hash(df: pd.DataFrame, columns: Sequence[str] | None = None) -> str:
    """Stable content hash of the training frame — recorded in every manifest."""
    cols = list(columns) if columns else list(df.columns)
    payload = df[cols].to_csv(index=False, float_format="%.6g").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def library_versions() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "xgboost": xgboost.__version__,
    }


def _score(y_true, y_pred, y_proba, task: str, classes=None) -> dict[str, Any]:
    if task == "regression":
        return M.regression_metrics(y_true, y_pred)
    if task == "binary":
        return M.binary_metrics(y_true, y_pred, y_proba, labels=[0, 1])
    return M.multiclass_metrics(y_true, y_pred, y_proba, classes=classes)


def _proba(estimator, X, task: str):
    """Return (predictions, proba_or_None, proba_classes_or_None)."""
    if task == "regression":
        return estimator.predict(X), None, None
    predictions = estimator.predict(X)
    proba = classes = None
    if hasattr(estimator, "predict_proba"):
        try:
            proba = estimator.predict_proba(X)
            classes = list(estimator.classes_)
        except (AttributeError, NotImplementedError, ValueError):
            proba, classes = None, None
    return predictions, proba, classes


def train_and_select(
    *,
    name: str,
    task: str,
    X_train,
    y_train,
    X_test,
    y_test,
    candidates: dict[str, Any],
    primary_metric: str,
    higher_is_better: bool = True,
    classes: Sequence[Any] | None = None,
    cv: int = CV_FOLDS,
    random_state: int = RANDOM_SEED,
    log_mlflow: bool = True,
    feature_names: Sequence[str] | None = None,
    params_for_log: dict[str, Any] | None = None,
) -> SweepResult:
    """Fit every candidate, pick the winner on CV, then report held-out test metrics."""
    result = SweepResult(
        name=name,
        task=task,
        primary_metric=primary_metric,
        selection_criterion=(
            f"{cv}-fold stratified cross-validation on {primary_metric}"
            + (" (higher is better)" if higher_is_better else " (lower is better)")
        ),
        classes=list(classes) if classes is not None else [],
    )

    splitter = StratifiedKFold(n_splits=cv, shuffle=True, random_state=random_state)

    # sklearn's text pipelines need an object-dtype array, not a bare Python list —
    # a list makes cross_val_predict coerce the documents into a sparse matrix.
    if isinstance(X_train, list):
        X_train = np.asarray(X_train, dtype=object)
    if isinstance(X_test, list):
        X_test = np.asarray(X_test, dtype=object)

    # Tracking is opt-in: never let an unreachable MLflow server stall a training run.
    mlflow_active = bool(log_mlflow) and _tracking_enabled()
    if mlflow_active:
        try:
            mlflow.set_tracking_uri(_tracking_uri())
        except Exception:  # pragma: no cover - tracking is best-effort
            mlflow_active = False
    else:
        print("    [mlflow] tracking disabled (MLFLOW_TRACKING_ENABLED is not set)")

    for candidate_name, estimator in candidates.items():
        folds: list[dict[str, Any]] = []
        try:
            if task == "regression":
                # Time-ordered folds are not available per-candidate, so use KFold
                # with shuffling off is wrong for temporal data; use the same
                # stratified-equivalent (KFold) for a fair comparison.
                from sklearn.model_selection import KFold

                fold_iter = KFold(n_splits=cv, shuffle=True, random_state=random_state).split(
                    X_train
                )
                oof_pred = cross_val_predict(estimator, X_train, y_train, cv=fold_iter)
                fold_metrics = _score(y_train, oof_pred, None, task)
                folds = [
                    {
                        "fold": "oof",
                        **{
                            k: v
                            for k, v in fold_metrics.items()
                            if isinstance(v, (int, float))
                        },
                    }
                ]
            else:
                oof_pred = cross_val_predict(
                    estimator,
                    X_train,
                    y_train,
                    cv=list(split_iter_gen(splitter, X_train, y_train)),
                )
                fold_metrics = _score(y_train, oof_pred, None, task, classes)
                folds = [
                    {
                        "fold": "oof",
                        **{
                            k: v
                            for k, v in fold_metrics.items()
                            if isinstance(v, (int, float))
                        },
                    }
                ]

            cv_summary = M.summarise_cv(folds, primary_metric)
            start = time.perf_counter()
            estimator.fit(X_train, y_train)
            fit_seconds = round(time.perf_counter() - start, 4)
            test_pred, test_proba, proba_classes = _proba(estimator, X_test, task)
            test_metrics = _score(y_test, test_pred, test_proba, task, classes)

            row = {
                "model": candidate_name,
                "accuracy": test_metrics.get("accuracy"),
                "precision": test_metrics.get("precision"),
                "recall": test_metrics.get("recall"),
                "f1": test_metrics.get("f1"),
                "f1_macro": test_metrics.get("f1_macro"),
                "roc_auc": test_metrics.get("roc_auc") or test_metrics.get("roc_auc_ovr"),
                "mae": test_metrics.get("mae"),
                "rmse": test_metrics.get("rmse"),
                "r2": test_metrics.get("r2"),
                "cv_" + primary_metric: cv_summary["cv_mean"],
                "cv_std": cv_summary["cv_std"],
                "selected": False,
            }
            result.candidates[candidate_name] = estimator
            result.rows.append({k: v for k, v in row.items() if v is not None})
            result.rows[-1]["training_time_seconds"] = fit_seconds
            result.cv_by_candidate[candidate_name] = cv_summary
            result.metrics_by_candidate[candidate_name] = test_metrics
            result.seconds_by_candidate[candidate_name] = fit_seconds
            result.preds_by_candidate[candidate_name] = (test_pred, test_proba, proba_classes)
            if mlflow_active:
                try:
                    import mlflow as _mlflow

                    with _mlflow.start_run(
                        run_name=f"{name}/{candidate_name}", nested=True
                    ) as _run:
                        _mlflow.log_params(
                            {
                                "model": candidate_name,
                                "task": task,
                                **{
                                    k: v
                                    for k, v in params_for_log.items()
                                    if isinstance(v, (int, float, str, bool))
                                },
                            }
                        )
                        M_logged = {
                            k: v
                            for k, v in test_metrics.items()
                            if isinstance(v, (int, float)) and not isinstance(v, bool)
                        }
                        _mlflow.log_metrics({f"test_{k}": v for k, v in M_logged.items()})
                        _mlflow.log_metrics(
                            {
                                f"cv_{k}": v
                                for k, v in cv_summary.items()
                                if isinstance(v, (int, float))
                            }
                        )
                        _mlflow.log_metric("fit_seconds", fit_seconds)
                except Exception:  # pragma: no cover
                    pass
            print(
                f"    [cv ] {candidate_name:<22} cv_{primary_metric}="
                f"{cv_summary['cv_mean']:.4f}  test_"
                f"{primary_metric}={test_metrics.get(primary_metric)}"
            )
        except Exception as exc:  # a failing candidate must not kill the sweep
            print(
                f"    [warn] candidate '{candidate_name}' failed: {type(exc).__name__}: {exc}"
            )
            if os.getenv("ML_DEBUG", "0").lower() in {"1", "true", "yes"}:
                traceback.print_exc()
            continue

    if not result.candidates:
        raise RuntimeError(f"every candidate model failed for task '{name}'")

    # --- selection: best CV score; ties broken by the test metric then by name
    def sort_key(row: dict[str, Any]) -> tuple:
        cv_score = row.get("cv_" + primary_metric, 0.0)
        test_score = row.get(primary_metric, 0.0)
        if higher_is_better:
            return (cv_score, test_score, row["model"])
        return (-cv_score, -test_score, row["model"])

    ranking = sorted(result.rows, key=sort_key, reverse=True)
    result.selected = ranking[0]["model"]
    for row in result.rows:
        row["selected"] = row["model"] == result.selected

    result.estimator = result.candidates[result.selected]
    result.test_metrics = result.metrics_by_candidate[result.selected]
    result.cv_summary = result.cv_by_candidate[result.selected]
    result.training_seconds = result.seconds_by_candidate[result.selected]
    (result.test_predictions, result.test_proba, result.classes_proba) = (
        result.preds_by_candidate[result.selected]
    )
    return result


def split_iter_gen(splitter, X, y):
    """Materialise the CV splits so cross_val_predict can reuse the same folds."""
    return splitter.split(X, y)


def _tracking_uri() -> str:
    import os

    return os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")


def _tracking_enabled() -> bool:
    import os

    return os.getenv("MLFLOW_TRACKING_ENABLED", "0").lower() in {"1", "true", "yes"}


def start_run(name: str, params: dict[str, Any], tags: dict[str, str] | None = None):
    """Start an MLflow run; returns None when tracking is unavailable or disabled."""
    if not _tracking_enabled():
        return None
    try:
        mlflow.set_tracking_uri(_tracking_uri())
        run = mlflow.start_run(run_name=name, tags=tags or {})
        mlflow.log_params(_flatten(params))
        return run
    except Exception as exc:  # pragma: no cover - never fail a training run on tracking
        print(f"  [mlflow] tracking unavailable ({exc}); continuing without it")
        return None


def _flatten(params: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for k, v in (params or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            flat.update(_flatten(v, prefix=f"{key}."))
        elif isinstance(v, (list, tuple)):
            flat[key] = ",".join(map(str, v))[:500]
        elif isinstance(v, (int, float, str, bool)) or v is None:
            flat[key] = v
        else:
            flat[key] = str(v)[:500]
    return flat


def log_metrics(run, metrics: dict[str, Any], prefix: str = "") -> None:
    if run is None or not metrics:
        return
    import mlflow

    flat = {
        f"{prefix}{k}": v
        for k, v in metrics.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    if flat:
        mlflow.log_metrics(flat)


def save_artifact(
    name: str,
    version: int,
    estimator,
    manifest: dict[str, Any],
    extra_files: dict[str, Any] | None = None,
) -> Path:
    """Persist the estimator plus a manifest that fully describes the run."""
    out_dir = ARTIFACT_DIR / name / f"v{version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(estimator, out_dir / "model.joblib", compress=3)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    for filename, payload in (extra_files or {}).items():
        (out_dir / filename).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
    return out_dir


def write_report(name: str, version: int, payload: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}_v{version}_report.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def write_markdown(name: str, version: int, markdown: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}_v{version}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
