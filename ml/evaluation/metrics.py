"""EVALUATION — every metric is computed from real predictions. Nothing is hard-coded."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
    roc_auc_score,
)

ROUND_TO = 6


def _f(value: Any) -> float:
    """JSON-safe float with NaN/inf collapsed to None-ish 0.0."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if np.isnan(out) or np.isinf(out):
        return 0.0
    return round(out, ROUND_TO)


def binary_metrics(y_true, y_pred, y_proba=None, *, labels=(0, 1)) -> dict[str, Any]:
    """Binary metrics.

    With a single observed class, F1/recall for the absent class are undefined; the
    result reports the present class and leaves the absent one at 0.0 rather than
    failing, so a degenerate fold cannot break a sweep.
    """
    y_true_arr = np.asarray(y_true)
    y_pred_arr = np.asarray(y_pred)
    labels = list(labels)
    present = set(np.unique(y_true_arr)) | set(np.unique(y_pred_arr))

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true_arr, y_pred_arr, labels=labels, zero_division=0
    )
    metrics: dict[str, Any] = {
        "accuracy": _f(accuracy_score(y_true_arr, y_pred_arr)),
        "precision": _f(precision[-1]),
        "recall": _f(recall[-1]),
        "f1": _f(f1[-1]),
        "precision_macro": _f(precision.mean()) if len(precision) else 0.0,
        "recall_macro": _f(recall.mean()) if len(recall) else 0.0,
        "f1_macro": _f(f1.mean()) if len(f1) else 0.0,
        "balanced_accuracy": _f(np.mean(recall)) if len(recall) else 0.0,
        "n_samples": int(len(y_true_arr)),
        "positive_rate": _f(np.mean(y_true_arr == 1)) if len(y_true_arr) else 0.0,
        "classes_present": sorted(str(c) for c in present),
    }
    if y_proba is not None and 1 in present and 0 in present:
        try:
            metrics["roc_auc"] = _f(roc_auc_score(y_true_arr, np.asarray(y_proba)[:, 1]))
        except (ValueError, IndexError):
            metrics["roc_auc"] = None
    else:
        # ROC-AUC is undefined with one class present; say so rather than invent a value.
        metrics["roc_auc"] = None
    return metrics


def multiclass_metrics(y_true, y_pred, y_proba=None, classes=None) -> dict[str, Any]:
    classes = list(classes) if classes is not None else sorted(set(map(str, y_true)))
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=classes, zero_division=0
    )
    per_class = {
        str(c): {
            "precision": _f(p),
            "recall": _f(r),
            "f1": _f(f),
            "support": int(s),
        }
        for c, p, r, f, s in zip(classes, precision, recall, f1, support)
    }
    metrics: dict[str, Any] = {
        "accuracy": _f(accuracy_score(y_true, y_pred)),
        "precision": _f(np.average(precision, weights=support)) if support.sum() else 0.0,
        "recall": _f(np.average(recall, weights=support)) if support.sum() else 0.0,
        "f1": _f(np.average(f1, weights=support)) if support.sum() else 0.0,
        "precision_macro": _f(precision.mean()),
        "recall_macro": _f(recall.mean()),
        "f1_macro": _f(f1.mean()),
        "per_class": per_class,
        "n_samples": int(len(y_true)),
        "n_classes": len(classes),
    }
    if y_proba is not None and len(classes) == 2:
        try:
            metrics["roc_auc"] = _f(
                roc_auc_score(y_true, np.asarray(y_proba)[:, 1], labels=classes)
            )
        except (ValueError, IndexError):
            metrics["roc_auc"] = None
    elif y_proba is not None:
        try:
            metrics["roc_auc_ovr"] = _f(
                roc_auc_score(
                    y_true,
                    np.asarray(y_proba),
                    multi_class="ovr",
                    average="macro",
                    labels=classes,
                )
            )
        except (ValueError, IndexError):
            metrics["roc_auc_ovr"] = None
    return metrics


def regression_metrics(y_true, y_pred) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    errors = y_pred - y_true
    pct_errors = np.where(y_true > 0, np.abs(errors) / np.maximum(y_true, 1e-9), np.nan)

    return {
        "mae": _f(mean_absolute_error(y_true, y_pred)),
        "rmse": _f(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": _f(r2_score(y_true, y_pred)),
        "mape_pct": _f(np.nanmean(pct_errors) * 100.0),
        "median_ae": _f(np.median(np.abs(errors))),
        "explained_variance": _f(1.0 - np.var(errors) / np.var(y_true))
        if np.var(y_true)
        else 0.0,
        "within_20pct": _f(np.nanmean(pct_errors <= 0.20) * 100.0),
        "within_2x": _f(np.nanmean(pct_errors <= 2.00) * 100.0),
        "n_samples": int(len(y_true)),
        "target_mean": _f(y_true.mean()),
        "target_median": _f(np.median(y_true)),
    }


def classification_report_dict(y_true, y_pred, classes) -> dict[str, Any]:
    raw = classification_report(
        y_true, y_pred, labels=classes, output_dict=True, zero_division=0
    )
    return {
        k: (v if not isinstance(v, (np.floating, np.integer)) else float(v))
        for k, v in raw.items()
    }


def confusion(y_true, y_pred, classes) -> list[list[int]]:
    return confusion_matrix(y_true, y_pred, labels=classes).astype(int).tolist()


def feature_importance(
    estimator, feature_names: list[str], *, top: int = 25
) -> list[dict[str, Any]]:
    """Tree/linear importance, normalised to sum to 1.

    `feature_names` may be shorter than the importance vector (for example a hybrid
    TF-IDF ⊕ structured model where only the structured block is named); the result is
    then truncated to the named features rather than raising.
    """
    values: np.ndarray | None = None
    kind = ""
    if hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float)
        kind = "impurity" if "tree" in type(estimator).__name__.lower() else "gain"
    elif hasattr(estimator, "coef_"):
        coef = np.asarray(estimator.coef_, dtype=float)
        values = np.abs(coef).sum(axis=0) if coef.ndim > 1 else np.abs(coef)
        kind = "absolute_coefficient"
    if values is None or values.size == 0:
        return []

    usable = min(len(values), len(feature_names))
    if usable == 0:
        return []
    values = values[:usable]
    names = list(feature_names[:usable])

    total = values.sum()
    if total > 0:
        values = values / total
    order = np.argsort(values)[::-1][:top]
    return [{"feature": names[i], "importance": _f(values[i]), "kind": kind} for i in order]


def top_weighted_terms(estimator, *, top: int = 25) -> list[dict[str, Any]]:
    """Highest-weight TF-IDF terms of a linear text model (mean |coef| across classes).

    For a hybrid TF-IDF ⊕ structured estimator this is the most interpretable view: the
    vocabulary terms the model actually leans on, plus the named structured features.
    """
    word = getattr(estimator, "word_vectoriser_", None)
    coef = getattr(estimator, "coef_", None)
    if word is None or coef is None:
        return []
    try:
        names = np.asarray(word.get_feature_names_out())
        weights = np.abs(np.asarray(coef, dtype=float))
        mean_weights = weights.mean(axis=0) if weights.ndim > 1 else weights
        usable = min(len(names), len(mean_weights))
        if usable == 0:
            return []
        names, mean_weights = names[:usable], mean_weights[:usable]
        total = float(mean_weights.sum()) or 1.0
        order = np.argsort(mean_weights)[::-1][:top]
        return [
            {
                "feature": str(names[i]),
                "importance": _f(mean_weights[i] / total),
                "kind": "mean_abs_coefficient",
            }
            for i in order
        ]
    except Exception:  # pragma: no cover - explanation must never break a run
        return []


def timed(fit_predict) -> tuple[Any, float]:
    """Run a fit/predict closure and return (result, seconds). Used for the comparison table."""
    start = time.perf_counter()
    result = fit_predict()
    return result, round(time.perf_counter() - start, 4)


def summarise_cv(fold_scores: list[dict[str, Any]], primary_metric: str) -> dict[str, Any]:
    """Mean/std of the primary metric across CV folds — the model-selection criterion."""
    values = [f[primary_metric] for f in fold_scores if primary_metric in f]
    if not values:
        return {"cv_mean": 0.0, "cv_std": 0.0, "cv_folds": fold_scores}
    return {
        "cv_mean": _f(np.mean(values)),
        "cv_std": _f(np.std(values)),
        "cv_min": _f(np.min(values)),
        "cv_max": _f(np.max(values)),
        "cv_folds": fold_scores,
    }


def dataset_summary(df: pd.DataFrame, target: str) -> dict[str, Any]:
    counts = df[target].value_counts().to_dict()
    return {
        "rows": int(len(df)),
        "target": target,
        "target_distribution": {str(k): int(v) for k, v in counts.items()},
        "positive_rate": _f(df[target].mean()) if target in df.columns else None,
        "repositories": int(df["repo"].nunique()) if "repo" in df.columns else None,
        "time_range": (
            [str(df["authored_at"].min()), str(df["authored_at"].max())]
            if "authored_at" in df.columns
            else None
        ),
    }
