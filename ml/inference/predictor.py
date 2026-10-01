"""Inference functions for the four models. The backend calls exactly these.

Each function takes plain Python inputs (no DataFrame required from the caller) and
returns a JSON-serialisable dict, so the API layer stays free of ML concerns.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from features.defect_features import FEATURE_COLUMNS
from inference.registry import LoadedModel, get_registry
from preprocessing.clean_text import build_document, clean_text

# Numeric range guards: the model was trained on real OSS history, so absurd inputs get
# clipped to the training range rather than extrapolated into nonsense.
LIMITS: dict[str, tuple[float, float]] = {
    "log_additions": (0, 12),
    "log_deletions": (0, 12),
    "log_churn": (0, 13),
    "log_files": (0, 7),
    "net_lines": (-2_000_000, 2_000_000),
    "churn_ratio": (0, 1),
    "additions_per_file": (0, 5000),
    "deletions_per_file": (0, 5000),
    "files_touched": (1, 400),
    "message_length": (0, 2000),
    "author_commit_count": (0, 20000),
    "author_defect_rate": (0, 1),
    "author_repo_share": (0, 1),
    "author_tenure_days": (0, 20000),
    "days_since_author_prev": (0, 3650),
    "hours_since_repo_last": (0, 87600),
    "repo_commits_last_7d": (0, 5000),
    "repo_commits_last_30d": (0, 20000),
    "repo_distinct_authors_30d": (0, 2000),
    "repo_defect_rate_30d": (0, 1),
    "repo_churn_last_30d": (0, 5e7),
    "repo_age_days": (0, 20000),
    "path_change_count": (0, 10000),
    "path_defect_rate": (0, 1),
    "path_age_days": (0, 20000),
    "paths_avg_age": (0, 20000),
    "paths_ever_touched_by_author": (0, 60),
}


def input_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]


def _clip(name: str, value: float) -> float:
    lo, hi = LIMITS.get(name, (-1e12, 1e12))
    if not math.isfinite(value):
        return 0.0
    return float(min(max(float(value), lo), hi))


# ------------------------------------------------------------------ MODEL 1
def predict_defect_risk(
    *,
    repository: str,
    changes: list[dict[str, Any]],
    author: str | None = None,
    commit_message: str | None = None,
    pull_request_size: int | None = None,
    registry=None,
) -> dict[str, Any]:
    """Score a proposed change set. Returns risk band, probability and explanations."""
    model: LoadedModel = (registry or get_registry()).require("defect_risk")
    if not changes:
        raise ValueError("at least one file change is required")

    additions = float(sum(c.get("additions", 0) for c in changes))
    deletions = float(sum(c.get("deletions", 0) for c in changes))
    files = float(len(changes))
    paths = [c.get("path", "") for c in changes]
    subject = (commit_message or "").split("\n", 1)[0][:300]

    is_test = any(
        re.search(
            r"(^|/)(tests?|spec|specs|__tests__)(/|$)|"
            r"(^|/)test_|_test\.|\.test\.",
            p,
            re.I,
        )
        for p in paths
    )
    is_doc = (
        all(re.search(r"\.(md|rst|txt|adoc)$", p, re.I) for p in paths) if paths else False
    )
    is_vendor = any(
        re.search(r"(^|/)(node_modules|vendor|third_party|site-packages)(/|$)", p, re.I)
        for p in paths
    )
    is_lock = any(
        re.search(
            r"(package-lock\.json|yarn\.lock|poetry\.lock|Cargo\.lock|"
            r"composer\.lock|go\.sum)$",
            p,
            re.I,
        )
        for p in paths
    )

    test_additions = sum(
        c.get("additions", 0)
        for c in changes
        if re.search(
            r"(^|/)(tests?|spec|specs|__tests__)(/|$)|(^|/)test_|_test\.|\.test\.",
            c.get("path", ""),
            re.I,
        )
    )
    source_additions = additions - test_additions

    row = {
        # the change
        "log_additions": _clip("log_additions", math.log1p(additions)),
        "log_deletions": _clip("log_deletions", math.log1p(deletions)),
        "log_churn": _clip("log_churn", math.log1p(additions + deletions)),
        "log_files": _clip("log_files", math.log1p(files)),
        "net_lines": _clip("net_lines", additions - deletions),
        "churn_ratio": _clip("churn_ratio", additions / max(1.0, additions + deletions)),
        "additions_per_file": _clip("additions_per_file", additions / max(1.0, files)),
        "deletions_per_file": _clip("deletions_per_file", deletions / max(1.0, files)),
        "is_merge": 0.0,
        "is_bugfix": 1.0 if re.search(r"\b(fix|bug|hotfix|patch)\b", subject, re.I) else 0.0,
        "commit_hour": 12.0,
        "commit_weekday": 2.0,
        "is_weekend": 0.0,
        "is_business_hours": 1.0,
        "message_length": _clip("message_length", len(subject)),
        "has_issue_ref": 1.0 if re.search(r"#\d+", subject) else 0.0,
        "message_question_ratio": _clip(
            "message_question_ratio", subject.count("?") / max(1, len(subject))
        ),
        # composition
        "files_touched": _clip("files_touched", files),
        "files_source": float(
            sum(
                1
                for p in paths
                if re.search(r"\.(py|js|ts|tsx|jsx|go|rs|java|rb|c|cc|cpp|h|cs|php)$", p, re.I)
            )
        ),
        "files_test": 1.0 if is_test else 0.0,
        "files_doc": 1.0 if is_doc else 0.0,
        "files_generated": float(
            sum(1 for p in paths if re.search(r"(\.min\.(js|css)$|\.lock$|\.map$)", p, re.I))
        ),
        "files_other": 0.0,
        "additions_test": _clip("additions_test", float(test_additions)),
        "additions_source": _clip("additions_source", float(source_additions)),
        "additions_doc": 0.0,
        "deletions_test": 0.0,
        "deletions_source": 0.0,
    }
    # History-dependent features are unknown for an uncommitted change. Rather than
    # inventing them we use neutral, clearly-documented priors and say so in the
    # explanation so a reader knows these did not carry predictive weight.
    for name, prior in (
        ("author_commit_count", 0.0),
        ("author_defect_rate", 0.0),
        ("author_repo_share", 0.0),
        ("author_tenure_days", 0.0),
        ("days_since_author_prev", 365.0),
        ("hours_since_repo_last", 168.0),
        ("repo_commits_last_7d", 0.0),
        ("repo_commits_last_30d", 0.0),
        ("repo_distinct_authors_30d", 0.0),
        ("repo_defect_rate_30d", 0.0),
        ("repo_churn_last_30d", 0.0),
        ("repo_age_days", 0.0),
        ("path_change_count", 0.0),
        ("path_defect_rate", 0.0),
        ("path_age_days", 0.0),
        ("paths_avg_age", 0.0),
        ("paths_ever_touched_by_author", 0.0),
    ):
        row[name] = _clip(name, prior)
    row["path_is_lockfile"] = 1.0 if is_lock else 0.0
    row["path_is_vendor"] = 1.0 if is_vendor else 0.0
    row["path_is_test"] = 1.0 if is_test else 0.0
    row["path_is_doc"] = 1.0 if is_doc else 0.0

    X = np.array([[row.get(f, 0.0) for f in FEATURE_COLUMNS]], dtype=float)
    proba = float(model.estimator.predict_proba(X)[0][1])
    level = model.risk_level(proba)

    return {
        "risk_level": level,
        "probability": round(proba, 4),
        "message": _risk_message(level, proba),
        "contributing_factors": _defect_factors(row, proba, model),
        "model_name": "defect_risk",
        "model_version": model.tag,
        "metrics_at_prediction_time": {
            k: model.metrics.get(k) for k in ("f1", "recall", "roc_auc", "accuracy")
        },
        "input_hash": input_hash(
            {
                "repository": repository,
                "changes": changes,
                "author": author,
                "message": commit_message,
            }
        ),
        "note": (
            "Repository/author history features are set to neutral priors because the "
            "change is not yet in the ingested history. Re-scoring after the change is "
            "committed gives the model real history to work with."
        ),
    }


def _risk_message(level: str, probability: float) -> str:
    if level == "HIGH":
        return (
            f"HIGH defect risk ({probability:.0%}). Recommend extra review, a smaller "
            f"change set, and explicit test coverage before merge."
        )
    if level == "MEDIUM":
        return (
            f"MEDIUM defect risk ({probability:.0%}). Standard review plus a focused "
            f"regression test is advisable."
        )
    return f"LOW defect risk ({probability:.0%}). Normal review process is appropriate."


def _defect_factors(
    row: dict[str, float], probability: float, model: LoadedModel
) -> list[dict[str, Any]]:
    importance = {
        f["feature"]: f["importance"] for f in model.manifest.get("feature_importance", [])
    }
    highlights = []
    for name, value, unit in (
        ("log_churn", row["log_churn"], "log lines changed"),
        ("log_files", row["log_files"], "log files changed"),
        ("path_is_test", row["path_is_test"], "touches tests"),
        ("path_is_vendor", row["path_is_vendor"], "touches vendored code"),
        ("path_is_lockfile", row["path_is_lockfile"], "touches a lockfile"),
        ("is_bugfix", row["is_bugfix"], "message announces a fix"),
    ):
        if value:
            highlights.append(
                {
                    "feature": name,
                    "value": round(float(value), 4),
                    "unit": unit,
                    "model_importance": round(float(importance.get(name, 0.0)), 6),
                }
            )
    highlights.sort(key=lambda h: -h["model_importance"])
    return highlights[:5]


# ------------------------------------------------------------------ MODEL 2
def classify_issue(
    *, title: str, body: str | None = None, labels: list[str] | None = None, registry=None
) -> dict[str, Any]:
    """TF-IDF issue classifier. Returns the category, confidence and full distribution."""
    model: LoadedModel = (registry or get_registry()).require("issue_classifier")
    document = build_document(title, body)
    proba = model.estimator.predict_proba([document])[0]
    classes = list(getattr(model.estimator, "classes_", model.classes))
    order = np.argsort(proba)[::-1]
    top = int(order[0])

    return {
        "category": str(classes[top]),
        "confidence": round(float(proba[top]), 4),
        "probabilities": {str(c): round(float(p), 4) for c, p in zip(classes, proba)},
        "top_features": _top_terms(model.estimator, document),
        "model_name": "issue_classifier",
        "model_version": model.tag,
        "input_hash": input_hash({"t": title, "b": body, "l": labels or []}),
    }


def _top_terms(estimator, document: str, top: int = 8) -> list[str]:
    """Terms present in *this* document with the largest weight for the predicted class.

    Explanation is best-effort: any failure returns an empty list rather than breaking
    the prediction.
    """
    try:
        steps = estimator.named_steps
        features = steps.get("features")
        clf = steps.get("clf")
        if features is None or clf is None:
            return []
        word = (
            features.transformer_list["word"][1]
            if hasattr(features, "transformer_list")
            else None
        )
        if word is None:
            return []

        names = np.asarray(word.get_feature_names_out())
        row = np.asarray(word.transform([document]).todense()).ravel()
        if row.sum() == 0:
            return []
        coef = np.asarray(clf.coef_)
        if coef.ndim == 1:
            weights = np.abs(coef)
        else:
            inner = getattr(clf, "estimator", clf)  # unwrap CalibratedClassifierCV
            inner_coef = np.asarray(inner.coef_)
            weights = np.abs(inner_coef).mean(axis=0)
        scores = row * weights
        order = np.argsort(scores)[::-1][:top]
        return [f"{names[i]} ({scores[i]:.4f})" for i in order if scores[i] > 0]
    except Exception:  # pragma: no cover - never fail a prediction over a nicety
        return []


def _issue_feature_frame(
    title: str,
    body: str | None,
    labels: list[str] | None,
    comments_count: int,
    author_association: str | None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Build the exact feature frame the issue models were trained on.

    The hybrid estimators select their structured columns *by name* during `fit`, so the
    inference frame must carry every one of them — not only the text columns. The
    structured values are also returned for the response explanation.
    """
    from features.issue_features import STRUCTURED_COLUMNS, structured_features

    document = build_document(title, body)
    base = {
        "labels": list(labels or []),
        "_text": document,
        "_title_text": clean_text(title),
        "comments_count": comments_count,
        "author_association": author_association,
    }
    features = structured_features(pd.Series(base))
    # Deduplicate while preserving order: `comments_count` is both a base key and a
    # structured feature, and pandas' column selection silently collapses the duplicate —
    # which would leave the scaler one column short of what it was fitted on.
    columns: list[str] = []
    for name in [*STRUCTURED_COLUMNS, "_text", "_title_text"]:
        if name not in columns:
            columns.append(name)
    return pd.DataFrame([{**base, **features}])[columns], features


# ------------------------------------------------------------------ MODEL 3
def predict_priority(
    *,
    title: str,
    body: str | None = None,
    labels: list[str] | None = None,
    comments_count: int = 0,
    author_association: str | None = None,
    linked_prs: int = 0,
    registry=None,
) -> dict[str, Any]:
    """Advisory priority estimate. Always carries the human-triage disclaimer."""
    model: LoadedModel = (registry or get_registry()).require("issue_priority")
    frame, features = _issue_feature_frame(
        title, body, labels, comments_count, author_association
    )
    proba = model.estimator.predict_proba(frame)[0]
    classes = list(getattr(model.estimator, "classes_", model.classes))
    top = int(np.argmax(proba))

    contributing = [name for name, value in features.items() if value and value != 1.0]
    return {
        "priority": str(classes[top]),
        "confidence": round(float(proba[top]), 4),
        "probabilities": {str(c): round(float(p), 4) for c, p in zip(classes, proba)},
        "contributing_factors": contributing[:8],
        "disclaimer": (
            "This priority is a machine-learning estimate derived from patterns in "
            "historical issues. It is an advisory signal only and must not be treated as "
            "an authoritative priority decision. Human triage remains the source of truth."
        ),
        "model_name": "issue_priority",
        "model_version": model.tag,
        "input_hash": input_hash(
            {
                "t": title,
                "b": body,
                "l": labels or [],
                "c": comments_count,
                "a": author_association,
            }
        ),
    }


# ------------------------------------------------------------------ MODEL 4
def estimate_effort(
    *,
    title: str,
    body: str | None = None,
    labels: list[str] | None = None,
    comments_count: int = 0,
    author_association: str | None = None,
    registry=None,
) -> dict[str, Any]:
    """Estimate resolution effort in hours, with a residual-spread interval."""
    model: LoadedModel = (registry or get_registry()).require("issue_effort")
    frame, _features = _issue_feature_frame(
        title, body, labels, comments_count, author_association
    )
    hours = float(model.estimator.predict(frame)[0])
    interval = model.manifest.get("interval", {})
    iqr = float(interval.get("iqr_hours") or 0.0)

    mae = float(model.metrics.get("mae") or 0.0)
    return {
        "estimated_hours": round(max(0.25, hours), 2),
        "estimate_low_hours": round(max(0.25, hours - iqr), 2),
        "estimate_high_hours": round(max(0.25, hours + iqr), 2),
        "unit": "hours",
        "confidence": round(max(0.05, min(0.95, 1.0 - min(0.9, mae / max(1.0, hours)))), 4),
        "contributors": {
            "mean_absolute_error_hours": round(mae, 3),
            "residual_iqr_hours": round(iqr, 3),
            "within_20pct_rate": model.metrics.get("within_20pct"),
            "r2": model.metrics.get("r2"),
        },
        "methodology_note": (
            "Trained on time-to-close (created_at → closed_at) of historical GitHub "
            "issues, because GitHub does not record engineering hours. Time-to-close "
            "includes queue and wait time, so this is a coarse proxy for hands-on "
            "effort rather than a timesheet measurement."
        ),
        "model_name": "issue_effort",
        "model_version": model.tag,
        "input_hash": input_hash(
            {"t": title, "b": body, "l": labels or [], "c": comments_count}
        ),
    }
