"""Model registry: discovers trained artefacts on disk and tracks the active version."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from config import ARTIFACT_DIR, RISK_BANDS


@dataclass
class LoadedModel:
    name: str
    version: int
    estimator: Any
    manifest: dict[str, Any]
    directory: Path

    @property
    def tag(self) -> str:
        return f"{self.name}-v{self.version}"

    @property
    def metrics(self) -> dict[str, Any]:
        return self.manifest.get("metrics", {})

    @property
    def classes(self) -> list[str]:
        return self.manifest.get("classes", []) or list(
            self.manifest.get("confusion_matrix_labels", [])
        )

    def risk_level(self, probability: float) -> str:
        for level, threshold in RISK_BANDS:
            if probability < threshold:
                return level
        return RISK_BANDS[-1][0]


class ModelRegistry:
    """Loads every `<name>/v<version>/model.joblib` found under `ml/artifacts`.

    The highest version is the active one. Missing models degrade gracefully: the API
    reports them as unavailable instead of crashing the whole application.
    """

    def __init__(self, root: Path | str = ARTIFACT_DIR):
        self.root = Path(root)
        self._models: dict[str, LoadedModel] = {}
        self._load_errors: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> dict[str, LoadedModel]:
        self._models.clear()
        self._load_errors.clear()
        if not self.root.exists():
            return self._models

        for name_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            name = name_dir.name
            versions = []
            for version_dir in name_dir.glob("v*"):
                if not version_dir.is_dir():
                    continue
                try:
                    versions.append(int(version_dir.name.lstrip("v")))
                except ValueError:
                    continue
            if not versions:
                continue
            latest = max(versions)
            version_dir = name_dir / f"v{latest}"
            try:
                estimator = joblib.load(version_dir / "model.joblib")
                manifest = json.loads((version_dir / "manifest.json").read_text("utf-8"))
                self._models[name] = LoadedModel(
                    name=name,
                    version=latest,
                    estimator=estimator,
                    manifest=manifest,
                    directory=version_dir,
                )
            except Exception as exc:  # a corrupt artefact must not break the API
                self._load_errors[name] = f"{type(exc).__name__}: {exc}"
        return self._models

    def get(self, name: str) -> LoadedModel | None:
        return self._models.get(name)

    def require(self, name: str) -> LoadedModel:
        model = self._models.get(name)
        if model is None:
            detail = self._load_errors.get(name)
            raise KeyError(
                f"model '{name}' is not available"
                + (
                    f" (load error: {detail})"
                    if detail
                    else " — run `python ml/training/run_all.py` to train it"
                )
            )
        return model

    def names(self) -> list[str]:
        return sorted(self._models)

    def is_loaded(self, name: str) -> bool:
        return name in self._models

    def all_versions(self) -> list[dict[str, Any]]:
        """Every version on disk, newest first — powers the model-performance page."""
        out: list[dict[str, Any]] = []
        if not self.root.exists():
            return out
        for name_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for version_dir in sorted(name_dir.glob("v*"), reverse=True):
                manifest_path = version_dir / "manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text("utf-8"))
                except json.JSONDecodeError:
                    continue
                out.append(
                    {
                        "name": name_dir.name,
                        "version": int(version_dir.name.lstrip("v") or 0),
                        "tag": f"{name_dir.name}-v{version_dir.name.lstrip('v')}",
                        "algorithm": manifest.get("algorithm"),
                        "task": manifest.get("task"),
                        "metrics": manifest.get("metrics", {}),
                        "cv": manifest.get("cv", {}),
                        "comparison": manifest.get("comparison", []),
                        "dataset": manifest.get("dataset", {}),
                        "data_hash": manifest.get("data_hash"),
                        "data_version": manifest.get("data_version"),
                        "mlflow_run_id": manifest.get("mlflow_run_id"),
                        "trained_at": manifest.get("trained_at"),
                        "limitations": manifest.get("limitations"),
                        "target_description": manifest.get("target_description"),
                        "is_active": bool(
                            self._models.get(name_dir.name)
                            and self._models[name_dir.name].version
                            == int(version_dir.name.lstrip("v") or 0)
                        ),
                    }
                )
        out.sort(key=lambda r: (r["name"], -r["version"]))
        return out

    def report(self, name: str) -> dict[str, Any]:
        """The full evaluation report for the active version of `name`."""
        model = self.require(name)
        m = model.manifest
        return {
            "name": name,
            "version": model.version,
            "algorithm": m.get("algorithm"),
            "task_type": m.get("task"),
            "target_description": m.get("target_description", ""),
            "metrics": m.get("metrics", {}),
            "classification_report": m.get("classification_report"),
            "confusion_matrix": m.get("confusion_matrix"),
            "confusion_matrix_labels": m.get("confusion_matrix_labels"),
            "cv_folds": m.get("cv_folds", []),
            "feature_importance": m.get("feature_importance", []),
            "comparison": m.get("comparison", []),
            "hyperparameters": m.get("hyperparameters", {}),
            "dataset": m.get("dataset", {}),
            "limitations": m.get("limitations"),
            "trained_at": m.get("trained_at"),
            "selection_criterion": m.get("selection_criterion"),
        }

    def comparison(self, name: str) -> dict[str, Any]:
        """Model-comparison table for one target, straight from the training run."""
        model = self.require(name)
        m = model.manifest
        rows = []
        for r in m.get("comparison", []):
            rows.append(
                {
                    "model": r.get("model"),
                    "accuracy": r.get("accuracy"),
                    "precision": r.get("precision"),
                    "recall": r.get("recall"),
                    "f1": r.get("f1"),
                    "roc_auc": r.get("roc_auc"),
                    "mae": r.get("mae"),
                    "rmse": r.get("rmse"),
                    "r2": r.get("r2"),
                    "cv_f1_mean": r.get("cv_f1_macro") or r.get("cv_mae"),
                    "training_time_seconds": r.get("training_time_seconds", 0.0),
                    "selected": bool(r.get("selected")),
                }
            )
        return {
            "name": name,
            "metric_focus": m.get("primary_metric") or m.get("task"),
            "selection_criterion": m.get("selection_criterion", ""),
            "rows": rows,
            "selected_model": m.get("algorithm", ""),
            "evaluated_at": m.get("trained_at"),
        }

    def loaded_summary(self) -> list[str]:
        return [f"{m.tag} ({m.manifest.get('algorithm')})" for m in self._models.values()]


_registry: ModelRegistry | None = None


def get_registry(reload: bool = False) -> ModelRegistry:
    global _registry
    if _registry is None or reload:
        _registry = ModelRegistry()
    return _registry
