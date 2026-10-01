"""EXTRACTION — real commit history from cloned git repositories.

`git log --numstat` yields the *actual* per-commit line additions/deletions per file, and
`git log --raw` yields the per-file change status. Git only honours one diff format per
invocation, so we run both and join on (sha, path). Everything here comes straight out of
version control — nothing is simulated.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from config import RAW_DIR, RepoSpec, all_repos

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = _FIELD_SEP.join(["%H", "%P", "%an", "%ae", "%aI", "%cI", "%s", "%b"])


def _git(repo: Path, *args: str, timeout: int = 900) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:2])} failed in {repo.name}: {result.stderr[:300]}"
        )
    return result.stdout


def _to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip())
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def _parse_commits(text: str) -> list[tuple[dict, list[str]]]:
    """Split the pretty-formatted log into (commit dict, remaining body lines)."""
    out: list[tuple[dict, list[str]]] = []
    for chunk in text.split(_RECORD_SEP):
        if not chunk.strip():
            continue
        header, _, rest = chunk.partition("\n")
        # The pretty format is prefixed by \x1f, so header may start with the separator.
        parts = header.lstrip(_FIELD_SEP).split(_FIELD_SEP)
        if len(parts) < 7:
            continue
        sha, parents, an, ae, aI, cI, subject = parts[:7]
        body = parts[7] if len(parts) > 7 else ""
        commit = {
            "sha": sha.strip(),
            "parents": [p for p in parents.strip().split() if p],
            "author_name": an.strip(),
            "author_email": ae.strip(),
            "authored_at": _to_datetime(aI),
            "committed_at": _to_datetime(cI),
            "message": f"{subject}\n{body}".strip(),
        }
        out.append((commit, rest.splitlines()))
    return out


def _parse_numstat(lines: list[str]) -> dict[tuple[str, str], tuple[int, int]]:
    """`<add>\t<del>\t<path>` -> (additions, deletions). Binary diffs report '-'."""
    stats: dict[tuple[str, str], tuple[int, int]] = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds, dels, path = parts[0], parts[1], "\t".join(parts[2:])
        stats[path] = (
            int(adds) if adds.isdigit() else 0,
            int(dels) if dels.isdigit() else 0,
        )
    return stats


def _parse_raw(lines: list[str]) -> dict[str, tuple[str, str | None]]:
    """`:<mode> <mode> <sha> <sha> <status>\\t<path>` -> (change_type, old_path)."""
    statuses: dict[str, tuple[str, str | None]] = {}
    for line in lines:
        if not line.startswith(":"):
            continue
        meta, tab, path = line.partition("\t")
        cols = meta.split(" ")
        if not tab or len(cols) < 5:
            continue
        code = cols[4]
        if code[0] in {"R", "C"} and "\t" in path:
            old_path, _, new_path = path.partition("\t")
            statuses[new_path] = ("renamed" if code[0] == "R" else "added", old_path)
        else:
            statuses[path] = (
                {
                    "A": "added",
                    "D": "deleted",
                    "M": "modified",
                    "T": "modified",
                }.get(code[0], "modified"),
                None,
            )
    return statuses


def _change_type(status: str | None) -> str:
    return {"A": "added", "D": "deleted", "R": "renamed", "M": "modified"}.get(
        (status or "M")[:1].upper(), "modified"
    )


def extract_repo_commits(
    spec: RepoSpec, max_commits: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (commits, file_changes) DataFrames built from real git history."""
    limit = f"--max-count={max_commits}" if max_commits else "--max-count=200000"
    pretty = f"--pretty=format:{_RECORD_SEP}%x1f{_LOG_FORMAT}"
    common = ("log", "--all", pretty, "--no-renames", limit)

    numstat_text = _git(spec.local_path, *common, "--numstat")
    raw_text = _git(spec.local_path, *common, "--raw")

    numstat_by_sha = {
        commit["sha"]: _parse_numstat(lines) for commit, lines in _parse_commits(numstat_text)
    }
    raw_by_sha = {
        commit["sha"]: _parse_raw(lines) for commit, lines in _parse_commits(raw_text)
    }

    commit_rows: list[dict] = []
    file_rows: list[dict] = []
    for commit, _ in _parse_commits(numstat_text):
        sha = commit["sha"]
        if commit["authored_at"] is None:
            continue
        counts = numstat_by_sha.get(sha, {})
        statuses = raw_by_sha.get(sha, {})

        additions = deletions = 0
        for path, (adds, dels) in counts.items():
            change_type, old_path = statuses.get(path, ("modified", None))
            additions += adds
            deletions += dels
            file_rows.append(
                {
                    "sha": sha,
                    "repository": spec.full_name,
                    "path": path,
                    "old_path": old_path,
                    "additions": adds,
                    "deletions": dels,
                    "change_type": change_type,
                }
            )

        commit_rows.append(
            {
                **commit,
                "repository": spec.full_name,
                "language": spec.language,
                "is_merge": len(commit["parents"]) > 1,
                "additions": additions,
                "deletions": deletions,
                "files_changed": len(counts),
                "churn": additions + deletions,
                "subject": commit["message"].split("\n", 1)[0][:400],
            }
        )

    if not commit_rows:
        raise RuntimeError(f"no commits extracted from {spec.full_name}")

    commits = pd.DataFrame(commit_rows)
    files = pd.DataFrame(
        file_rows,
        columns=[
            "sha",
            "repository",
            "path",
            "old_path",
            "additions",
            "deletions",
            "change_type",
        ],
    )
    files = files[files["path"].notna() & (files["path"] != "")]
    return commits, files.reset_index(drop=True)


def extract_all(
    max_commits: int | None = None, workers: int = 5
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract every configured real repository in parallel and concatenate."""
    available = [s for s in all_repos() if s.local_path.exists()]
    if not available:
        raise FileNotFoundError(
            f"No repositories found under {RAW_DIR / 'repos'}. Run `python ml/data/acquire.py` "
            "first to clone the real repositories."
        )

    all_commits: list[pd.DataFrame] = []
    all_files: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(available))) as pool:
        futures = {pool.submit(extract_repo_commits, s, max_commits): s for s in available}
        for fut, spec in futures.items():
            c, f = fut.result()
            all_commits.append(c)
            all_files.append(f)
            print(f"  [extract] {spec.full_name}: {len(c):,} commits, {len(f):,} file changes")

    commits = pd.concat(all_commits, ignore_index=True)
    files = pd.concat(all_files, ignore_index=True) if all_files else pd.DataFrame()
    commits = commits.dropna(subset=["authored_at"]).sort_values("authored_at")
    return commits.reset_index(drop=True), files.reset_index(drop=True)


def extract_releases(spec: RepoSpec) -> pd.DataFrame:
    """Real release tags with their commit timestamps — the release-frequency signal."""
    cols = ["repository", "tag_name", "published_at", "message"]
    if not spec.local_path.exists():
        return pd.DataFrame(columns=cols)
    try:
        out = _git(
            spec.local_path,
            "for-each-ref",
            "--sort=-creatordate",
            f"--format=%(refname:short){_FIELD_SEP}%(creatordate:iso-strict)"
            f"{_FIELD_SEP}%(contents:subject)",
            "refs/tags",
        )
    except RuntimeError:
        return pd.DataFrame(columns=cols)
    rows = []
    for line in out.splitlines():
        parts = line.split(_FIELD_SEP)
        if len(parts) < 2 or not parts[0].strip():
            continue
        dt = _to_datetime(parts[1])
        if dt is None:
            continue
        rows.append(
            {
                "repository": spec.full_name,
                "tag_name": parts[0].strip(),
                "published_at": dt,
                "message": parts[2].strip() if len(parts) > 2 else "",
            }
        )
    return pd.DataFrame(rows, columns=cols)
