"""FEATURE ENGINEERING for the defect-risk model.

Leakage control: every historical feature is computed in a single forward pass over
commits sorted by time within each repository, using only events strictly *before* the
commit being featurised. The features for a commit at time t therefore cannot observe any
event at or after t.

The pass is a plain Python loop over dicts/deques: O(n) with a small constant, and far
easier to audit than a vectorised equivalent of the same logic.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from preprocessing.clean_history import is_low_signal_path, path_class

FEATURE_COLUMNS = [
    # --- the change itself --------------------------------------------------
    "log_additions",
    "log_deletions",
    "log_churn",
    "log_files",
    "net_lines",
    "churn_ratio",
    "additions_per_file",
    "deletions_per_file",
    "is_merge",
    "is_bugfix",
    "commit_hour",
    "commit_weekday",
    "is_weekend",
    "is_business_hours",
    "message_length",
    "has_issue_ref",
    "message_question_ratio",
    # --- file composition ---------------------------------------------------
    "files_touched",
    "files_source",
    "files_test",
    "files_doc",
    "files_generated",
    "files_other",
    "additions_test",
    "additions_source",
    "additions_doc",
    "deletions_test",
    "deletions_source",
    # --- author history (prior commits only) --------------------------------
    "author_commit_count",
    "author_defect_rate",
    "author_repo_share",
    "author_tenure_days",
    "days_since_author_prev",
    # --- repository activity (prior commits only) ---------------------------
    "hours_since_repo_last",
    "repo_commits_last_7d",
    "repo_commits_last_30d",
    "repo_distinct_authors_30d",
    "repo_defect_rate_30d",
    "repo_churn_last_30d",
    "repo_age_days",
    # --- file history (prior changes only) ----------------------------------
    "path_change_count",
    "path_defect_rate",
    "path_age_days",
    "paths_avg_age",
    "paths_ever_touched_by_author",
    "path_is_lockfile",
    "path_is_vendor",
    "path_is_test",
    "path_is_doc",
]
TARGET_COLUMN = "is_defective"
TIME_COLUMN = "authored_at"
ID_COLUMNS = ["sha", "repo", "author", "authored_at", "subject"]
MAX_PATHS_PER_COMMIT = 60

COMPOSITION_COLUMNS = [
    "files_source",
    "files_test",
    "files_doc",
    "files_generated",
    "files_other",
    "additions_test",
    "additions_source",
    "additions_doc",
    "deletions_test",
    "deletions_source",
]

PATH_FLAG_PATTERNS = {
    "path_is_lockfile": r"(package-lock\.json|yarn\.lock|poetry\.lock|Cargo\.lock|"
    r"composer\.lock|go\.sum)$",
    "path_is_vendor": r"(^|/)(node_modules|vendor|third_party|site-packages)(/|$)",
    "path_is_test": r"(^|/)(tests?|spec|specs|__tests__)(/|$)|(^|/)test_|_test\.|\.test\.",
    "path_is_doc": r"\.(md|rst|txt|adoc)$",
}
PATH_FLAG_RE = {k: re.compile(v, re.IGNORECASE) for k, v in PATH_FLAG_PATTERNS.items()}

DAY = 86400.0
WINDOW_7D = 7 * DAY
WINDOW_30D = 30 * DAY
WINDOW_RETENTION = 150 * DAY


# ------------------------------------------------------------- per-commit only
def _static_features(commits: pd.DataFrame) -> pd.DataFrame:
    """Features derivable from the commit in isolation — no history, so no leakage."""
    df = commits.copy()
    safe_files = df["files_changed"].replace(0, np.nan)
    total = (df["additions"] + df["deletions"]).replace(0, np.nan)

    df["log_additions"] = np.log1p(df["additions"])
    df["log_deletions"] = np.log1p(df["deletions"])
    df["log_churn"] = np.log1p(df["churn"])
    df["log_files"] = np.log1p(df["files_changed"])
    df["net_lines"] = df["additions"] - df["deletions"]
    df["churn_ratio"] = (df["additions"] / total).fillna(0.5).clip(0, 1)
    df["additions_per_file"] = (df["additions"] / safe_files).fillna(0).clip(0, 5000)
    df["deletions_per_file"] = (df["deletions"] / safe_files).fillna(0).clip(0, 5000)
    df["files_touched"] = df["files_changed"].astype(float)
    df["commit_hour"] = df["hour"].astype(float)
    df["commit_weekday"] = df["weekday"].astype(float)
    for col in ("is_weekend", "is_business_hours", "is_merge", "is_bugfix"):
        df[col] = df[col].astype(float)
    df["message_length"] = df["subject"].str.len().clip(0, 2000).astype(float)
    df["has_issue_ref"] = (
        df["subject"].str.contains(r"#\d+", regex=True, na=False).astype(float)
    )
    df["message_question_ratio"] = (
        (df["subject"].str.count(r"\?") / df["subject"].str.len().clip(lower=1))
        .astype(float)
        .fillna(0)
        .clip(0, 1)
    )
    return df


def _relevant_files(files: pd.DataFrame) -> pd.DataFrame:
    """Drop lockfiles, vendored trees and changelogs — churn there is not a defect signal."""
    if files is None or files.empty:
        return pd.DataFrame(
            columns=["sha", "path", "old_path", "additions", "deletions", "cls"]
        )
    f = files.copy()
    f["cls"] = f["path"].map(path_class)
    f = f[~f["path"].map(is_low_signal_path)]
    f = f[f["cls"] != "vendor"]
    return f


def _file_composition(files: pd.DataFrame, shas: list[str]) -> pd.DataFrame:
    """files_<class> counts plus per-class additions/deletions, keyed by commit sha."""
    index = pd.Index(shas, name="sha")
    comp = pd.DataFrame(0.0, index=index, columns=COMPOSITION_COLUMNS)
    f = _relevant_files(files)
    if f.empty:
        return comp
    f = f[f["sha"].isin(index)]
    if f.empty:
        return comp

    counts = f.pivot_table(index="sha", columns="cls", values="path", aggfunc="count").fillna(
        0
    )
    for cls in ("source", "test", "doc", "generated", "other"):
        if cls in counts.columns:
            comp[f"files_{cls}"] = counts[cls]
    for measure in ("additions", "deletions"):
        agg = f.pivot_table(index="sha", columns="cls", values=measure, aggfunc="sum").fillna(
            0
        )
        for cls in ("test", "source", "doc"):
            if cls in agg.columns:
                comp[f"{measure}_{cls}"] = agg[cls]
    return comp


def _paths_by_commit(files: pd.DataFrame) -> dict[str, list[str]]:
    """sha -> touched paths, with a rename also registering the previous path."""
    f = _relevant_files(files)
    f = f[f["cls"] != "generated"]
    if f.empty:
        return {}
    renames = f.loc[f["old_path"].notna(), ["sha", "old_path"]].rename(
        columns={"old_path": "path"}
    )
    f = pd.concat(
        [f[["sha", "path"]].drop_duplicates(), renames.drop_duplicates()],
        ignore_index=True,
    )
    return {
        sha: grp["path"].tolist()[:MAX_PATHS_PER_COMMIT]
        for sha, grp in f.groupby("sha", sort=False)
    }


def _path_history(
    commits: pd.DataFrame, files: pd.DataFrame
) -> tuple[dict[str, list[float]], dict[str, list[int]]]:
    """path -> (sorted prior-change timestamps, aligned defect flags) for O(log n) lookup."""
    f = _relevant_files(files)
    if f.empty:
        return {}, {}
    sha_time = dict(zip(commits["sha"], commits[TIME_COLUMN]))
    sha_defect = dict(zip(commits["sha"], commits[TARGET_COLUMN]))

    f = f.assign(_t=f["sha"].map(sha_time), _d=f["sha"].map(sha_defect).fillna(0).astype(int))
    f = f.dropna(subset=["_t"])
    renames = f.loc[f["old_path"].notna(), ["old_path", "_t", "_d"]].rename(
        columns={"old_path": "path"}
    )
    f = pd.concat(
        [f[["path", "_t", "_d"]].drop_duplicates(), renames.drop_duplicates()],
        ignore_index=True,
    )
    # Force nanosecond resolution *before* viewing as int64. pandas 2.x commonly hands
    # back `datetime64[us]`, where a bare `.astype("int64")` yields microseconds and
    # `DatetimeIndex.asi8` returns those same microseconds — so dividing by 1e9 would
    # silently produce kiloseconds and break every ordered lookup by a factor of 1000.
    utc = f["_t"].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    f["epoch"] = utc.astype("int64") / 1e9

    times: dict[str, list[float]] = {}
    defects: dict[str, list[int]] = {}
    for path, grp in f.groupby("path", sort=False):
        ordered = grp.sort_values("epoch")
        times[path] = ordered["epoch"].tolist()
        defects[path] = ordered["_d"].tolist()
    return times, defects


# ------------------------------------------------------------ history-dependent
def _historical_features(
    df: pd.DataFrame,
    paths_by_sha: dict[str, list[str]],
    path_times: dict[str, list[float]],
    path_defects: dict[str, list[int]],
) -> pd.DataFrame:
    """One forward pass per repository, consuming only strictly prior events."""
    records: list[dict] = []
    order: list[int] = []

    for _, grp in df.groupby("repo", sort=False):
        grp = grp.sort_values(TIME_COLUMN, kind="mergesort")
        repo_rows = grp.to_dict("records")
        repo_start = repo_rows[0][TIME_COLUMN].timestamp()

        author_last: dict[str, float] = {}
        author_count: dict[str, int] = defaultdict(int)
        author_defects: dict[str, int] = defaultdict(int)
        author_first: dict[str, float] = {}
        author_paths: dict[str, set[str]] = defaultdict(set)
        window: deque[tuple[float, str, int, float]] = deque()

        for i, rec in enumerate(repo_rows):
            ts: pd.Timestamp = rec[TIME_COLUMN]
            t = ts.timestamp()
            author = rec["author"]
            paths = paths_by_sha.get(rec["sha"], [])
            defect = int(rec[TARGET_COLUMN])
            churn = float(rec["churn"])

            # --- repository windows over the preceding events only
            w7 = [(e[0], e[1]) for e in window if t - WINDOW_7D < e[0] < t]
            w30 = [e for e in window if t - WINDOW_30D < e[0] < t]
            n30 = len(w30)
            churn30 = sum(e[3] for e in w30)
            defect30 = sum(e[2] for e in w30)
            authors30 = len({e[1] for e in w30})

            prev_author_t = author_last.get(author)
            prev_count = author_count[author]
            prev_defects = author_defects[author]

            # --- file history over strictly prior changes only
            counts: list[int] = []
            ages: list[float] = []
            seen_before = 0
            for path in paths:
                series = path_times.get(path)
                if not series:
                    continue
                k = bisect_left(series, t)  # prior changes to this path
                counts.append(k)
                ages.append((t - series[0]) / DAY)
                if k and path in author_paths[author]:
                    seen_before += 1

            if counts:
                # Aggregate prior defect flags across this commit's paths.
                prior_defects = 0
                seen_paths = 0
                for path, c in zip(paths, counts):
                    if not c:
                        continue
                    flags = path_defects.get(path)
                    if flags:
                        prior_defects += sum(flags[:c])
                    seen_paths += 1
                path_defect_rate = prior_defects / seen_paths if seen_paths else 0.0
                path_change_count = float(np.mean(counts))
                path_age = ages[0] if ages else 0.0
                paths_avg_age = float(np.mean(ages)) if ages else 0.0
            else:
                path_defect_rate = path_change_count = path_age = paths_avg_age = 0.0

            records.append(
                {
                    "author_commit_count": float(prev_count),
                    "author_defect_rate": float(prev_defects / prev_count)
                    if prev_count
                    else 0.0,
                    "author_repo_share": float(prev_count / i) if i else 0.0,
                    "author_tenure_days": (t - author_first.get(author, t)) / DAY,
                    "days_since_author_prev": (
                        (t - prev_author_t) / DAY if prev_author_t is not None else 365.0
                    ),
                    "hours_since_repo_last": (
                        (t - repo_rows[i - 1][TIME_COLUMN].timestamp()) / 3600.0
                        if i
                        else 720.0
                    ),
                    "repo_commits_last_7d": float(len(w7)),
                    "repo_commits_last_30d": float(n30),
                    "repo_distinct_authors_30d": float(authors30),
                    "repo_defect_rate_30d": (defect30 / n30) if n30 else 0.0,
                    "repo_churn_last_30d": float(churn30),
                    "repo_age_days": (t - repo_start) / DAY,
                    "path_change_count": float(path_change_count),
                    "path_defect_rate": float(path_defect_rate),
                    "path_age_days": float(path_age),
                    "paths_avg_age": float(paths_avg_age),
                    "paths_ever_touched_by_author": float(seen_before),
                }
            )
            order.append(int(rec["_row"]))

            # --- advance state only *after* the features were extracted
            author_last[author] = t
            author_count[author] = prev_count + 1
            author_defects[author] = prev_defects + defect
            author_first.setdefault(author, t)
            author_paths[author].update(paths)
            window.append((t, author, defect, churn))
            while len(window) > 1 and t - window[0][0] > WINDOW_RETENTION:
                window.popleft()

    hist = pd.DataFrame(records)
    hist["__order__"] = order
    return hist.sort_values("__order__").drop(columns="__order__").reset_index(drop=True)


def build_defect_features(commits: pd.DataFrame, files: pd.DataFrame) -> pd.DataFrame:
    """Assemble the complete defect-risk feature matrix, ordered by repo then time."""
    df = _static_features(commits)
    df = df.sort_values(["repo", TIME_COLUMN], kind="mergesort").reset_index(drop=True)
    df["_row"] = np.arange(len(df))

    df = df.join(_file_composition(files, df["sha"].tolist()), on="sha")
    for col in COMPOSITION_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    df[COMPOSITION_COLUMNS] = df[COMPOSITION_COLUMNS].fillna(0.0)

    paths_by_sha = _paths_by_commit(files)
    for name, pattern in PATH_FLAG_RE.items():
        df[name] = [
            float(any(pattern.search(p) for p in paths_by_sha.get(sha, [])))
            for sha in df["sha"]
        ]

    path_times, path_defects = _path_history(commits, files)
    hist = _historical_features(df, paths_by_sha, path_times, path_defects)
    for col in hist.columns:
        df[col] = hist[col].to_numpy()

    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    df[FEATURE_COLUMNS] = (
        df[FEATURE_COLUMNS]
        .apply(pd.to_numeric, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    return df[ID_COLUMNS + FEATURE_COLUMNS + [TARGET_COLUMN]].reset_index(drop=True)


def chronological_split(
    df: pd.DataFrame, test_frac: float = 0.15
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on the past, test on the future — the realistic deployment scenario."""
    ordered = df.sort_values(TIME_COLUMN, kind="mergesort").reset_index(drop=True)
    cut = int(len(ordered) * (1 - test_frac))
    return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()
