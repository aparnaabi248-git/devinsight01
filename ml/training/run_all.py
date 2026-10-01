"""Train every DevInsight model end to end and mirror the winners into the registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ARTIFACT_DIR, REPORT_DIR  # noqa: E402

TRAINERS = {
    "defect_risk": ("training.train_defect_risk", "main"),
    "issue_classifier": ("training.train_issue_classifier", "main"),
    "issue_priority": ("training.train_priority", "main"),
    "issue_effort": ("training.train_effort", "main"),
}


def run_all(only: list[str] | None = None, rebuild: bool = False) -> dict[str, dict]:
    import importlib

    results: dict[str, dict] = {}
    targets = only or list(TRAINERS)
    for name in targets:
        if name not in TRAINERS:
            print(f"  [skip] unknown model '{name}'")
            continue
        module_name, func_name = TRAINERS[name]
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        print()
        results[name] = func(**({"rebuild": rebuild} if name == "defect_risk" else {}))

    summary = {
        name: {
            "algorithm": m["algorithm"],
            "version": m["version"],
            "primary_metrics": {
                k: v
                for k, v in m["metrics"].items()
                if k
                in {
                    "accuracy",
                    "f1",
                    "f1_macro",
                    "precision",
                    "recall",
                    "roc_auc",
                    "mae",
                    "rmse",
                    "r2",
                }
            },
            "dataset_rows": m["dataset"].get("rows")
            or m["dataset"].get("labelled_issues")
            or m["dataset"].get("closed_issues_used"),
            "trained_at": m["trained_at"],
        }
        for name, m in results.items()
    }
    (REPORT_DIR / "training_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    print("\n" + "=" * 78)
    print("TRAINING SUMMARY")
    print("=" * 78)
    for name, s in summary.items():
        metrics = ", ".join(f"{k}={v}" for k, v in s["primary_metrics"].items())
        print(f"  {name:<18} {s['algorithm']:<22} rows={s['dataset_rows']:<7} {metrics}")
    print(f"\n  artefacts: {ARTIFACT_DIR}")
    print(f"  reports:   {REPORT_DIR}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Train all DevInsight models")
    ap.add_argument("--only", nargs="*", choices=list(TRAINERS), help="train a subset")
    ap.add_argument("--rebuild", action="store_true", help="re-extract raw git history")
    args = ap.parse_args()
    run_all(only=args.only, rebuild=args.rebuild)
