"""Cross-cutting checks on the trained artefacts.

Run after `python ml/training/run_all.py`. This is the MLOps gate: it refuses to pass if
a model is missing, if metrics are absent, or if a metric looks like a placeholder rather
than a measured value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO_ROOT / "ml" / "artifacts"

EXPECTED = {
    "defect_risk": ("binary", {"accuracy", "f1", "f1_macro", "precision", "recall"}),
    "issue_classifier": ("multiclass", {"accuracy", "f1_macro", "per_class"}),
    "issue_priority": ("multiclass", {"accuracy", "f1_macro", "per_class"}),
    "issue_effort": ("regression", {"mae", "rmse", "r2"}),
}

# A metric outside this band means the number is a placeholder, not a measurement.
PLAUSIBLE = {
    "accuracy": (0.2, 1.0),
    "f1": (0.1, 1.0),
    "f1_macro": (0.05, 1.0),
    "precision": (0.1, 1.0),
    "recall": (0.1, 1.0),
    "roc_auc": (0.4, 1.0),
    "r2": (-1.0, 1.0),
    "mae": (0.01, 10000.0),
    "rmse": (0.01, 10000.0),
}


def main() -> int:
    problems: list[str] = []
    if not ARTIFACTS.exists():
        print(
            "::error::No artefacts directory. Run `python ml/training/run_all.py` first."
        )
        return 1

    for name, (task, required_metrics) in EXPECTED.items():
        model_dir = ARTIFACTS / name
        if not model_dir.exists():
            problems.append(f"{name}: not trained")
            continue

        versions = sorted(
            (p for p in model_dir.glob("v*") if p.is_dir()),
            key=lambda p: int(p.name.lstrip("v") or 0),
        )
        if not versions:
            problems.append(f"{name}: no versioned artefact")
            continue

        latest = versions[-1]
        if not (latest / "model.joblib").exists():
            problems.append(f"{name}: {latest.name} has no model.joblib")
        if not (latest / "manifest.json").exists():
            problems.append(f"{name}: {latest.name} has no manifest.json")
            continue

        manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))

        if manifest.get("task") != task:
            problems.append(
                f"{name}: task is {manifest.get('task')!r}, expected {task!r}"
            )
        for field in (
            "algorithm",
            "data_version",
            "data_hash",
            "trained_at",
            "target_description",
            "limitations",
        ):
            if not manifest.get(field):
                problems.append(f"{name}: manifest is missing '{field}'")
        if not manifest.get("library_versions"):
            problems.append(f"{name}: library versions not recorded — not reproducible")

        metrics = manifest.get("metrics") or {}
        missing = required_metrics - set(metrics)
        if missing:
            problems.append(f"{name}: metrics missing {sorted(missing)}")
        for key, (low, high) in PLAUSIBLE.items():
            value = metrics.get(key)
            if isinstance(value, (int, float)) and not (low <= value <= high):
                problems.append(
                    f"{name}: metric '{key}' = {value} is outside the plausible "
                    f"range [{low}, {high}] — is it a placeholder?"
                )

        comparison = manifest.get("comparison") or []
        if len(comparison) < 2:
            problems.append(
                f"{name}: only {len(comparison)} candidate model(s) compared — "
                "a model must be chosen by evaluation, not assumption"
            )
        if not any(row.get("selected") for row in comparison):
            problems.append(f"{name}: no candidate marked as selected")
        for row in comparison:
            if row.get("training_time_seconds") is None:
                problems.append(
                    f"{name}: candidate '{row.get('model')}' has no training time"
                )

        print(
            f"  [ok] {name:<18} v{manifest.get('version')} {manifest.get('algorithm'):<24} "
            f"{len(comparison)} candidates compared"
        )

    if problems:
        print("\nARTEFACT VALIDATION FAILED")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"\nAll {len(EXPECTED)} models validated: trained, evaluated, and documented."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
