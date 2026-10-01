"""Seed the database from the already-cloned real repositories — no GitHub API needed.

The ETL extractor has already produced `data/processed/commits.csv` and
`file_changes.csv` from five real OSS repositories. This script loads those real commits,
their real per-file diffs, and the real contributors into PostgreSQL so the dashboard has
content without spending any GitHub quota.

Usage:  python scripts/seed_local.py [owner/name ...]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "backend"), str(REPO_ROOT / "ml"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.core.config import settings  # noqa: E402
from app.db.base import ChangeType, SyncStatus  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.activity import Commit, FileChange  # noqa: E402
from app.models.repository import Contributor, Repository  # noqa: E402

COMMITS_CSV = REPO_ROOT / "data" / "processed" / "commits.csv"
FILES_CSV = REPO_ROOT / "data" / "processed" / "file_changes.csv"

# Stars/fork counts are static reference figures for these well-known projects, used only
# for the repository card. All behavioural metrics come from the ingested history.
STATIC_META = {
    "pallets/click": {"stars": 36200, "forks": 1300, "language": "Python",
                      "description": "Composable command line interface toolkit",
                      "license_spdx": "BSD-3-Clause"},
    "psf/requests": {"stars": 52400, "forks": 9500, "language": "Python",
                     "description": "A simple, yet elegant, HTTP library",
                     "license_spdx": "Apache-2.0"},
    "pallets/flask": {"stars": 68600, "forks": 15700, "language": "Python",
                      "description": "The Python micro web framework",
                      "license_spdx": "BSD-3-Clause"},
    "encode/httpx": {"stars": 14100, "forks": 900, "language": "Python",
                     "description": "A next generation HTTP client for Python",
                     "license_spdx": "BSD-3-Clause"},
    "expressjs/express": {"stars": 65700, "forks": 20400, "language": "JavaScript",
                          "description": "Fast, unopinionated, minimalist web framework",
                          "license_spdx": "MIT"},
}

MAX_PER_REPO = 900


def author_login(record) -> str | None:
    """Reuse the ETL's authorship normalisation so contributors match training.

    `commits.csv` holds the raw `author_name`/`author_email` from git; the `author`
    column only exists after `clean_commits` runs. Applying the same rule here keeps the
    database consistent with the feature pipeline.
    """
    from preprocessing.clean_history import normalise_author

    login = normalise_author(pd.Series({
        "author": None,
        "author_email": getattr(record, "author_email", None),
        "author_name": getattr(record, "author_name", None),
    }))
    return None if login == "unknown" else login


def as_datetime(value) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.to_pydatetime().astimezone(timezone.utc)


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not COMMITS_CSV.exists() or not FILES_CSV.exists():
        raise SystemExit(
            f"Missing {COMMITS_CSV.name} / {FILES_CSV.name}.\n"
            "Generate them first:  python ml/data/acquire.py && "
            "cd ml && python -c \"import sys; sys.path.insert(0,'.'); "
            "from preprocessing.build_datasets import load_or_extract_defect_dataset as f; "
            "f()\""
        )
    commits = pd.read_csv(COMMITS_CSV, parse_dates=["authored_at", "committed_at"])
    files = pd.read_csv(FILES_CSV)
    return commits, files


def upsert_repository(db, full_name: str) -> Repository:
    owner_name, _, name = full_name.partition("/")
    meta = STATIC_META.get(full_name, {})
    repo = db.execute(
        select(Repository).where(Repository.full_name == full_name)
    ).scalar_one_or_none()
    if repo is None:
        repo = Repository(full_name=full_name, owner_name=owner_name, name=name)
        db.add(repo)
    repo.html_url = f"https://github.com/{full_name}"
    repo.clone_url = f"https://github.com/{full_name}.git"
    repo.default_branch = "main"
    repo.stars = meta.get("stars", 0)
    repo.forks = meta.get("forks", 0)
    repo.language = meta.get("language", "Python")
    repo.description = meta.get("description", "")
    repo.license_spdx = meta.get("license_spdx", "MIT")
    repo.sync_status = SyncStatus.completed
    repo.last_synced_at = datetime.now(timezone.utc)
    db.flush()
    return repo


def seed_repository(db, full_name: str, commits: pd.DataFrame, files: pd.DataFrame
                    ) -> tuple[int, int]:
    repo = upsert_repository(db, full_name)

    existing = {s for (s,) in db.execute(
        select(Commit.sha).where(Commit.repository_id == repo.id)).all()}
    new = commits[~commits["sha"].isin(existing)].sort_values("authored_at")
    if len(new) > MAX_PER_REPO:
        new = new.tail(MAX_PER_REPO)  # keep the most recent slice
    if new.empty:
        return 0, 0

    rows = []
    for record in new.itertuples(index=False):
        author = author_login(record)
        rows.append({
            "repository_id": repo.id,
            "sha": record.sha,
            "parent_sha": None,
            "author_login": author,
            "author_name": getattr(record, "author_name", None),
            "author_email": getattr(record, "author_email", None),
            "message": (getattr(record, "message", None) or record.subject or "")[:8000],
            "authored_at": as_datetime(record.authored_at),
            "additions": int(record.additions or 0),
            "deletions": int(record.deletions or 0),
            "files_changed": int(record.files_changed or 0),
            "is_merge": bool(getattr(record, "is_merge", False)),
            "is_bugfix": bool(getattr(record, "is_bugfix", False)),
        })
    db.bulk_insert_mappings(Commit, rows)
    db.flush()

    # Per-file diffstat for the commits we just inserted.
    wanted = {r["sha"] for r in rows}
    repo_commits = files[files["sha"].isin(wanted)]
    id_map = {sha: cid for sha, cid in db.execute(
        select(Commit.sha, Commit.id).where(Commit.repository_id == repo.id)).all()}
    change_rows = []
    for record in repo_commits.itertuples(index=False):
        commit_id = id_map.get(record.sha)
        if commit_id is None:
            continue
        change_rows.append({
            "commit_id": commit_id,
            "path": str(record.path)[:512],
            "old_path": getattr(record, "old_path", None),
            "change_type": ChangeType(getattr(record, "change_type", "modified") or "modified"),
            "additions": int(record.additions or 0),
            "deletions": int(record.deletions or 0),
        })
    for i in range(0, len(change_rows), 5000):
        db.bulk_insert_mappings(FileChange, change_rows[i:i + 5000])

    # Aggregate contributors from the real author identities in the data.
    agg: dict[str, dict] = defaultdict(
        lambda: {"commits_count": 0, "additions": 0, "deletions": 0,
                 "first": None, "last": None, "files": set()})
    for record in new.itertuples(index=False):
        login = author_login(record)
        if not login:
            continue
        when = as_datetime(record.authored_at)
        entry = agg[login.strip()]
        entry["commits_count"] += 1
        entry["additions"] += int(record.additions or 0)
        entry["deletions"] += int(record.deletions or 0)
        entry["files"].add(int(record.files_changed or 0))
        entry["first"] = when if entry["first"] is None else min(entry["first"], when)
        entry["last"] = when if entry["last"] is None else max(entry["last"], when)

    existing_contributors = {
        login for (login,) in db.execute(
            select(Contributor.github_login).where(Contributor.repository_id == repo.id)).all()}
    contributor_rows = [
        {
            "repository_id": repo.id, "github_login": login,
            "commits_count": v["commits_count"], "additions": v["additions"],
            "deletions": v["deletions"], "files_touched": max(v["files"], default=0),
            "first_commit_at": v["first"], "last_commit_at": v["last"],
        }
        for login, v in agg.items() if login not in existing_contributors
    ]
    if contributor_rows:
        db.bulk_insert_mappings(Contributor, contributor_rows)

    repo.ingested_commits = db.execute(
        select(func.count(Commit.id)).where(Commit.repository_id == repo.id)
    ).scalar_one()
    db.flush()
    return len(rows), len(change_rows)


def main() -> int:
    wanted = set(sys.argv[1:])
    commits, files = load_sources()
    if wanted:
        commits = commits[commits["repository"].isin(wanted)]
        files = files[files["repository"].isin(wanted)]

    db = SessionLocal()
    try:
        print("=" * 68)
        print("SEEDING FROM LOCAL GIT HISTORY (no GitHub API calls)")
        print("=" * 68)
        total_commits = total_changes = 0
        for full_name in sorted(commits["repository"].unique()):
            repo_commits = commits[commits["repository"] == full_name]
            repo_files = files[files["repository"] == full_name]
            n_commits, n_changes = seed_repository(db, full_name, repo_commits, repo_files)
            total_commits += n_commits
            total_changes += n_changes
            print(f"  {full_name:<24} +{n_commits:>5} commits  +{n_changes:>6} file changes")
        db.commit()

        print(f"\n  total: {total_commits:,} commits, {total_changes:,} file changes")
        for repo in db.execute(select(Repository)).scalars():
            contributors = db.execute(
                select(func.count(Contributor.id)).where(
                    Contributor.repository_id == repo.id)).scalar_one()
            print(f"    id={repo.id}  {repo.full_name:<22} "
                  f"{repo.ingested_commits:>5} commits  {contributors:>4} contributors")
        print("\n  open http://localhost:5173 and sign in as demo / DemoPass123")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"  seed failed: {type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
