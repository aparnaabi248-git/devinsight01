"""Acquisition layer: clone the real training repositories and download the issue corpus.

Run once before training:
    python ml/data/acquire.py
    python ml/data/acquire.py --max-commits 2000   # a quicker subset

Everything fetched here is real public data. The repositories are cloned over the git
transport, which is not subject to the REST API rate limit, and the issue corpus is a
public mirror of verbatim GitHub REST API issue objects.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ISSUE_CORPUS, RAW_DIR, REAL_REPOS, extra_repos  # noqa: E402

USER_AGENT = "DevInsight/1.0 (+https://github.com/)"
ISSUE_CORPUS_URL = (
    "https://huggingface.co/datasets/Motahar/github-issues/resolve/main/"
    "issues-datasets-with-comments.jsonl"
)


def clone_repo(
    full_name: str, destination: Path, *, depth: int | None = None, force: bool = False
) -> bool:
    """Clone a real repository. Full history is required — the defect model needs it."""
    if (destination / ".git").exists() and not force:
        commits = run_git(destination, "rev-list", "--count", "HEAD")
        print(f"  [skip] {full_name}: already cloned ({commits} commits)")
        return True
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = ["git", "clone", "--quiet"]
    if depth:
        args += ["--depth", str(depth)]
    args += [f"https://github.com/{full_name}.git", str(destination)]
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", b"")
        print(f"  [fail] {full_name}: {detail[:200] if detail else exc}")
        return False
    print(
        f"  [ok]   {full_name}: {run_git(destination, 'rev-list', '--count', 'HEAD')} commits"
    )
    return True


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300
    )
    return result.stdout.strip()


def download_issue_corpus(destination: Path = ISSUE_CORPUS, *, force: bool = False) -> bool:
    """Download the public GitHub issue corpus (JSONL of real issue objects)."""
    if destination.exists() and destination.stat().st_size > 0 and not force:
        print(f"  [skip] issue corpus already present ({destination.stat().st_size:,} bytes)")
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ISSUE_CORPUS_URL, headers={"User-Agent": USER_AGENT})
    print(f"  [get]  {ISSUE_CORPUS_URL}")
    try:
        with (
            urllib.request.urlopen(request, timeout=300) as response,
            destination.with_suffix(".part").open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        print(f"  [fail] issue corpus download: {exc}")
        return False
    shutil.move(str(destination.with_suffix(".part")), str(destination))
    print(f"  [ok]   issue corpus: {destination.stat().st_size:,} bytes")
    return True


def main(
    max_commits: int | None = None, force: bool = False, skip_issues: bool = False
) -> int:
    print("=" * 74)
    print("ACQUIRING REAL TRAINING DATA")
    print("=" * 74)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Any repositories the user registered through the API, plus the fixed baseline.
    targets = list(REAL_REPOS) + list(extra_repos())
    print(f"\nCloning {len(targets)} real repositories (git transport, no API quota):")
    ok = 0
    for spec in targets:
        if clone_repo(spec.full_name, spec.local_path, force=force):
            ok += 1
    print(f"  -> {ok}/{len(targets)} repositories available")

    if not skip_issues:
        print("\nDownloading the public issue corpus:")
        download_issue_corpus(force=force)

    missing = [s.full_name for s in REAL_REPOS if not s.local_path.exists()]
    if missing:
        print(f"\nMissing repositories: {', '.join(missing)}")
        print("Training needs at least one real repository — check network access.")
        return 1
    print("\nAcquisition complete. Next:  cd ml && python training/run_all.py")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-commits",
        type=int,
        default=None,
        help="clone with a depth limit (faster, less history)",
    )
    parser.add_argument("--force", action="store_true", help="re-clone from scratch")
    parser.add_argument(
        "--skip-issues",
        action="store_true",
        help="only fetch repositories, not the issue corpus",
    )
    args = parser.parse_args()
    sys.exit(
        main(max_commits=args.max_commits, force=args.force, skip_issues=args.skip_issues)
    )
