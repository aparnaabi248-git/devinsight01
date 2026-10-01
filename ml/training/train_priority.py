"""PHASE 10 — train the issue-priority model (CRITICAL / HIGH / MEDIUM / LOW)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.base import clone  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402

from config import (  # noqa: E402
    CV_FOLDS,
    DATA_VERSION,
    LIMITATIONS,
    RANDOM_SEED,
    TARGET_DESCRIPTIONS,
)
from data.issue_corpus import corpus_provenance, load_issue_corpus  # noqa: E402
from evaluation import metrics as M  # noqa: E402
from features.issue_features import (  # noqa: E402
    PRIORITIES,
    PRIORITY_STRUCTURED_COLUMNS,
    filter_labelled,
    prepare_issue_frame,
)
from training.common import (  # noqa: E402
    data_hash,
    library_versions,
    log_metrics,
    save_artifact,
    start_run,
    train_and_select,
    utcnow_iso,
    write_markdown,
    write_report,
)

STRUCTURED_COLUMNS = PRIORITY_STRUCTURED_COLUMNS

MODEL_NAME = "issue_priority"
PRIMARY_METRIC = "f1_macro"


def hybrid_pipeline(name: str):
    """One candidate: TF-IDF over the title ⊕ 22 scaled structured features → linear model.

    The structured block carries the interpretable signals (label severity, discussion
    volume, stack traces, length); the text block carries the wording. Concatenating them
    lets a single linear model use both.
    """
    from models.candidates import text_classifier_candidates
    from models.hybrid import HybridTextClassifier

    base = text_classifier_candidates()[name]
    estimator = clone(base.named_steps["clf"])
    return HybridTextClassifier(
        estimator=estimator,
        text_column="_text",
        title_column="_title_text",
        columns=list(STRUCTURED_COLUMNS),
    )


def build_dataset() -> tuple[pd.DataFrame, dict]:
    raw = load_issue_corpus()
    prepared = prepare_issue_frame(raw, with_effort=False)
    labelled = filter_labelled(prepared, ["priority"])
    meta = {
        "data_version": DATA_VERSION,
        **corpus_provenance(),
        "raw_issues": int(len(raw)),
        "labelled_issues": int(len(labelled)),
        "dropped_unlabelled": int(len(raw) - len(labelled)),
        "priority_distribution": labelled["priority"].value_counts().to_dict(),
        "repositories": int(labelled["repository"].nunique()),
        "leakage_control": (
            "Label-presence features (has_bug_label, has_feature_label, has_severity_label, "
            "…) are EXCLUDED from this model. The priority target is derived from the "
            "issue's labels, so any label-presence feature would let the model read the "
            "target directly."
        ),
    }
    return labelled, meta


def main() -> dict:
    print("=" * 78)
    print("MODEL 3 — ISSUE PRIORITY PREDICTION (4 classes)")
    print("=" * 78)

    df, meta = build_dataset()
    df = df[df["priority"].isin(PRIORITIES)].reset_index(drop=True)
    print(
        f"  [data ] {len(df):,} labelled real issues; "
        f"distribution = {df['priority'].value_counts().to_dict()}"
    )

    train_df, test_df = train_test_split(
        df, test_size=0.15, random_state=RANDOM_SEED, stratify=df["priority"]
    )
    train_df, test_df = train_df.reset_index(drop=True), test_df.reset_index(drop=True)

    X_train, y_train = train_df.drop(columns=["priority"]), train_df["priority"].to_numpy()
    X_test, y_test = test_df.drop(columns=["priority"]), test_df["priority"].to_numpy()
    print(f"  [split] stratified 85/15 -> train={len(train_df):,} test={len(test_df):,}")

    run = start_run(
        MODEL_NAME,
        params={
            "data_version": DATA_VERSION,
            "primary_metric": PRIMARY_METRIC,
            "cv_folds": CV_FOLDS,
            "seed": RANDOM_SEED,
            "n_train": len(train_df),
            "n_test": len(test_df),
            "features": "tfidf(title) + structured",
            **library_versions(),
        },
        tags={"task": "multiclass", "model": MODEL_NAME},
    )
    mlflow_run_id = run.info.run_id if run is not None else None

    from models.candidates import text_classifier_candidates

    result = train_and_select(
        name=MODEL_NAME,
        task="multiclass",
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        candidates={name: hybrid_pipeline(name) for name in text_classifier_candidates()},
        primary_metric=PRIMARY_METRIC,
        higher_is_better=True,
        classes=PRIORITIES,
        cv=CV_FOLDS,
        random_state=RANDOM_SEED,
    )
    print(
        f"\n  [select] winner = {result.selected} "
        f"(cv_{PRIMARY_METRIC}={result.cv_summary['cv_mean']:.4f})"
    )

    matrix = M.confusion(y_test, result.test_predictions, PRIORITIES)
    report = M.classification_report_dict(y_test, result.test_predictions, PRIORITIES)
    base = getattr(result.estimator, "estimator", result.estimator)
    # The hybrid model's coefficient vector spans the TF-IDF vocabulary *and* the
    # structured block, so report both views: top vocabulary terms and the structured
    # features (appended after the vectorisers' columns).
    importance = M.top_weighted_terms(result.estimator, top=20)
    offset = None
    try:
        word = result.estimator.word_vectoriser_
        char = result.estimator.char_vectoriser_
        offset = len(word.get_feature_names_out()) + len(char.get_feature_names_out())
    except Exception:
        offset = None
    coef = getattr(base, "coef_", None)
    if coef is not None and offset is not None:
        values = np.abs(np.asarray(coef, dtype=float))
        mean_values = values.mean(axis=0) if values.ndim > 1 else values
        block = mean_values[offset : offset + len(STRUCTURED_COLUMNS)]
        total = float(block.sum()) or 1.0
        pairs = sorted(zip(STRUCTURED_COLUMNS, block), key=lambda kv: -kv[1])[:10]
        importance += [
            {
                "feature": f"struct::{name}",
                "importance": M._f(value / total),
                "kind": "mean_abs_coefficient",
            }
            for name, value in pairs
        ]

    manifest = {
        "name": MODEL_NAME,
        "version": 1,
        "task": "multiclass",
        "target": "priority",
        "algorithm": result.selected,
        "data_version": DATA_VERSION,
        "data_hash": data_hash(
            pd.DataFrame({"t": df["_text"], "p": df["priority"]}), ["t", "p"]
        ),
        "dataset": {
            **meta,
            "n_train": len(train_df),
            "n_test": len(test_df),
            "split": "stratified 85/15",
            "cv": f"{CV_FOLDS}-fold stratified",
        },
        "classes": PRIORITIES,
        "features": ["tfidf_title_word_1_2", "tfidf_title_char_wb_3_5", *STRUCTURED_COLUMNS],
        "hyperparameters": {},
        "metrics": result.test_metrics,
        "cv": {k: v for k, v in result.cv_summary.items() if k != "cv_folds"},
        "cv_folds": result.cv_summary["cv_folds"],
        "comparison": result.rows,
        "confusion_matrix": matrix,
        "confusion_matrix_labels": PRIORITIES,
        "classification_report": report,
        "feature_importance": importance,
        "target_description": TARGET_DESCRIPTIONS[MODEL_NAME],
        "limitations": LIMITATIONS[MODEL_NAME],
        "advisory_disclaimer": (
            "This model produces an advisory estimate learned from historical patterns. "
            "It is not an authoritative priority decision; human triage decides priority."
        ),
        "mlflow_run_id": mlflow_run_id,
        "trained_at": utcnow_iso(),
        "library_versions": library_versions(),
        "seed": RANDOM_SEED,
        "n_cv_folds": CV_FOLDS,
    }
    out_dir = save_artifact(
        MODEL_NAME, 1, result.estimator, manifest, {"comparison.json": result.rows}
    )
    write_report(MODEL_NAME, 1, manifest)
    write_markdown(MODEL_NAME, 1, _markdown(manifest, result))

    log_metrics(run, result.test_metrics, prefix="test_")
    if run is not None:
        mlflow.log_artifact(str(out_dir / "model.joblib"))
        run.end()
    print(f"\n  [saved] {out_dir}")
    return manifest


def _markdown(m: dict, result) -> str:
    rows = "\n".join(
        f"| {r['model']} | {r.get('accuracy', '—')} | {r.get('precision', '—')} | "
        f"{r.get('recall', '—')} | {r.get('f1', '—')} | {r.get('f1_macro', '—')} | "
        f"{r.get('roc_auc', '—')} | {r.get('training_time_seconds', '—')} | "
        f"{'**selected**' if r.get('selected') else ''} |"
        for r in m["comparison"]
    )
    t = m["metrics"]
    per_class = "\n".join(
        f"| {k} | {v['precision']:.3f} | {v['recall']:.3f} | {v['f1']:.3f} | {v['support']} |"
        for k, v in t["per_class"].items()
    )
    return f"""# Issue Priority Model — Evaluation Report (v1)

**Algorithm:** `{m["algorithm"]}` · **Classes:** {", ".join(PRIORITIES)} ·
**Trained:** {m["trained_at"]}

> {m["advisory_disclaimer"]}

## Target
{m["target_description"]}

## Dataset
- Real issues loaded: **{m["dataset"]["raw_issues"]:,}**; labelled: **{m["dataset"]["labelled_issues"]:,}**
  (dropped {m["dataset"]["dropped_unlabelled"]:,} with no derivable priority — never guessed)
- Distribution: {m["dataset"]["priority_distribution"]}
- Features: TF-IDF over the issue title (word 1–2-gram + char_wb 3–5-gram) concatenated with
  {len(STRUCTURED_COLUMNS)} scaled structured features (label severity, comment volume,
  stack-trace detection, critical-language detection, length, author association).

## Model comparison (selection: {result.selection_criterion})

| Model | Accuracy | Precision | Recall | F1 | F1-macro | ROC-AUC | Train time (s) | |
|---|---|---|---|---|---|---|---|---|
{rows}

## Held-out test metrics — selected model

| Metric | Value |
|---|---|
| Accuracy | {t["accuracy"]:.4f} |
| F1 (macro) | {t["f1_macro"]:.4f} |
| ROC-AUC (OvR macro) | {t.get("roc_auc_ovr", "n/a")} |

## Per-class performance

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
{per_class}

## Limitations
{m["limitations"]}
"""


if __name__ == "__main__":
    main()
