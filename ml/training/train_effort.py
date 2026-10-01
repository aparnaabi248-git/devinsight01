"""PHASE 11 — train the development-effort regressor (hours to resolve an issue)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mlflow  # noqa: E402
import numpy as np  # noqa: E402
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
from features.issue_features import (  # noqa: E402
    STRUCTURED_COLUMNS,
    prepare_issue_frame,
)
from models.hybrid import HybridTextRegressor  # noqa: E402
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

MODEL_NAME = "issue_effort"
PRIMARY_METRIC = "mae"
MAX_HOURS = 24 * 365  # clip absurd tails (left open for years) at one year


def effort_pipeline(estimator) -> HybridTextRegressor:
    from models.hybrid import HybridTextRegressor

    return HybridTextRegressor(
        estimator=estimator,
        text_column="_text",
        title_column="_title_text",
        columns=list(STRUCTURED_COLUMNS),
        clip_hours=MAX_HOURS,
    )


def build_dataset() -> tuple[pd.DataFrame, dict]:
    """Every closed issue with a real close timestamp is usable.

    Deliberately *not* filtered on the priority label: the target here is time-to-close,
    which is independent of the labels, so requiring a severity label would throw away
    most of the corpus for no reason.
    """
    raw = load_issue_corpus()
    prepared = prepare_issue_frame(raw, with_effort=True)
    with_text = prepared[prepared["_text"].str.split().str.len() >= 5]
    labelled = with_text[with_text["resolution_hours"].notna()]
    closed_before = labelled
    labelled = labelled[
        (labelled["resolution_hours"] > 0.02) & (labelled["resolution_hours"] <= MAX_HOURS)
    ].reset_index(drop=True)
    meta = {
        "data_version": DATA_VERSION,
        **corpus_provenance(),
        "raw_issues": int(len(raw)),
        "closed_issues_used": int(len(labelled)),
        "dropped_no_close_timestamp": int(len(with_text) - len(closed_before)),
        "dropped_implausible_duration": int(len(closed_before) - len(labelled)),
        "target_hours": {
            "mean": round(float(labelled["resolution_hours"].mean()), 3),
            "median": round(float(labelled["resolution_hours"].median()), 3),
            "p90": round(float(labelled["resolution_hours"].quantile(0.9)), 3),
            "max": round(float(labelled["resolution_hours"].max()), 3),
        },
        "repositories": int(labelled["repository"].nunique()),
    }
    return labelled, meta


def main() -> dict:
    print("=" * 78)
    print("MODEL 4 — DEVELOPMENT EFFORT PREDICTION (regression, hours)")
    print("=" * 78)

    df, meta = build_dataset()
    print(f"  [data ] {len(df):,} closed real issues with a real close timestamp")
    print(
        f"  [data ] target hours: mean={meta['target_hours']['mean']} "
        f"median={meta['target_hours']['median']} p90={meta['target_hours']['p90']}"
    )

    train_df, test_df = train_test_split(df, test_size=0.15, random_state=RANDOM_SEED)
    train_df, test_df = train_df.reset_index(drop=True), test_df.reset_index(drop=True)

    X_train = train_df.drop(columns=["resolution_hours"])
    y_train = train_df["resolution_hours"].to_numpy(dtype=float)
    X_test = test_df.drop(columns=["resolution_hours"])
    y_test = test_df["resolution_hours"].to_numpy(dtype=float)
    print(f"  [split] 85/15 -> train={len(train_df):,} test={len(test_df):,}")

    run = start_run(
        MODEL_NAME,
        params={
            "data_version": DATA_VERSION,
            "primary_metric": PRIMARY_METRIC,
            "cv_folds": CV_FOLDS,
            "seed": RANDOM_SEED,
            "n_train": len(train_df),
            "n_test": len(test_df),
            "target": "log1p(resolution_hours)",
            **library_versions(),
        },
        tags={"task": "regression", "model": MODEL_NAME},
    )
    mlflow_run_id = run.info.run_id if run is not None else None

    from models.candidates import regression_candidates

    result = train_and_select(
        name=MODEL_NAME,
        task="regression",
        X_train=X_train,
        y_train=y_train,
        X_test=X_test,
        y_test=y_test,
        candidates={
            name: effort_pipeline(base) for name, base in regression_candidates().items()
        },
        primary_metric=PRIMARY_METRIC,
        higher_is_better=False,
        cv=CV_FOLDS,
        random_state=RANDOM_SEED,
    )
    print(
        f"\n  [select] winner = {result.selected} "
        f"(cv_{PRIMARY_METRIC}={result.cv_summary['cv_mean']:.4f} — lower is better)"
    )

    # Quantile-style interval from the residual spread on the test set.
    residuals = np.abs(np.asarray(y_test, float) - np.asarray(result.test_predictions, float))
    iqr = float(np.percentile(residuals, 75) - np.percentile(residuals, 25))
    residual_sigma = float(np.std(residuals))

    manifest = {
        "name": MODEL_NAME,
        "version": 1,
        "task": "regression",
        "target": "resolution_hours",
        "algorithm": result.selected,
        "data_version": DATA_VERSION,
        "data_hash": data_hash(
            pd.DataFrame({"t": df["_text"], "h": df["resolution_hours"]}), ["t", "h"]
        ),
        "dataset": {
            **meta,
            "n_train": len(train_df),
            "n_test": len(test_df),
            "split": "random 85/15",
            "cv": f"{CV_FOLDS}-fold KFold",
        },
        "target_transform": "log1p, inverted at prediction time",
        "features": ["tfidf_word_1_2", "tfidf_char_wb_3_5", *STRUCTURED_COLUMNS],
        "hyperparameters": {},
        "metrics": result.test_metrics,
        "cv": {k: v for k, v in result.cv_summary.items() if k != "cv_folds"},
        "cv_folds": result.cv_summary["cv_folds"],
        "comparison": result.rows,
        "interval": {
            "method": "residual IQR on the test set",
            "iqr_hours": round(iqr, 3),
            "sigma_hours": round(residual_sigma, 3),
        },
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
        run.end()
    print(f"\n  [saved] {out_dir}")
    return manifest


def _markdown(m: dict, result) -> str:
    rows = "\n".join(
        f"| {r['model']} | {r.get('mae', '—')} | {r.get('rmse', '—')} | {r.get('r2', '—')} | "
        f"{r.get('cv_mae', '—')} | {r.get('training_time_seconds', '—')} | "
        f"{'**selected**' if r.get('selected') else ''} |"
        for r in m["comparison"]
    )
    t = m["metrics"]
    return f"""# Development Effort Model — Evaluation Report (v1)

**Algorithm:** `{m["algorithm"]}` · **Target:** `{m["target"]}` (hours) ·
**Trained:** {m["trained_at"]}

> {m["limitations"]}

## Target definition
{m["target_description"]}

## Dataset
- Real issues loaded: **{m["dataset"]["raw_issues"]:,}**
- Closed issues with a real `closed_at` used: **{m["dataset"]["closed_issues_used"]:,}**
- Dropped (open / no close timestamp / non-positive / > 1 year): **{m["dataset"]["dropped_no_close_timestamp"]:,}**
- Target distribution (hours): {m["dataset"]["target_hours"]}
- Transform: {m["target_transform"]} — all metrics below are on the original hour scale.

## Model comparison (selection: {result.selection_criterion} — lower is better)

| Model | MAE (h) | RMSE (h) | R² | CV MAE | Train time (s) | |
|---|---|---|---|---|---|---|
{rows}

## Held-out test metrics — selected model

| Metric | Value |
|---|---|
| MAE | {t["mae"]:.3f} h |
| RMSE | {t["rmse"]:.3f} h |
| R² | {t["r2"]:.4f} |
| Median absolute error | {t["median_ae"]:.3f} h |
| MAPE | {t["mape_pct"]:.2f}% |
| Within ±20% of actual | {t["within_20pct"]:.2f}% |
| Within 2× of actual | {t["within_2x"]:.2f}% |

Prediction interval: ±{m["interval"]["iqr_hours"]} h (IQR of absolute test residuals).

## Limitations
{m["limitations"]}
"""


if __name__ == "__main__":
    main()
