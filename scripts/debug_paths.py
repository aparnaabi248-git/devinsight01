"""Debug the per-path history feature computation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

import pandas as pd
from features.defect_features import (
    _path_history,
    _paths_by_commit,
    _relevant_files,
)


def make(n):
    base = pd.Timestamp("2023-01-01", tz="UTC")
    authors = ["alice" if i % 2 == 0 else "bob" for i in range(n)]
    commits = pd.DataFrame(
        {
            "sha": [f"sha{i:03d}" for i in range(n)],
            "repo": ["a/b"] * n,
            "author": authors,
            "authored_at": [base + pd.Timedelta(days=i) for i in range(n)],
            "subject": [f"change {i}" for i in range(n)],
            "message": [f"change {i}" for i in range(n)],
            "author_name": [a.title() for a in authors],
            "author_email": [f"{a}@x.io" for a in authors],
            "additions": [10 + i for i in range(n)],
            "deletions": [i for i in range(n)],
            "files_changed": 2,
            "churn": [10 + 2 * i for i in range(n)],
            "is_merge": [False] * n,
            "is_fix_intent": [i % 7 == 0 for i in range(n)],
            "is_bugfix": [i % 7 == 0 for i in range(n)],
            "hour": [10] * n,
            "weekday": [2] * n,
            "is_weekend": [0] * n,
            "is_business_hours": [1] * n,
            "is_defective": [1 if i % 7 == 0 else 0 for i in range(n)],
        }
    )
    files = pd.DataFrame(
        {
            "sha": [f"sha{i:03d}" for i in range(n)],
            "path": [f"src/mod{i % 3}.py" for i in range(n)],
            "old_path": [None] * n,
            "additions": [10] * n,
            "deletions": [1] * n,
            "change_type": ["modified"] * n,
        }
    )
    return commits, files


for n in (10, 15):
    commits, files = make(n)
    paths_by_sha = _paths_by_commit(files)
    path_times, path_defects = _path_history(commits, files)
    print(f"\n=== n={n} ===")
    print("relevant files rows:", len(_relevant_files(files)))
    print("path_times keys:", {k: len(v) for k, v in path_times.items()})
    print(
        "first 4 path_times:",
        {
            k: [round(x / 86400, 1) for x in v[:4]]
            for k, v in list(path_times.items())[:2]
        },
    )
    for i in range(min(6, n)):
        sha = f"sha{i:03d}"
        paths = paths_by_sha.get(sha)
        t = commits.loc[commits["sha"] == sha, "authored_at"].iloc[0].timestamp()
        counts = []
        for p in paths or []:
            series = path_times.get(p)
            if not series:
                continue
            import bisect

            counts.append(bisect.bisect_left(series, t))
        print(f"  {sha} paths={paths} counts={counts}")
