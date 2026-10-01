"""Regression guard: the inference feature frame must match what training produced.

The hybrid issue estimators select their structured columns by name and were fitted on a
scaler with a fixed `n_features_in_`. Any drift between the training frame and the
inference frame fails at *prediction* time with an opaque sklearn error, so this test
asserts column agreement directly, and pins the bug that caused it: a duplicate
`comments_count` column produced by concatenating the corpus frame with the engineered
struct frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (str(REPO_ROOT), str(REPO_ROOT / "ml")):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import REPO_ROOT as ML_REPO_ROOT  # noqa: E402
from data.issue_corpus import load_issue_corpus  # noqa: E402
from features.issue_features import (  # noqa: E402
    PRIORITY_STRUCTURED_COLUMNS,
    STRUCTURED_COLUMNS,
    filter_labelled,
    prepare_issue_frame,
)
from inference.predictor import _issue_feature_frame  # noqa: E402


def test_structured_columns_have_no_duplicates():
    assert len(STRUCTURED_COLUMNS) == len(set(STRUCTURED_COLUMNS))
    assert len(PRIORITY_STRUCTURED_COLUMNS) == len(set(PRIORITY_STRUCTURED_COLUMNS))
    # The priority set must exclude every label-derived feature.
    assert "has_bug_label" not in PRIORITY_STRUCTURED_COLUMNS
    assert "has_feature_label" not in PRIORITY_STRUCTURED_COLUMNS
    assert "label_count" not in PRIORITY_STRUCTURED_COLUMNS
    assert len(PRIORITY_STRUCTURED_COLUMNS) < len(STRUCTURED_COLUMNS)


def test_prepared_frame_has_unique_columns():
    """Regression: concatenating struct onto the corpus duplicated `comments_count`."""
    corpus = pd.DataFrame(
        [
            {
                "title": "Something broke",
                "body": "Traceback (most recent call last): AttributeError",
                "labels": ["bug"],
                "comments_count": 3,
                "author_association": "CONTRIBUTOR",
                "repository": "o/r",
                "created_at": 1_600_000_000_000,
                "closed_at": 1_600_086_400_000,
                "is_pull_request": False,
            }
        ]
        * 5
    )
    prepared = prepare_issue_frame(corpus, with_effort=True)

    duplicated = prepared.columns[prepared.columns.duplicated()].tolist()
    assert not duplicated, f"duplicate columns leaked into the frame: {duplicated}"


def test_inference_frame_matches_training_columns():
    """The frame built for inference must be a superset of the model's columns."""
    frame, features = _issue_feature_frame(
        title="Something broke",
        body="Traceback (most recent call last): AttributeError",
        labels=["bug"],
        comments_count=3,
        author_association="CONTRIBUTOR",
    )
    for column in STRUCTURED_COLUMNS:
        assert column in frame.columns, f"inference frame is missing {column}"
    assert len(frame.columns) == len(set(frame.columns)), "inference frame has duplicates"
    assert features["has_bug_label"] == 1.0
    assert features["comments_raw"] == 3.0


def test_training_frame_and_inference_frame_agree_on_column_count():
    """The exact failure mode: a fitted scaler expecting more columns than inference."""
    if not (ML_REPO_ROOT / "data" / "raw" / "github_issues.jsonl").exists():
        pytest.skip("issue corpus has not been acquired")

    raw = load_issue_corpus()
    prepared = prepare_issue_frame(raw, with_effort=True)
    labelled = filter_labelled(prepared, ["priority"])
    assert not labelled.columns.duplicated().any()

    row = labelled.iloc[0]
    train_frame = labelled[[*STRUCTURED_COLUMNS, "_text", "_title_text"]]
    inferred, _ = _issue_feature_frame(
        title=row["title"],
        body=row["body"],
        labels=row["labels"],
        comments_count=int(row["comments_count"]),
        author_association=row["author_association"],
    )
    assert train_frame.shape[1] == inferred.shape[1], (
        f"training frame has {train_frame.shape[1]} columns but the inference frame has "
        f"{inferred.shape[1]}"
    )
