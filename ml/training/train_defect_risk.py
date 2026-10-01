"""PHASE 8 — train and evaluate the defect-risk model on real git history."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ml/

import mlflow  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from config import (  # noqa: E402
    CV_FOLDS,
    DATA_VERSION,
    LIMITATIONS,
    RANDOM_SEED,
    TARGET_DESCRIPTIONS,
)
from evaluation import metrics as M  # noqa: E402
from features.defect_features import FEATURE_COLUMNS, TARGET_COLUMN  # noqa: E402
from models.candidates import tabular_classifier_candidates  # noqa: E402
from preprocessing.build_datasets import (  # noqa: E402
    load_or_extract_defect_dataset,
    make_defect_splits,
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

MODEL_NAME = "defect_risk"
PRIMARY_METRIC = "f1_macro"
FEATURES = FEATURE_COLUMNS


def _impute() -> SimpleImputer:
    return SimpleImputer(strategy="median")


def candidates() -> dict[str, object]:
    raw = tabular_classifier_candidates()
    out: dict[str, object] = {}
    for name, est in raw.items():
        if name == "logistic_regression":
            out[name] = Pipeline(
                [
                    ("impute", _impute()),
                    ("scale", StandardScaler()),
                    (
                        "clf",
                        LogisticRegression(
                            max_iter=3000, C=1.0, class_weight="balanced", solver="lbfgs"
                        ),
                    ),
                ]
            )
        elif name == "random_forest" or name == "gradient_boosting":
            out[name] = Pipeline([("impute", _impute()), ("clf", est)])
        else:
            out[name] = Pipeline([("impute", _impute()), ("clf", est)])
    return out


def risk_level(probability: float) -> str:
    """Band a calibrated P(defect) into the API's LOW / MEDIUM / HIGH contract."""
    from config import RISK_BANDS

    for level, threshold in RISK_BANDS:
        if probability < threshold:
            return level
    return RISK_BANDS[-1][0]


def main(rebuild: bool = False) -> dict:
    print("=" * 78)
    print("MODEL 1 — SOFTWARE DEFECT RISK PREDICTION")
    print("=" * 78)

    features, meta = load_or_extract_defect_dataset(rebuild=rebuild)
    splits = make_defect_splits(features)
    df = features.sort_values("authored_at").reset_index(drop=True)
    cut = int(len(df) * 0.85)
    train_df, test_df = df.iloc[:cut], df.iloc[cut:]

    X_train, y_train = (
        train_df[FEATURES].to_numpy(dtype=float),
        train_df[TARGET_COLUMN].to_numpy(),
    )
    X_test, y_test = test_df[FEATURES].to_numpy(dtype=float), test_df[TARGET_COLUMN].to_numpy()
    print(
        f"  [split] chronological: train={len(train_df):,} (up to "
        f"{str(train_df['authored_at'].max())[:10]}), test={len(test_df):,} "
        f"(from {str(test_df['authored_at'].min())[:10]})"
    )
    print(
        f"  [split] train positives={int(y_train.sum()):,} "
        f"({y_train.mean() * 100:.2f}%), test positives={int(y_test.sum()):,} "
        f"({y_test.mean() * 100:.2f}%)"
    )

    run = start_run(
        MODEL_NAME,
        params={
            "data_version": DATA_VERSION,
            "primary_metric": PRIMARY_METRIC,
            "cv_folds": CV_FOLDS,
            "seed": RANDOM_SEED,
            "n_features": len(FEATURES),
            "n_train": len(train_df),
            "n_test": len(test_df),
            **library_versions(),
        },
        tags={"task": "binary", "model": MODEL_NAME},
    )
    mlflow_run_id = run.info.run_id if run is not None else None

    result = train_and_select(
        name=MODEL_NAME,
        task="binary",
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        candidates=candidates(),
        primary_metric=PRIMARY_METRIC,
        higher_is_better=True,
        classes=[0, 1],
        cv=CV_FOLDS,
        random_state=RANDOM_SEED,
        params_for_log={"model_sweep": ",".join(candidates().keys())},
    )
    print(
        f"\n  [select] winner = {result.selected} "
        f"(cv_{PRIMARY_METRIC}={result.cv_summary['cv_mean']:.4f})"
    )
    print(
        f"  [test ] accuracy={result.test_metrics['accuracy']:.4f} "
        f"f1={result.test_metrics['f1']:.4f} f1_macro={result.test_metrics['f1_macro']:.4f} "
        f"roc_auc={result.test_metrics.get('roc_auc')}"
    )

    matrix = M.confusion(y_test, result.test_predictions, [0, 1])
    importance = M.feature_importance(result.estimator, FEATURES)
    if not importance:  # pipelines hide the final estimator
        final = (
            result.estimator.named_steps.get("clf", result.estimator)
            if hasattr(result.estimator, "named_steps")
            else result.estimator
        )
        importance = M.feature_importance(final, FEATURES)
    report_df = M.classification_report_dict(y_test, result.test_predictions, [0, 1])

    manifest = {
        "name": MODEL_NAME,
        "version": 1,
        "task": "binary",
        "target": TARGET_COLUMN,
        "algorithm": result.selected,
        "data_version": DATA_VERSION,
        "data_hash": data_hash(features, FEATURES + [TARGET_COLUMN]),
        "dataset": {
            **meta,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
            "train_positive_rate": round(float(y_train.mean()), 6),
            "test_positive_rate": round(float(y_test.mean()), 6),
            "train_end": splits["train_cut_date"],
            "test_start": splits["test_start_date"],
        },
        "features": FEATURES,
        "n_features": len(FEATURES),
        "hyperparameters": _params(result.estimator),
        "metrics": result.test_metrics,
        "cv": {k: v for k, v in result.cv_summary.items() if k != "cv_folds"},
        "cv_folds": result.cv_summary["cv_folds"],
        "comparison": result.rows,
        "confusion_matrix": matrix,
        "confusion_matrix_labels": ["no_defect", "defect"],
        "classification_report": report_df,
        "feature_importance": importance,
        "risk_bands": [
            {"level": "LOW", "lt": 0.33},
            {"level": "MEDIUM", "lt": 0.66},
            {"level": "HIGH", "gte": 0.66},
        ],
        "target_description": TARGET_DESCRIPTIONS[MODEL_NAME],
        "limitations": LIMITATIONS[MODEL_NAME],
        "mlflow_run_id": mlflow_run_id,
        "trained_at": utcnow_iso(),
        "library_versions": library_versions(),
        "seed": RANDOM_SEED,
        "n_cv_folds": CV_FOLDS,
        "selection_criterion": result.selection_criterion,
    }
    extra = {
        "comparison.json": result.rows,
        "report.json": {
            k: manifest[k]
            for k in (
                "metrics",
                "confusion_matrix",
                "classification_report",
                "feature_importance",
                "cv_folds",
                "dataset",
            )
        },
    }
    out_dir = save_artifact(MODEL_NAME, 1, result.estimator, manifest, extra)
    write_report(MODEL_NAME, 1, manifest)
    write_markdown(MODEL_NAME, 1, _markdown(manifest, result))

    log_metrics(run, result.test_metrics, prefix="test_")
    log_metrics(
        run, {k: v for k, v in result.cv_summary.items() if k != "cv_folds"}, prefix="cv_"
    )
    if run is not None:
        mlflow.log_artifact(str(out_dir / "model.joblib"))
        mlflow.log_dict(result.rows, "comparison.json")
        run.end()

    print(f"\n  [saved] {out_dir}")
    print(f"  [saved] reports/{MODEL_NAME}_v1.md")
    return manifest


def _params(estimator) -> dict:
    final = estimator
    if hasattr(estimator, "named_steps"):
        final = estimator.named_steps.get("clf", estimator)
    params = getattr(final, "get_params", lambda: {})()
    return {
        k: v for k, v in params.items() if isinstance(v, (int, float, str, bool, type(None)))
    }


def _markdown(m: dict, result) -> str:
    rows = "\n".join(
        f"| {r['model']} | {r.get('accuracy', '—')} | {r.get('precision', '—')} | "
        f"{r.get('recall', '—')} | {r.get('f1', '—')} | {r.get('roc_auc', '—')} | "
        f"{r.get('cv_f1_macro', '—')} | {r.get('training_time_seconds', '—')} | "
        f"{'**selected**' if r.get('selected') else ''} |"
        for r in m["comparison"]
    )
    top = "\n".join(
        f"| {f['feature']} | {f['importance']:.4f} |" for f in m["feature_importance"][:15]
    )
    t = m["metrics"]
    return f"""# Defect Risk Model — Evaluation Report (v1)

**Algorithm selected:** `{m["algorithm"]}` · **Target:** `{m["target"]}` ·
**Trained:** {m["trained_at"]} · **Data:** {m["data_version"]} (`{m["data_hash"]}`)

## Target definition
{m["target_description"]}

## Dataset
- Rows: **{m["dataset"]["rows"]:,}** real commits from {len(m["dataset"]["repositories"])} repositories
- Train: {m["dataset"]["n_train"]:,} (positive rate {m["dataset"]["train_positive_rate"] * 100:.2f}%)
- Test: {m["dataset"]["n_test"]:,} (positive rate {m["dataset"]["test_positive_rate"] * 100:.2f}%) — chronological holdout
- Features: **{m["n_features"]}**
- Repositories: {", ".join(m["dataset"]["repositories"])}

## Model comparison (selection: {m.get("selection_criterion") or result.selection_criterion})

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | CV F1-macro | Train time (s) | |
|---|---|---|---|---|---|---|---|---|
{rows}

## Selected model — held-out test metrics

| Metric | Value |
|---|---|
| Accuracy | {t["accuracy"]:.4f} |
| Precision (positive class) | {t["precision"]:.4f} |
| Recall (positive class) | {t["recall"]:.4f} |
| F1 (positive class) | {t["f1"]:.4f} |
| F1 (macro) | {t["f1_macro"]:.4f} |
| Balanced accuracy | {t["balanced_accuracy"]:.4f} |
| ROC-AUC | {t.get("roc_auc", "n/a")} |

## Confusion matrix (rows = actual, cols = predicted)

| | no defect | defect |
|---|---|---|
| **no defect** | {m["confusion_matrix"][0][0]} | {m["confusion_matrix"][0][1]} |
| **defect** | {m["confusion_matrix"][1][0]} | {m["confusion_matrix"][1][1]} |

## Top features

| Feature | Importance |
|---|---|
{top}

## Limitations
{m["limitations"]}
"""


if __name__ == "__main__":
    main(rebuild="--rebuild" in sys.argv)
