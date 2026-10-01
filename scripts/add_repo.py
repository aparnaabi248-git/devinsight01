"""Register a repository for extraction, then analyse it from its real git history.

Uses the git transport rather than the GitHub REST API, so it works without a token and
without spending any API quota. Use this when the API reports a rate-limit error.

Usage:
    python scripts/add_repo.py owner/name                 # clone + register
    python scripts/add_repo.py owner/name --analyse       # clone + ingest into the DB
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "backend"), str(REPO_ROOT / "ml"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import EXTRA_REPOS_FILE, RAW_DIR  # noqa: E402
from sqlalchemy import select  # noqa: E402

USER_AGENT = "DevInsight/1.0"


def register(full_name: str) -> None:
    EXTRA_REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = [
        line.strip()
        for line in
        (EXTRA_REPOS_FILE.read_text(encoding="utf-8").splitlines()
         if EXTRA_REPOS_FILE.exists() else [])
    ]
    if full_name in existing:
        print(f"  already registered: {full_name}")
        return
    existing.append(full_name)
    EXTRA_REPOS_FILE.write_text("\n".join(existing) + "\n", encoding="utf-8")
    print(f"  registered: {full_name} -> {EXTRA_REPOS_FILE}")


def clone(full_name: str) -> Path | None:
    owner, _, name = full_name.partition("/")
    target = RAW_DIR / "repos" / f"{owner}__{name}"
    if (target / ".git").exists():
        count = subprocess.run(["git", "-C", str(target), "rev-list", "--count", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        print(f"  already cloned: {full_name} ({count} commits)")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--quiet", f"https://github.com/{full_name}.git", str(target)],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        print(f"  clone FAILED for {full_name}:\n    {result.stderr.strip()[:300]}")
        print("  If this repository is private, a GITHUB_TOKEN is required.")
        return None
    count = subprocess.run(["git", "-C", str(target), "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    print(f"  cloned: {full_name} ({count} commits)")
    return target


def main() -> int:
    if len(sys.argv) < 2 or "/" not in sys.argv[1]:
        print(__doc__)
        return 1
    full_name = sys.argv[1].strip().rstrip("/")
    analyse = "--analyse" in sys.argv or "--analyze" in sys.argv

    print("=" * 62)
    print(f"ADDING REPOSITORY: {full_name}")
    print("=" * 62)
    if clone(full_name) is None:
        return 1
    register(full_name)

    # Extract this repository's commits through the same ETL the models were trained on.
    from data.github_history import extract_repo_commits

    from config import all_repos

    spec = next((s for s in all_repos() if s.full_name == full_name), None)
    if spec is None:
        print("  repository did not register")
        return 1
    commits, files = extract_repo_commits(spec)
    print(f"  extracted {len(commits):,} commits and {len(files):,} file changes")

    if not analyse:
        print("\n  Now run:  python scripts/seed_local.py " + full_name)
        return 0

    print("\n  loading into PostgreSQL…")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from seed_local import load_sources, seed_repository  # noqa: PLC0415

    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.models.repository import Repository  # noqa: PLC0415

    load_sources()  # validates that the processed CSVs are reachable
    db = SessionLocal()
    try:
        n_commits, n_changes = seed_repository(db, full_name, commits, files)
        db.commit()
        repo = db.execute(
            select(Repository).where(Repository.full_name == full_name)
        ).scalar_one()
        print(f"\n  id={repo.id}  {repo.full_name}: {repo.ingested_commits} commits, "
              f"{n_changes} file changes, sync_status={repo.sync_status.value}")
    finally:
        db.close()

    print("\n  Refresh the dashboard — the repository is now listed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
