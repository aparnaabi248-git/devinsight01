"""PHASE 9 — train and evaluate the issue-category classifier on the real issue corpus."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
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
    CATEGORIES,
    filter_labelled,
    prepare_issue_frame,
)
from models.candidates import text_classifier_candidates  # noqa: E402
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

MODEL_NAME = "issue_classifier"
PRIMARY_METRIC = "f1_macro"


def build_dataset() -> tuple[pd.DataFrame, dict]:
    raw = load_issue_corpus()
    prepared = prepare_issue_frame(raw, with_effort=False)
    labelled = filter_labelled(prepared, ["category"])
    meta = {
        "data_version": DATA_VERSION,
        **corpus_provenance(),
        "raw_issues": int(len(raw)),
        "labelled_issues": int(len(labelled)),
        "dropped_unlabelled": int(len(raw) - len(labelled)),
        "category_distribution": labelled["category"].value_counts().to_dict(),
        "repositories": int(labelled["repository"].nunique()),
    }
    return labelled, meta


def main() -> dict:
    print("=" * 78)
    print("MODEL 2 — ISSUE CLASSIFICATION (6 categories)")
    print("=" * 78)

    df, meta = build_dataset()
    df = df[df["category"].isin(CATEGORIES)]
    print(
        f"  [data ] {len(df):,} labelled real issues; "
        f"distribution = {df['category'].value_counts().to_dict()}"
    )

    X = df["_text"].tolist()
    y = df["category"].to_numpy()
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, random_state=RANDOM_SEED, stratify=y
    )
    print(f"  [split] stratified 85/15 -> train={len(X_train):,} test={len(X_test):,}")

    run = start_run(
        MODEL_NAME,
        params={
            "data_version": DATA_VERSION,
            "primary_metric": PRIMARY_METRIC,
            "cv_folds": CV_FOLDS,
            "seed": RANDOM_SEED,
            "n_train": len(X_train),
            "n_test": len(X_test),
            "vectoriser": "word(1,2) + char_wb(3,5) TF-IDF",
            **library_versions(),
        },
        tags={"task": "multiclass", "model": MODEL_NAME},
    )
    mlflow_run_id = run.info.run_id if run is not None else None

    result = train_and_select(
        name=MODEL_NAME,
        task="multiclass",
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        candidates=text_classifier_candidates(),
        primary_metric=PRIMARY_METRIC,
        higher_is_better=True,
        classes=CATEGORIES,
        cv=CV_FOLDS,
        random_state=RANDOM_SEED,
    )
    print(
        f"\n  [select] winner = {result.selected} "
        f"(cv_{PRIMARY_METRIC}={result.cv_summary['cv_mean']:.4f})"
    )

    matrix = M.confusion(y_test, result.test_predictions, CATEGORIES)
    report = M.classification_report_dict(y_test, result.test_predictions, CATEGORIES)

    # Top discriminative terms, read from the fitted vectorisers.
    top_features: list[str] = []
    if hasattr(result.estimator, "named_steps"):
        try:
            names = result.estimator.named_steps["word"].get_feature_names_out()
            coef = abs(result.estimator.named_steps["clf"].coef_)
            mean_coef = coef.mean(axis=0) if coef.ndim > 1 else coef
            top_features = [
                str(n)
                for n in names[sorted(range(len(names)), key=lambda i: -mean_coef[i])[:30]]
            ]
        except (AttributeError, ValueError, KeyError, IndexError):
            top_features = []

    manifest = {
        "name": MODEL_NAME,
        "version": 1,
        "task": "multiclass",
        "target": "category",
        "algorithm": result.selected,
        "data_version": DATA_VERSION,
        "data_hash": data_hash(pd.DataFrame({"text": df["_text"], "y": y}), ["text", "y"]),
        "dataset": {
            **meta,
            "n_train": len(X_train),
            "n_test": len(X_test),
            "split": "stratified 85/15",
            "cv": f"{CV_FOLDS}-fold stratified",
        },
        "classes": CATEGORIES,
        "features": ["tfidf_word_1_2gram", "tfidf_char_wb_3_5gram", "cleaned_issue_text"],
        "hyperparameters": _params(result.estimator),
        "metrics": result.test_metrics,
        "cv": {k: v for k, v in result.cv_summary.items() if k != "cv_folds"},
        "cv_folds": result.cv_summary["cv_folds"],
        "comparison": result.rows,
        "confusion_matrix": matrix,
        "confusion_matrix_labels": CATEGORIES,
        "classification_report": report,
        "feature_importance": [
            {"feature": f, "importance": None, "kind": "mean_abs_coefficient"}
            for f in top_features[:20]
        ],
        "target_description": TARGET_DESCRIPTIONS[MODEL_NAME],
        "limitations": LIMITATIONS[MODEL_NAME],
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
        mlflow.log_dict(result.rows, "comparison.json")
        run.end()
    print(f"\n  [saved] {out_dir}")
    return manifest


def _params(estimator) -> dict:
    try:
        return {
            k: v
            for k, v in estimator.get_params().items()
            if isinstance(v, (int, float, str, bool, type(None)))
        }
    except Exception:
        return {}


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
    header = "| | " + " | ".join(m["confusion_matrix_labels"]) + " |\n"
    sep = "|---" * (len(m["confusion_matrix_labels"]) + 1) + "|\n"
    cm = (
        header
        + sep
        + "".join(
            f"| **{label}** | " + " | ".join(str(v) for v in row) + " |\n"
            for label, row in zip(m["confusion_matrix_labels"], m["confusion_matrix"])
        )
    )
    return f"""# Issue Classifier — Evaluation Report (v1)

**Algorithm:** `{m["algorithm"]}` · **Classes:** {len(CATEGORIES)} ·
**Trained:** {m["trained_at"]} · **Data hash:** `{m["data_hash"]}`

## Target
{m["target_description"]}

## Dataset
- Real issues loaded: **{m["dataset"]["raw_issues"]:,}**
- Labelled issues used: **{m["dataset"]["labelled_issues"]:,}**
  (dropped {m["dataset"]["dropped_unlabelled"]:,} with no derivable category — never guessed)
- Class distribution: {m["dataset"]["category_distribution"]}
- Split: {m["dataset"]["split"]}; CV: {m["dataset"]["cv"]}
- Representation: word 1–2-gram + char_wb 3–5-gram TF-IDF over cleaned issue text

## Model comparison (selection: {result.selection_criterion})

| Model | Accuracy | Precision | Recall | F1 | F1-macro | ROC-AUC | Train time (s) | |
|---|---|---|---|---|---|---|---|---|
{rows}

## Held-out test metrics — selected model

| Metric | Value |
|---|---|
| Accuracy | {t["accuracy"]:.4f} |
| Precision (weighted) | {t["precision"]:.4f} |
| Recall (weighted) | {t["recall"]:.4f} |
| F1 (macro) | {t["f1_macro"]:.4f} |
| ROC-AUC (OvR macro) | {t.get("roc_auc_ovr", "n/a")} |

## Per-class performance

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
{per_class}

## Confusion matrix (rows = actual, cols = predicted)

{cm}
## Limitations
{m["limitations"]}
"""


if __name__ == "__main__":
    main()
