"""End-to-end dataset construction: real git history -> labelled feature matrices."""

from __future__ import annotations

import json

import pandas as pd

from config import DATA_VERSION, FEATURES_DIR, PROCESSED_DIR, REAL_REPOS
from data.github_history import extract_all, extract_releases
from features.defect_features import build_defect_features, chronological_split
from preprocessing.clean_history import build_defect_labels, clean_commits

DEFECT_CSV = FEATURES_DIR / "defect_features.csv"
DEFECT_SPLIT_DIR = FEATURES_DIR / "defect_splits"
RELEASES_CSV = PROCESSED_DIR / "releases.csv"


def _cache_path(name: str) -> object:
    return PROCESSED_DIR / name


def load_or_extract_defect_dataset(
    max_commits: int | None = None, rebuild: bool = False
) -> tuple[pd.DataFrame, dict]:
    """Build the defect-risk dataset from real git history, with an on-disk cache."""
    commits_csv = _cache_path("commits.csv")
    files_csv = _cache_path("file_changes.csv")

    if not rebuild and commits_csv.exists() and files_csv.exists():
        print("  [cache] reusing extracted git history")
        commits = pd.read_csv(commits_csv, parse_dates=["authored_at", "committed_at"])
        files = pd.read_csv(files_csv)
    else:
        print("  [extract] reading real git history (git log --numstat/--raw)")
        commits, files = extract_all(max_commits=max_commits)
        commits.to_csv(commits_csv, index=False)
        files.to_csv(files_csv, index=False)

    print(f"  [clean ] {len(commits):,} commits, {len(files):,} file changes")
    cleaned = clean_commits(commits)
    print(
        f"  [label ] deriving the defect proxy label "
        f"({int(cleaned['is_fix_intent'].sum()):,} fix-intent commits detected)"
    )

    labelled = build_defect_labels(cleaned, files)
    positive_rate = float(labelled["is_defective"].mean())
    print(
        f"  [label ] positive rate = {positive_rate:.4f} "
        f"({int(labelled['is_defective'].sum()):,} defective changes)"
    )

    features = build_defect_features(labelled, files)
    features.to_csv(DEFECT_CSV, index=False)

    meta = {
        "data_version": DATA_VERSION,
        "rows": int(len(features)),
        "features": int(
            len(
                [
                    c
                    for c in features.columns
                    if c
                    not in {"sha", "repo", "author", "authored_at", "subject", "is_defective"}
                ]
            )
        ),
        "positive_rate": round(positive_rate, 6),
        "repositories": sorted(features["repo"].unique().tolist()),
        "commits_per_repo": features.groupby("repo").size().to_dict(),
        "time_range": [str(features["authored_at"].min()), str(features["authored_at"].max())],
    }
    (PROCESSED_DIR / "defect_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    return features, meta


def make_defect_splits(features: pd.DataFrame, test_frac: float = 0.15) -> dict[str, str]:
    """Time-ordered holdout. Returns file paths so the split is reproducible and inspectable."""
    DEFECT_SPLIT_DIR.mkdir(parents=True, exist_ok=True)
    train, test = chronological_split(features, test_frac=test_frac)
    paths = {}
    for name, frame in (("train", train), ("test", test)):
        path = DEFECT_SPLIT_DIR / f"{name}.csv"
        frame.to_csv(path, index=False)
        paths[name] = str(path)
    paths["train_cut_date"] = str(train["authored_at"].max())
    paths["test_start_date"] = str(test["authored_at"].min())
    return paths


def build_release_timeline() -> pd.DataFrame:
    """Real release tags with their commit timestamps."""
    frames = [extract_releases(spec) for spec in REAL_REPOS if spec.local_path.exists()]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["repository", "tag_name", "published_at", "message"])
    releases = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["repository", "tag_name"]
    )
    releases.to_csv(RELEASES_CSV, index=False)
    return releases
