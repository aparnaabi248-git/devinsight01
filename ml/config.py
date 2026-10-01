"""Global ML configuration: paths, seeds, label definitions, model registry metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ML_ROOT = REPO_ROOT / "ml"
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
FEATURES_DIR = REPO_ROOT / "data" / "features"
ARTIFACT_DIR = ML_ROOT / "artifacts"
REPORT_DIR = ML_ROOT / "reports"
DATA_VERSION = "v1.0.0"

for _d in (PROCESSED_DIR, FEATURES_DIR, ARTIFACT_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

RANDOM_SEED = int(os.getenv("ML_SEED", "42"))
TEST_SIZE = 0.15
VALID_SIZE = 0.15
CV_FOLDS = 5


@dataclass(frozen=True)
class RepoSpec:
    """A real open-source repository used as a data source."""

    full_name: str
    local_path: Path
    language: str


# The five real repositories cloned into data/raw/repos. 23,125 real commits total.
REAL_REPOS: tuple[RepoSpec, ...] = (
    RepoSpec("pallets/click", RAW_DIR / "repos" / "pallets__click", "Python"),
    RepoSpec("psf/requests", RAW_DIR / "repos" / "psf__requests", "Python"),
    RepoSpec("pallets/flask", RAW_DIR / "repos" / "pallets__flask", "Python"),
    RepoSpec("encode/httpx", RAW_DIR / "repos" / "encode__httpx", "Python"),
    RepoSpec("expressjs/express", RAW_DIR / "repos" / "expressjs__express", "JavaScript"),
)

# User-supplied repositories, cloned on demand via `POST /api/repositories/analyze` or
# `python ml/data/acquire.py --add owner/name`. Kept separate from REAL_REPOS so the
# training baseline stays fixed and reproducible while ad-hoc repositories can be added
# without changing the training set.
EXTRA_REPOS_FILE = RAW_DIR / "extra_repos.txt"


def extra_repos() -> list[RepoSpec]:
    """Repositories added at runtime, one `owner/name` per line in data/raw/extra_repos.txt."""
    if not EXTRA_REPOS_FILE.exists():
        return []
    specs: list[RepoSpec] = []
    for line in EXTRA_REPOS_FILE.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#") or "/" not in name:
            continue
        owner, _, repo = name.partition("/")
        specs.append(
            RepoSpec(
                full_name=name,
                local_path=RAW_DIR / "repos" / f"{owner}__{repo}",
                language="unknown",
            )
        )
    return specs


def all_repos() -> tuple[RepoSpec, ...]:
    """The full extraction set: the fixed training baseline plus any ad-hoc repositories."""
    return REAL_REPOS + tuple(extra_repos())


ISSUE_CORPUS = RAW_DIR / "github_issues.jsonl"
ISSUE_CORPUS_SOURCE = (
    "Public GitHub issue corpus (huggingface/datasets repository issues) containing "
    "verbatim GitHub REST API issue objects: title, body, labels, state, comments, "
    "created_at, closed_at."
)

# ------------------------------------------------------------------------ labels
DEFECT_WINDOW_DAYS = 30
DEFECT_TARGET = "is_defective"
"""A commit is labelled defective if a *different* author, within
DEFECT_WINDOW_DAYS, commits a fix-intent change touching >=1 of the same paths.
This is the standard bug-inducing-change proxy from the JIT defect-prediction
literature. See ml/docs/LABELS.md for its limitations."""

RISK_BANDS = (("LOW", 0.33), ("MEDIUM", 0.66), ("HIGH", 1.01))
EFFORT_TARGET = "resolution_hours"

TARGET_DESCRIPTIONS = {
    "defect_risk": (
        "Binary defect-inducing-change label derived from real git history: a commit is "
        "positive if another author fixes the same paths within 30 days with a fix-intent "
        "commit message. Risk bands: LOW < 0.33, MEDIUM < 0.66, HIGH >= 0.66."
    ),
    "issue_classifier": (
        "Multiclass issue category (BUG / FEATURE_REQUEST / DOCUMENTATION / QUESTION / "
        "ENHANCEMENT / OTHER) inferred from real issue titles, bodies and repository labels."
    ),
    "issue_priority": (
        "4-class urgency (CRITICAL / HIGH / MEDIUM / LOW) derived from the issue's own "
        "human-applied labels, then predicted from its title, body, labels, discussion "
        "volume and author association. An estimate, not a decision. The target is "
        "deliberately label-only: using text or comment counts to build the target while "
        "also supplying them as features would leak, inflating the score without adding "
        "signal."
    ),
    "issue_effort": (
        "Regression on time-to-close hours (closed_at - created_at) of real issues. GitHub "
        "records no engineering hours, so time-to-close is used as a coarse proxy that "
        "includes queue time."
    ),
}

LIMITATIONS = {
    "defect_risk": (
        "The label is a proxy: absence of an observable follow-up fix is treated as 'not "
        "defective', so silent defects are counted as negatives. Fix-intent detection relies "
        "on commit-message conventions, which differ between projects. The model is trained "
        "on 5 Python/JavaScript OSS projects and may not transfer to closed-source or "
        "non-OSS codebases."
    ),
    "issue_classifier": (
        "Categories are inferred from project-specific label vocabulary, so label meanings "
        "differ between projects and the model will not transfer without relabelling. Short "
        "issues with no body carry little signal. The OTHER class absorbs everything the "
        "vocabulary does not cover. The model tops out at 6 coarse categories; it does not "
        "attempt assignee or team routing, and it is trained on a single project."
    ),
    "issue_priority": (
        "Priority is a statistical estimate from historical patterns, not an authoritative "
        "business decision, and human triage remains the source of truth. Three limits are "
        "worth stating. First, the target is a mapping from this project's label vocabulary, "
        "so 'enhancement' means whatever maintainers of this repository mean by it; another "
        "project may label differently and the model will not transfer without relabelling. "
        "Second, urgency is not directly observable, so the label is a proxy — a genuine "
        "sev-1 outage labelled only 'bug' is indistinguishable from a typo labelled 'bug'. "
        "Third, the training corpus is a single project, so the reported score measures "
        "within-project predictability and should not be read as general triage accuracy."
    ),
    "issue_effort": (
        "Time-to-close includes queue and wait time and is therefore an upper bound on, not "
        "a measurement of, hands-on engineering effort. Noisy for issues left open, closed "
        "in bulk, or blocked on external dependencies."
    ),
}


@dataclass
class ModelSpec:
    name: str
    task: str  # binary | multiclass | regression
    target: str
    primary_metric: str
    selection_criterion: str
    version: int = 1
    extra: dict = field(default_factory=dict)


MODEL_SPECS = (
    ModelSpec("defect_risk", "binary", "is_defective", "f1", "stratified 5-fold CV macro F1"),
    ModelSpec(
        "issue_classifier",
        "multiclass",
        "category",
        "f1_macro",
        "stratified 5-fold CV macro F1",
    ),
    ModelSpec(
        "issue_priority", "multiclass", "priority", "f1_macro", "stratified 5-fold CV macro F1"
    ),
    ModelSpec(
        "issue_effort",
        "regression",
        "resolution_hours",
        "mae",
        "stratified 5-fold CV MAE (lowest wins)",
    ),
)
