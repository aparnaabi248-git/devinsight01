"""Summarise every trained model from its manifest."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

from config import ARTIFACT_DIR, REPORT_DIR

ORDER = ["defect_risk", "issue_classifier", "issue_priority", "issue_effort"]
TASK = {
    "defect_risk": "binary",
    "issue_classifier": "multiclass (6)",
    "issue_priority": "multiclass (4)",
    "issue_effort": "regression",
}
HEADLINE = {
    "defect_risk": ("accuracy", "precision", "recall", "f1", "f1_macro", "roc_auc"),
    "issue_classifier": ("accuracy", "f1_macro", "roc_auc_ovr"),
    "issue_priority": ("accuracy", "f1_macro", "roc_auc_ovr"),
    "issue_effort": ("mae", "rmse", "r2"),
}


def fmt(value, digits=4):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main() -> int:
    print("=" * 84)
    print("TRAINED MODELS — all metrics measured on a held-out test split")
    print("=" * 84)

    for name in ORDER:
        model_dir = ARTIFACT_DIR / name
        manifests = (
            sorted(model_dir.glob("v*/manifest.json")) if model_dir.exists() else []
        )
        if not manifests:
            print(f"\n{name}: NOT TRAINED")
            continue
        m = json.loads(manifests[-1].read_text(encoding="utf-8"))
        metrics = m.get("metrics", {})
        dataset = m.get("dataset", {})

        print(f"\n{name}  (v{m.get('version')})")
        print(f"  task        {TASK.get(m.get('task'), m.get('task'))}")
        print(f"  algorithm   {m.get('algorithm')}")
        print(f"  data        {m.get('data_version')}  hash={m.get('data_hash')}")
        print(f"  trained     {m.get('trained_at')}")
        rows = dataset.get("n_train"), dataset.get("n_test")
        print(f"  rows        train={rows[0]}  test={rows[1]}")
        print(
            "  metrics     "
            + "  ".join(f"{k}={fmt(metrics.get(k))}" for k in HEADLINE.get(name, ()))
        )
        if "positive_rate" in dataset:
            print(f"  positives   {dataset['positive_rate']}")

        print("  comparison:")
        print(
            f"    {'candidate':<24}{'acc':>9}{'prec':>9}{'rec':>9}{'f1':>9}"
            f"{'cv':>9}{'sec':>8}  "
        )
        for row in m.get("comparison", []):
            cv = row.get("cv_f1_macro", row.get("cv_mae"))
            mark = " <- selected" if row.get("selected") else ""
            print(
                f"    {row.get('model')!s:<24}"
                f"{fmt(row.get('accuracy')):>9}{fmt(row.get('precision')):>9}"
                f"{fmt(row.get('recall')):>9}{fmt(row.get('f1')):>9}"
                f"{fmt(cv):>9}{fmt(row.get('training_time_seconds'), 2):>8}  {mark}"
            )
        top = m.get("feature_importance", [])[:5]
        if top and top[0].get("importance") is not None:
            print(
                "  top features: "
                + ", ".join(f"{f['feature']}={f['importance']:.3f}" for f in top)
            )

    reports = sorted(REPORT_DIR.glob("*.md"))
    if reports:
        print(f"\nreports: {', '.join(p.name for p in reports)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
