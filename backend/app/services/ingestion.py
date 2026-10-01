"""ETL ingestion: GitHub API -> normalised relational rows, with a job ledger.

Every write is idempotent (natural unique keys + upsert), so a re-run of the pipeline
never duplicates data and never re-hits GitHub for rows already stored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import GitHubError, NotFoundError
from app.core.logging import get_logger
from app.db.base import ChangeType, IssueState, JobStatus, PRState, SyncStatus
from app.models.activity import Commit, FileChange, PullRequest, Release
from app.models.analytics import RefreshJob
from app.models.issue import Issue, IssueComment, IssueLabel, IssueLabelMap
from app.models.repository import Contributor, Repository
from app.services.github_client import GitHubClient, parse_repo_url

log = get_logger("etl.ingestion")

# Fix-intent heuristics. Used for the `is_bugfix` flag on real commit messages.
BUGFIX_RE = re.compile(
    r"\b(fix(e[sd])?|bug|hotfix|patch|regression|broken|crash|error|defect|"
    r"issue\s*#?\d+|closes?\s*#\d+|resolves?\s*#\d+|correct(s|ed)?\b)",
    re.IGNORECASE,
)
MERGE_RE = re.compile(r"^Merge (pull request|branch|remote)", re.IGNORECASE)


def _dt(value: Any) -> datetime | None:
    """GitHub returns either an ISO-8601 string or a unix-millis integer."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ms(value: Any) -> datetime | None:
    """GitHub REST issue payloads use unix milliseconds (seconds for the HTML API)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=UTC)
    return _dt(value)


def upsert_repository(db: Session, data: dict[str, Any]) -> Repository:
    full_name = data["full_name"]
    owner_name, _, name = full_name.partition("/")
    payload = {
        "owner_name": owner_name,
        "name": name,
        "full_name": full_name,
        "description": data.get("description"),
        "html_url": data.get("html_url", f"https://github.com/{full_name}"),
        "clone_url": data.get("clone_url"),
        "default_branch": data.get("default_branch") or "main",
        "language": data.get("language"),
        "stars": int(data.get("stargazers_count") or 0),
        "forks": int(data.get("forks_count") or 0),
        "watchers": int(data.get("watchers_count") or 0),
        "open_issues_count": int(data.get("open_issues_count") or 0),
        "size_kb": int(data.get("size") or 0),
        "license_spdx": (data.get("license") or {}).get("spdx_id"),
        "is_fork": bool(data.get("fork", False)),
        "is_archived": bool(data.get("archived", False)),
        "updated_at": datetime.now(UTC),
    }
    stmt = pg_insert(Repository).values(**payload, created_at=datetime.now(UTC))
    stmt = stmt.on_conflict_do_update(
        index_elements=[Repository.owner_name, Repository.name], set_=payload
    )
    repo_id = db.execute(stmt.returning(Repository.id)).scalar_one()
    return db.get(Repository, repo_id)


def _upsert_contributors(db: Session, repo_id: int, commits: list[dict]) -> int:
    """Aggregate contributors in Python (compact, cacheable) then upsert in one statement."""
    agg: dict[str, dict[str, Any]] = {}
    for c in commits:
        login = (c.get("author") or {}).get("login")
        if not login:
            continue
        row = agg.setdefault(
            login,
            {
                "github_login": login,
                "github_id": (c.get("author") or {}).get("id"),
                "display_name": (c.get("commit") or {}).get("author", {}).get("name"),
                "commits_count": 0,
                "additions": 0,
                "deletions": 0,
                "first_commit_at": None,
                "last_commit_at": None,
            },
        )
        row["commits_count"] += 1
        row["additions"] += int(c.get("additions") or 0)
        row["deletions"] += int(c.get("deletions") or 0)
        when = _dt((c.get("commit") or {}).get("author", {}).get("date"))
        if when:
            if row["first_commit_at"] is None or when < row["first_commit_at"]:
                row["first_commit_at"] = when
            if row["last_commit_at"] is None or when > row["last_commit_at"]:
                row["last_commit_at"] = when

    if not agg:
        return 0

    # Merge with what is already stored so a re-ingest accumulates rather than replaces.
    existing = {
        login: (count, adds, dels)
        for login, count, adds, dels in db.execute(
            select(
                Contributor.github_login,
                Contributor.commits_count,
                Contributor.additions,
                Contributor.deletions,
            ).where(Contributor.repository_id == repo_id)
        ).all()
    }

    values = []
    for row in agg.values():
        prev_count, prev_adds, prev_dels = existing.get(row["github_login"], (0, 0, 0))
        values.append(
            {
                **row,
                "repository_id": repo_id,
                "commits_count": prev_count + row["commits_count"],
                "additions": prev_adds + row["additions"],
                "deletions": prev_dels + row["deletions"],
                "updated_at": datetime.now(UTC),
            }
        )

    db.execute(
        pg_insert(Contributor)
        .values(values)
        .on_conflict_do_update(
            index_elements=[Contributor.repository_id, Contributor.github_login],
            set_={
                "github_id": pg_insert(Contributor).excluded.github_id,
                "display_name": pg_insert(Contributor).excluded.display_name,
                "commits_count": pg_insert(Contributor).excluded.commits_count,
                "additions": pg_insert(Contributor).excluded.additions,
                "deletions": pg_insert(Contributor).excluded.deletions,
                "first_commit_at": pg_insert(Contributor).excluded.first_commit_at,
                "last_commit_at": pg_insert(Contributor).excluded.last_commit_at,
                "updated_at": pg_insert(Contributor).excluded.updated_at,
            },
        )
    )
    return len(values)


def upsert_commits(
    db: Session, repo_id: int, commits: list[dict], *, replace: bool = True
) -> int:
    """Store the real commit history plus its per-file diffstat."""
    if not commits:
        return 0

    if replace:
        existing = {
            s
            for (s,) in db.execute(
                select(Commit.sha).where(Commit.repository_id == repo_id)
            ).all()
        }
        stale = [c["sha"] for c in commits if c.get("sha") not in existing]
        if stale:
            db.execute(
                delete(Commit).where(
                    Commit.repository_id == repo_id, Commit.sha.in_(stale[:2000])
                )
            )

    existing_shas = {
        s
        for (s,) in db.execute(select(Commit.sha).where(Commit.repository_id == repo_id)).all()
    }

    rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    for c in commits:
        sha = c.get("sha")
        if not sha or sha in existing_shas:
            continue
        commit = c.get("commit") or {}
        author_block = commit.get("author") or {}
        message = (commit.get("message") or "").strip()
        files = c.get("files") or []
        additions = int(
            c.get("additions")
            if c.get("additions") is not None
            else sum(int(f.get("additions") or 0) for f in files)
        )
        deletions = int(
            c.get("deletions")
            if c.get("deletions") is not None
            else sum(int(f.get("deletions") or 0) for f in files)
        )
        rows.append(
            {
                "repository_id": repo_id,
                "sha": sha,
                "parent_sha": ((c.get("parents") or [{}])[0] or {}).get("sha"),
                "author_login": (c.get("author") or {}).get("login"),
                "author_name": author_block.get("name"),
                "author_email": author_block.get("email"),
                "message": message[:8000],
                "authored_at": _dt(author_block.get("date")) or datetime.now(UTC),
                "committed_at": _dt((commit.get("committer") or {}).get("date")),
                "additions": additions,
                "deletions": deletions,
                "files_changed": int(c.get("files_count") or len(files)),
                "is_merge": bool((c.get("parents") or []) and len(c["parents"]) > 1)
                or bool(MERGE_RE.match(message)),
                "is_bugfix": bool(BUGFIX_RE.search(message))
                and len(c.get("parents") or []) <= 1,
            }
        )
        for f in files:
            file_rows.append(
                {
                    "commit_sha": sha,
                    "path": (f.get("filename") or "")[:512],
                    "old_path": (f.get("previous_filename") or None),
                    "change_type": _change_type(f.get("status")),
                    "additions": int(f.get("additions") or 0),
                    "deletions": int(f.get("deletions") or 0),
                }
            )

    if not rows:
        return 0

    db.execute(
        pg_insert(Commit)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Commit.repository_id, Commit.sha])
    )

    if file_rows:
        # commit_sha -> id, in chunks to stay under the 32k bind-parameter limit.
        shas = sorted({r["commit_sha"] for r in file_rows})
        id_map: dict[str, int] = {}
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            for sha, cid in db.execute(
                select(Commit.sha, Commit.id).where(
                    Commit.repository_id == repo_id, Commit.sha.in_(chunk)
                )
            ).all():
                id_map[sha] = cid
        db.execute(delete(FileChange).where(FileChange.commit_id.in_(list(id_map.values()))))
        resolved = [
            {**r, "commit_id": id_map[r["commit_sha"]]}
            for r in file_rows
            if r["commit_sha"] in id_map
        ]
        if resolved:
            for i in range(0, len(resolved), 5000):
                # `FileChange.__table__` is typed as an Iterable of columns; the mapped
                # class is the equivalent, better-typed construct for `pg_insert`.
                db.execute(pg_insert(FileChange).values(resolved[i : i + 5000]))

    return len(rows)


def _change_type(status: str | None) -> ChangeType:
    return {
        "added": ChangeType.added,
        "removed": ChangeType.deleted,
        "renamed": ChangeType.renamed,
        "modified": ChangeType.modified,
    }.get((status or "modified").lower(), ChangeType.modified)


def upsert_pull_requests(db: Session, repo_id: int, prs: list[dict]) -> int:
    if not prs:
        return 0
    rows = []
    for p in prs:
        created = _dt(p.get("created_at"))
        rows.append(
            {
                "repository_id": repo_id,
                "number": int(p["number"]),
                "title": (p.get("title") or "")[:512],
                "body": p.get("body"),
                "state": PRState(p.get("state") or "open"),
                "merged": bool(p.get("merged_at")),
                "author_login": (p.get("user") or {}).get("login"),
                "merged_by": (p.get("merged_by") or {}).get("login"),
                "additions": int(p.get("additions") or 0),
                "deletions": int(p.get("deletions") or 0),
                "changed_files": int(p.get("changed_files") or 0),
                "comments_count": int(p.get("comments") or 0),
                "review_comments_count": int(p.get("review_comments") or 0),
                "created_at": created or datetime.now(UTC),
                "closed_at": _dt(p.get("closed_at")),
                "merged_at": _dt(p.get("merged_at")),
                "html_url": p.get("html_url"),
                "updated_at": datetime.now(UTC),
            }
        )
    db.execute(
        pg_insert(PullRequest)
        .values(rows)
        .on_conflict_do_update(
            index_elements=[PullRequest.repository_id, PullRequest.number],
            set_={
                k: v
                for k, v in rows[0].items()
                if k not in {"repository_id", "number", "created_at"}
            },
        )
    )
    return len(rows)


def upsert_issues(
    db: Session,
    repo_id: int,
    issues: list[dict],
    *,
    max_comments_per_issue: int = 0,
    comment_fetcher: Any = None,
) -> int:
    if not issues:
        return 0
    rows, label_pairs, comment_rows = [], [], []
    for i in issues:
        created = _ms(i.get("created_at"))
        closed = _ms(i.get("closed_at"))
        is_pr = "pull_request" in i
        rows.append(
            {
                "repository_id": repo_id,
                "number": int(i["number"]),
                "title": (i.get("title") or "")[:1024],
                "body": i.get("body"),
                "state": IssueState(i.get("state") or "open"),
                "is_pull_request": is_pr,
                "author_login": (i.get("user") or {}).get("login"),
                "author_association": i.get("author_association"),
                "assignee_login": (i.get("assignee") or {}).get("login"),
                "comments_count": int(i.get("comments") or 0),
                "created_at": created or datetime.now(UTC),
                "updated_at": _ms(i.get("updated_at")),
                "closed_at": closed,
                "html_url": i.get("html_url"),
            }
        )
        for lbl in i.get("labels") or []:
            label_pairs.append(
                (
                    int(i["number"]),
                    lbl.get("name", ""),
                    lbl.get("color"),
                    lbl.get("description"),
                )
            )
        if max_comments_per_issue and i.get("comments") and not is_pr:
            comment_rows.append((int(i["number"]), int(i["comments"])))

    if not rows:
        return 0

    db.execute(
        pg_insert(Issue)
        .values(rows)
        .on_conflict_do_update(
            index_elements=[Issue.repository_id, Issue.number],
            set_={
                k: rows[0][k]
                for k in (
                    "title",
                    "body",
                    "state",
                    "is_pull_request",
                    "author_login",
                    "author_association",
                    "assignee_login",
                    "comments_count",
                    "updated_at",
                    "closed_at",
                    "html_url",
                )
            },
        )
    )

    # Labels -> dictionary table, then the join table.
    if label_pairs:
        uniq = {name: (colour, desc) for _, name, colour, desc in label_pairs if name}
        db.execute(
            pg_insert(IssueLabel)
            .values(
                [
                    {"name": n, "colour": c, "description": d, "created_at": datetime.now(UTC)}
                    for n, (c, d) in uniq.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=[IssueLabel.name])
        )
        label_ids = dict(db.execute(select(IssueLabel.name, IssueLabel.id)).all())
        issue_ids = dict(
            db.execute(
                select(Issue.number, Issue.id).where(
                    Issue.repository_id == repo_id,
                    Issue.number.in_({n for n, _, _, _ in label_pairs}),
                )
            ).all()
        )
        pairs = [
            {"issue_id": issue_ids[num], "label_id": label_ids[name]}
            for num, name, _, _ in label_pairs
            if name in label_ids and num in issue_ids
        ]
        if pairs:
            db.execute(pg_insert(IssueLabelMap).values(pairs).on_conflict_do_nothing())

    # Comments are expensive (1 API call per issue) so they are opt-in.
    if max_comments_per_issue and comment_rows and comment_fetcher is not None:
        for number, _comment_count in comment_rows[:max_comments_per_issue]:
            issue = db.execute(
                select(Issue).where(Issue.repository_id == repo_id, Issue.number == number)
            ).scalar_one_or_none()
            if issue is None:
                continue
            fetched = comment_fetcher(number)[:max_comments_per_issue]
            if not fetched:
                continue
            db.execute(delete(IssueComment).where(IssueComment.issue_id == issue.id))
            db.execute(
                pg_insert(IssueComment).values(
                    [
                        {
                            "issue_id": issue.id,
                            "author_login": (c.get("user") or {}).get("login"),
                            "body": c.get("body"),
                            "created_at": _ms(c.get("created_at")) or datetime.now(UTC),
                        }
                        for c in fetched
                    ]
                )
            )

    return len(rows)


def upsert_releases(db: Session, repo_id: int, releases: list[dict]) -> int:
    if not releases:
        return 0
    rows = [
        {
            "repository_id": repo_id,
            "tag_name": r.get("tag_name") or "",
            "name": r.get("name"),
            "author_login": (r.get("author") or {}).get("login"),
            "is_draft": bool(r.get("draft")),
            "is_prerelease": bool(r.get("prerelease")),
            "published_at": _dt(r.get("published_at")),
            "html_url": r.get("html_url"),
            "updated_at": datetime.now(UTC),
        }
        for r in releases
        if r.get("tag_name")
    ]
    if not rows:
        return 0
    db.execute(
        pg_insert(Release)
        .values(rows)
        .on_conflict_do_update(
            index_elements=[Release.repository_id, Release.tag_name],
            set_={
                k: rows[0][k]
                for k in (
                    "name",
                    "author_login",
                    "is_draft",
                    "is_prerelease",
                    "published_at",
                    "html_url",
                )
            },
        )
    )
    return len(rows)


# ------------------------------------------------------------------- pipeline
def ingest_repository(
    db: Session,
    url: str,
    *,
    user_id: int | None = None,
    with_commits: bool = True,
    with_issues: bool = True,
    with_prs: bool = True,
    with_releases: bool = True,
    with_contributors: bool = True,
    max_commits: int | None = None,
    max_issues: int | None = None,
    with_comments: int = 0,
    client: GitHubClient | None = None,
) -> RefreshJob:
    """Run the full extract → load for one repository. Returns the job ledger row."""
    ref = parse_repo_url(url)
    gh = client or GitHubClient()
    job = RefreshJob(
        job_type="ingest",
        target=ref.full_name,
        status=JobStatus.running,
        user_id=user_id,
        started_at=datetime.now(UTC),
    )
    db.add(job)
    db.flush()
    total_rows = 0
    calls_before = gh.rate.calls_made

    try:
        meta = gh.get_repository(ref)
        repo = upsert_repository(db, meta)
        repo.sync_status = SyncStatus.running
        db.flush()
        job.repository_id = repo.id
        db.flush()
        total_rows += 1

        if with_commits:
            commits = gh.get_commits(ref, max_items=max_commits or settings.INGEST_MAX_COMMITS)
            total_rows += upsert_commits(db, repo.id, commits)
            if with_contributors:
                total_rows += _upsert_contributors(db, repo.id, commits)

        if with_issues:
            issues = gh.get_issues(ref, max_items=max_issues or settings.INGEST_MAX_ISSUES)
            total_rows += upsert_issues(
                db,
                repo.id,
                issues,
                max_comments_per_issue=with_comments,
                comment_fetcher=lambda n: gh.get_issue_comments(ref, n),
            )
            # /issues returns PRs too — record them properly.
            pr_rows = [i for i in issues if "pull_request" in i]
            if pr_rows and not with_prs:
                total_rows += upsert_issues(db, repo.id, pr_rows)

        if with_prs:
            total_rows += upsert_pull_requests(db, repo.id, gh.get_pull_requests(ref))

        if with_releases:
            total_rows += upsert_releases(db, repo.id, gh.get_releases(ref))

        repo.sync_status = SyncStatus.completed
        repo.last_synced_at = datetime.now(UTC)
        repo.ingested_commits = db.execute(
            select(func.count(Commit.id)).where(Commit.repository_id == repo.id)
        ).scalar_one()
        repo.ingested_issues = db.execute(
            select(func.count(Issue.id)).where(
                Issue.repository_id == repo.id, Issue.is_pull_request.is_(False)
            )
        ).scalar_one()

        job.status = JobStatus.completed
        log.info(
            "ingestion completed",
            extra={"context": {"repo": ref.full_name, "rows": total_rows}},
        )

    except (GitHubError, NotFoundError) as exc:
        job.status = JobStatus.failed
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        repo = db.execute(
            select(Repository).where(Repository.full_name == ref.full_name)
        ).scalar_one_or_none()
        if repo is not None:
            repo.sync_status = SyncStatus.failed
        log.error(
            "ingestion failed", extra={"context": {"repo": ref.full_name, "error": str(exc)}}
        )
        db.flush()
        raise
    finally:
        job.rows_ingested = total_rows
        job.api_calls_made = max(0, gh.rate.calls_made - calls_before)
        job.finished_at = datetime.now(UTC)
        started = job.started_at or job.finished_at
        job.duration_seconds = (job.finished_at - started).total_seconds()
        db.flush()

    return job
