"""ML preprocessing, feature engineering, metrics and model-loading tests.

These run against small fixtures so the suite stays fast; the full-scale numbers come
from the training runs themselves, not from here.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from config import REPO_ROOT
from evaluation import metrics as M
from features.issue_features import (
    CATEGORIES,
    label_category,
    label_priority,
    structured_features,
)
from inference.registry import ModelRegistry
from preprocessing.clean_history import is_fix_intent, path_class
from preprocessing.clean_text import build_document, clean_text


# ------------------------------------------------------------------ text clean
def test_clean_text_strips_code_blocks_urls_and_issue_refs():
    raw = "Bug report\n```python\nraise ValueError()\n```\nSee https://x.io/a#b and #4242"
    cleaned = clean_text(raw)
    assert "valueerror" not in cleaned
    assert "https" not in cleaned
    # Issue references are masked: they appear everywhere and carry no signal.
    assert "#4242" not in cleaned
    assert "bug report" in cleaned


def test_clean_text_handles_none_and_empty():
    assert clean_text(None) == ""
    assert clean_text("") == ""
    assert clean_text("   \n\n  ") == ""


def test_clean_text_unescapes_html():
    assert "<b>" not in clean_text("<b>bold</b> &amp; broken")


def test_build_document_emphasises_the_title():
    document = build_document("crash on startup", "a" * 50)
    # The title is repeated so short titles are not drowned out by long bodies.
    assert document.split().count("crash") == 2


# ------------------------------------------------------------- commit cleaning
@pytest.mark.parametrize(
    "message,expected",
    [
        ("Fix crash on startup", True),
        ("fix: guard against None", True),
        ("HOTFIX for regression in parser", True),
        ("Closes #1234", True),
        ("revert bad commit", True),
        ("Update README", False),
        ("Bump version to 8.2.0", False),
        ("Add typing overloads", False),
        ("", False),
        (None, False),
    ],
)
def test_fix_intent_detection(message, expected):
    assert is_fix_intent(message) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/click/core.py", "source"),
        ("tests/test_core.py", "test"),
        ("spec/foo_spec.rb", "test"),
        ("docs/index.rst", "doc"),
        ("package-lock.json", "generated"),
        ("node_modules/left-pad/index.js", "vendor"),
        ("scripts/deploy.sh", "other"),
    ],
)
def test_path_classification(path, expected):
    assert path_class(path) == expected


# ------------------------------------------------------------ issue labelling
@pytest.mark.parametrize(
    "labels,text,expected",
    [
        (["bug"], "something broke", "BUG"),
        (["enhancement"], "make it faster", "ENHANCEMENT"),
        (["documentation"], "typo in the readme", "DOCUMENTATION"),
        (["question"], "how do I configure this?", "QUESTION"),
        (["feature request"], "please add an option", "FEATURE_REQUEST"),
        (["wontfix", "upstream"], "stale thread", None),
        ([], "Traceback (most recent call last): AttributeError", "BUG"),
    ],
)
def test_label_category(labels, text, expected):
    assert label_category(labels, text) == expected


def test_label_category_returns_none_rather_than_guessing():
    assert label_category(["random-label"], "short text with no signal") is None


@pytest.mark.parametrize(
    "labels,text,comments,expected",
    [
        (["p0"], "", 0, "CRITICAL"),
        (["critical"], "", 0, "CRITICAL"),
        (["data loss"], "", 0, "CRITICAL"),
        (["bug"], "", 0, "HIGH"),
        (["p1"], "", 0, "MEDIUM"),
        (["enhancement"], "", 0, "MEDIUM"),
        (["documentation"], "", 0, "LOW"),
        (["good first issue"], "", 0, "LOW"),
        (["wontfix"], "", 0, "LOW"),
        ([], "we are seeing data loss in production", 0, None),
        ([], "Traceback (most recent call last):", 0, None),
        ([], "just a small nit", 0, None),
        ([], "long neutral discussion without urgency", 0, None),
    ],
)
def test_label_priority_uses_labels_only(labels, text, comments, expected):
    """Text and comment count must NOT influence the target — they are also features."""
    assert label_priority(labels, text, comments) == expected


def test_label_priority_ignores_text_entirely():
    """Regression guard: a text-based fallback would leak into the target."""
    assert label_priority([], "production is down, complete data loss", 99) is None
    assert label_priority(["p0"], "routine cleanup", 0) == "CRITICAL"


def test_structured_features_have_a_stable_schema():
    features = structured_features(
        pd.Series(
            {
                "labels": ["bug"],
                "_text": "crash",
                "_title_text": "crash",
                "comments_count": 4,
                "author_association": "CONTRIBUTOR",
            }
        )
    )
    assert features["has_bug_label"] == 1.0
    assert features["author_association_rank"] == 1.0
    assert all(isinstance(v, float) for v in features.values())


# ------------------------------------------------------------------- metrics
def test_binary_metrics_are_computed_from_predictions():
    y_true = np.array([0, 0, 1, 1, 1, 0])
    y_pred = np.array([0, 1, 1, 1, 0, 0])
    proba = np.array([[0.9, 0.1], [0.6, 0.4], [0.2, 0.8], [0.3, 0.7], [0.6, 0.4], [0.8, 0.2]])
    result = M.binary_metrics(y_true, y_pred, proba)
    assert result["accuracy"] == pytest.approx(4 / 6, abs=1e-6)
    assert 0 <= result["f1"] <= 1
    assert 0 <= result["roc_auc"] <= 1
    assert result["n_samples"] == 6
    assert result["positive_rate"] == pytest.approx(0.5, abs=1e-6)


def test_binary_metrics_handle_a_single_class():
    result = M.binary_metrics([0, 0, 0], [0, 0, 0], None)
    assert result["accuracy"] == 1.0
    assert result["roc_auc"] is None  # undefined, not fabricated


def test_multiclass_metrics_include_per_class_report():
    y_true = ["BUG", "BUG", "QUESTION", "QUESTION", "OTHER"]
    y_pred = ["BUG", "BUG", "QUESTION", "BUG", "OTHER"]
    proba = [
        [0.9, 0.05, 0.05],
        [0.8, 0.1, 0.1],
        [0.1, 0.8, 0.1],
        [0.5, 0.4, 0.1],
        [0.1, 0.1, 0.8],
    ]
    result = M.multiclass_metrics(y_true, y_pred, proba, classes=CATEGORIES)
    assert set(result["per_class"]) == set(CATEGORIES)
    assert result["per_class"]["BUG"]["support"] == 2
    assert result["accuracy"] == pytest.approx(4 / 5, abs=1e-6)


def test_regression_metrics_against_a_known_perfect_fit():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    result = M.regression_metrics(y, y)
    assert result["mae"] == 0.0
    assert result["rmse"] == 0.0
    assert result["r2"] == pytest.approx(1.0)
    assert result["within_20pct"] == pytest.approx(100.0)


def test_regression_metrics_against_a_constant_baseline():
    y_true = np.array([2.0, 4.0, 6.0])
    y_pred = np.array([4.0, 4.0, 4.0])
    result = M.regression_metrics(y_true, y_pred)
    assert result["mae"] == pytest.approx(4 / 3, abs=1e-6)
    assert result["r2"] < 1.0


def test_confusion_matrix_shape_and_sum():
    matrix = M.confusion(["A", "A", "B"], ["A", "B", "B"], ["A", "B"])
    assert len(matrix) == 2 and len(matrix[0]) == 2
    assert sum(sum(row) for row in matrix) == 3


def test_metrics_are_json_serialisable():
    result = M.binary_metrics([0, 1], [0, 1], np.array([[0.8, 0.2], [0.3, 0.7]]))
    json.dumps(result)  # must not raise


def test_cv_summary_reports_mean_and_spread():
    folds = [{"fold": 0, "f1": 0.6}, {"fold": 1, "f1": 0.8}]
    summary = M.summarise_cv(folds, "f1")
    assert summary["cv_mean"] == pytest.approx(0.7)
    assert summary["cv_std"] == pytest.approx(0.1)


# --------------------------------------------------------- feature engineering
def test_defect_features_produce_no_future_leakage():
    """A commit's historical features must not change when later commits are appended."""
    from features.defect_features import build_defect_features

    def make(n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
        base = pd.Timestamp("2023-01-01", tz="UTC")
        # Author assignment must not depend on `n`, or the two runs would describe
        # different histories and the comparison would be meaningless.
        authors = ["alice" if i % 2 == 0 else "bob" for i in range(n)]
        commits = pd.DataFrame(
            {
                "sha": [f"sha{i:03d}" for i in range(n)],
                "repo": ["a/b"] * n,
                "author": authors,
                "authored_at": [base + pd.Timedelta(days=i) for i in range(n)],
                "subject": [f"change number {i}" for i in range(n)],
                "message": [f"change number {i}" for i in range(n)],
                "author_name": [a.title() for a in authors],
                "author_email": [f"{a}@x.io" for a in authors],
                "additions": [10 + i for i in range(n)],
                "deletions": [i for i in range(n)],
                "files_changed": 2,
                "churn": [10 + 2 * i for i in range(n)],
                "is_merge": [False] * n,
                "is_fix_intent": [i % 7 == 0 for i in range(n)],
                "is_bugfix": [i % 7 == 0 for i in range(n)],
                "hour": [10] * n,
                "weekday": [2] * n,
                "is_weekend": [0] * n,
                "is_business_hours": [1] * n,
                "is_defective": [1 if i % 7 == 0 else 0 for i in range(n)],
            }
        )
        files = pd.DataFrame(
            {
                "sha": [f"sha{i:03d}" for i in range(n)],
                "path": [f"src/mod{i % 3}.py" for i in range(n)],
                "old_path": [None] * n,
                "additions": [10] * n,
                "deletions": [1] * n,
                "change_type": ["modified"] * n,
            }
        )
        return commits, files

    commits_10, files_10 = make(10)
    commits_15, files_15 = make(15)

    features_10 = build_defect_features(commits_10, files_10)
    features_15 = build_defect_features(commits_15, files_15)

    earlier = features_10.set_index("sha")
    later = features_15.set_index("sha")
    historical = [
        "author_commit_count",
        "author_defect_rate",
        "repo_commits_last_7d",
        "repo_commits_last_30d",
        "repo_distinct_authors_30d",
        "repo_churn_last_30d",
        "repo_defect_rate_30d",
        "path_change_count",
        "path_defect_rate",
        "days_since_author_prev",
        "repo_age_days",
    ]
    shared = earlier.index.intersection(later.index)
    assert len(shared) == 10, "the two runs should share their first 10 commits"

    for column in historical:
        before = earlier.loc[shared, column].to_numpy()
        after = later.loc[shared, column].to_numpy()
        assert np.allclose(before, after), (
            f"{column} changed when future commits were appended — that is future leakage"
        )


def test_defect_features_have_the_declared_schema():
    from features.defect_features import FEATURE_COLUMNS, ID_COLUMNS, TARGET_COLUMN

    frame = pd.DataFrame(
        {
            "sha": ["a1"],
            "repo": ["o/r"],
            "author": ["x"],
            "authored_at": [pd.Timestamp("2023-01-01", tz="UTC")],
            "subject": ["s"],
            "message": ["s"],
            "author_name": ["X"],
            "author_email": ["x@y.z"],
            "additions": [5],
            "deletions": [1],
            "files_changed": 1,
            "churn": [6],
            "is_merge": [False],
            "is_fix_intent": [False],
            "is_bugfix": [False],
            "hour": [9],
            "weekday": [0],
            "is_weekend": [1],
            "is_business_hours": [0],
            "is_defective": [0],
        }
    )
    files = pd.DataFrame(
        {
            "sha": ["a1"],
            "path": ["a.py"],
            "old_path": [None],
            "additions": [5],
            "deletions": [1],
            "change_type": ["added"],
        }
    )
    from features.defect_features import build_defect_features

    out = build_defect_features(frame, files)
    assert list(out.columns) == ID_COLUMNS + FEATURE_COLUMNS + [TARGET_COLUMN]
    assert out[FEATURE_COLUMNS].notna().all().all()
    assert not np.isinf(out[FEATURE_COLUMNS].to_numpy()).any()


# ------------------------------------------------------------------- registry
def test_registry_reports_available_models():
    registry = ModelRegistry(REPO_ROOT / "ml" / "artifacts")
    names = registry.names()
    assert isinstance(names, list)
    # Whatever exists on disk, the API must never crash on it.
    for name in names:
        model = registry.require(name)
        assert model.tag == f"{name}-v{model.version}"
        assert isinstance(model.metrics, dict)


def test_registry_raises_for_an_unknown_model():
    registry = ModelRegistry(REPO_ROOT / "ml" / "artifacts")
    with pytest.raises(KeyError):
        registry.require("definitely_not_a_model")


def test_registry_risk_banding_matches_the_documented_thresholds():
    from config import RISK_BANDS
    from inference.registry import LoadedModel

    model = LoadedModel("defect_risk", 1, None, {}, Path("."))
    assert model.risk_level(0.0) == "LOW"
    assert model.risk_level(0.32) == "LOW"
    assert model.risk_level(0.33) == "MEDIUM"
    assert model.risk_level(0.65) == "MEDIUM"
    assert model.risk_level(0.66) == "HIGH"
    assert model.risk_level(0.99) == "HIGH"
    assert [level for level, _ in RISK_BANDS] == ["LOW", "MEDIUM", "HIGH"]


# --------------------------------------------------- trained artefact manifests
@pytest.mark.parametrize(
    "name", ["defect_risk", "issue_classifier", "issue_priority", "issue_effort"]
)
def test_trained_manifests_are_complete_when_present(name):
    """If a model has been trained, its manifest must document target + limitations."""
    root = REPO_ROOT / "ml" / "artifacts" / name
    if not root.exists():
        pytest.skip(f"{name} has not been trained yet")
    manifest_path = sorted(root.glob("v*/manifest.json"))[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == name
    assert manifest["algorithm"]
    assert manifest["target_description"], "the target must be documented, not implicit"
    assert manifest["limitations"], "limitations must be stated explicitly"
    assert manifest["data_version"]
    assert manifest["data_hash"]
    assert manifest["trained_at"]
    assert manifest["metrics"], "metrics must be measured, never hard-coded"
    assert manifest["comparison"], "the model comparison table must be recorded"
