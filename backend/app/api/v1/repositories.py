"""Repository, analytics and ingestion routes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, status
from sqlalchemy import ColumnElement, func, select

from app.api.deps import AuthenticatedUser, DbSession, RepoRead, RequireAnalyst
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.base import IssueState, JobStatus, SyncStatus
from app.db.session import SessionLocal
from app.models.activity import Commit, PullRequest, Release
from app.models.analytics import RefreshJob
from app.models.issue import Issue
from app.models.repository import Contributor, Repository
from app.schemas.common import Page
from app.schemas.repository import (
    AnalyzeRepositoryRequest,
    AnalyzeRepositoryResponse,
    CommitDetail,
    CommitOut,
    ContributorOut,
    Granularity,
    IssueOut,
    JobOut,
    PullRequestOut,
    ReleaseOut,
    RepositoryHealth,
    RepositoryMetrics,
    RepositoryOut,
    TrendMetric,
    TrendSeries,
)
from app.services import access as Access
from app.services import analytics as A
from app.services.ingestion import ingest_repository

log = get_logger("api.repositories")
router = APIRouter(tags=["repositories"])


def _background_ingest(repo_url: str, user_id: int | None, max_commits: int, max_issues: int):
    """Run ingestion on a worker thread with its own session (never reuse the request one)."""
    db = SessionLocal()
    try:
        job = ingest_repository(
            db, repo_url, user_id=user_id, max_commits=max_commits, max_issues=max_issues
        )
        db.commit()
        log.info(
            "background ingestion finished",
            extra={"context": {"repo": repo_url, "rows": job.rows_ingested}},
        )
    except Exception as exc:
        db.rollback()
        log.error(
            "background ingestion error",
            extra={"context": {"repo": repo_url, "error": str(exc)}},
        )
        # The rollback above discards the `failed` status the ingestion service set, which
        # would leave the repository stuck on `pending` forever. Record the outcome in a
        # fresh transaction so the UI shows the real state and the reason.
        _record_failure(repo_url, user_id, exc)
    finally:
        db.close()


def _record_failure(repo_url: str, user_id: int | None, exc: Exception) -> None:
    """Persist a failed ingestion outcome after the working transaction was rolled back."""
    session = SessionLocal()
    try:
        repository = session.execute(
            select(Repository).where(Repository.full_name == repo_url)
        ).scalar_one_or_none()
        if repository is not None:
            repository.sync_status = SyncStatus.failed

        job = RefreshJob(
            repository_id=repository.id if repository is not None else None,
            user_id=user_id,
            job_type="ingest",
            target=repo_url,
            status=JobStatus.failed,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(job)
        session.commit()
    except Exception as log_exc:  # never let bookkeeping mask the original failure
        session.rollback()
        log.error(
            "could not record ingestion failure",
            extra={"context": {"repo": repo_url, "error": str(log_exc)}},
        )
    finally:
        session.close()


@router.post(
    "/repositories/analyze",
    response_model=AnalyzeRepositoryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def analyze_repository(
    payload: AnalyzeRepositoryRequest,
    db: DbSession,
    background: BackgroundTasks,
    user: RequireAnalyst,
) -> AnalyzeRepositoryResponse:
    """Register a repository and start ingestion. Idempotent per (owner, name)."""
    from app.services.github_client import parse_repo_url

    ref = parse_repo_url(payload.url)
    existing = db.execute(
        select(Repository).where(Repository.full_name == ref.full_name)
    ).scalar_one_or_none()

    if existing is not None:
        # A completed sync within the last hour needs no new work.
        recent = (
            existing.last_synced_at is not None
            and (datetime.now(UTC) - existing.last_synced_at).total_seconds() < 3600
            and existing.sync_status == SyncStatus.completed
        )
        if recent:
            return AnalyzeRepositoryResponse(
                repository_id=existing.id,
                full_name=existing.full_name,
                job_id=None,
                status=existing.sync_status,
                message="already ingested within the last hour — returning cached results",
            )
        existing.sync_status = SyncStatus.pending
        repo, created = existing, False
        # Whoever submits the link owns the project. An unowned repository (imported by
        # the ETL scripts, say) is claimed on first submission, so a user who enters a
        # link always ends up able to see what they added.
        if existing.owner_id is None:
            existing.owner_id = user.id
    else:
        repo = Repository(
            full_name=ref.full_name,
            owner_name=ref.owner,
            name=ref.name,
            html_url=f"https://github.com/{ref.full_name}",
            clone_url=f"https://github.com/{ref.full_name}.git",
            sync_status=SyncStatus.pending,
            owner_id=user.id,
        )
        db.add(repo)
        created = True
    db.flush()

    job_id: int | None = None
    if payload.ingest_history:
        if payload.background:
            background.add_task(
                _background_ingest,
                ref.full_name,
                user.id,
                payload.max_commits,
                payload.max_issues,
            )
        else:
            job = ingest_repository(
                db,
                ref.full_name,
                user_id=user.id,
                max_commits=payload.max_commits,
                max_issues=payload.max_issues,
            )
            job_id = job.id
            db.commit()
            return AnalyzeRepositoryResponse(
                repository_id=repo.id,
                full_name=repo.full_name,
                job_id=job.id,
                status=repo.sync_status,
                message=f"ingested {job.rows_ingested} rows in {job.api_calls_made} API calls",
            )
    db.commit()

    return AnalyzeRepositoryResponse(
        repository_id=repo.id,
        full_name=repo.full_name,
        job_id=job_id,
        status=repo.sync_status,
        message=(
            "repository registered; ingestion running in the background — "
            "poll GET /api/jobs or refresh the repository"
            if payload.ingest_history
            else (
                "repository registered without history ingestion"
                if created
                else "repository refreshed"
            )
        ),
    )


@router.get("/repositories", response_model=Page[RepositoryOut])
def list_repositories(
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=120),
    language: str | None = Query(None, max_length=40),
    sync_status: SyncStatus | None = None,
    sort: str = Query("created_at", pattern="^(created_at|stars|name|last_synced_at)$"),
) -> Page[RepositoryOut]:
    filters: list[ColumnElement[bool]] = []
    if search:
        filters.append(Repository.full_name.ilike(f"%{search}%"))
    if language:
        filters.append(Repository.language == language)
    if sync_status:
        filters.append(Repository.sync_status == sync_status)

    # Restrict the listing to the repositories this caller may read. The count is taken
    # from the same scoped statement so the total can never leak the hidden rows.
    stmt = Access.apply_repository_scope(select(Repository).where(*filters), db, user)
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows: Sequence[Any] = (
        db.execute(
            stmt.order_by(getattr(Repository, sort).desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([RepositoryOut.model_validate(r) for r in rows], total, page, page_size)


@router.get("/repositories/{repo_id}", response_model=RepositoryOut)
def get_repository(repo_id: RepoRead, db: DbSession, user: AuthenticatedUser) -> RepositoryOut:
    return RepositoryOut.model_validate(A.get_repository_or_404(db, repo_id))


@router.get("/repositories/{repo_id}/analytics", response_model=RepositoryMetrics)
def repository_analytics(
    repo_id: RepoRead, db: DbSession, user: AuthenticatedUser
) -> RepositoryMetrics:
    repo = A.get_repository_or_404(db, repo_id)
    metrics = A.compute_repository_metrics(db, repo)
    # The response schema already declares these three fields; the service leaves them
    # empty so the distribution queries stay out of the main aggregation.
    return metrics.model_copy(
        update={
            "label_distribution": A.label_distribution(db, repo_id),
            "category_distribution": A.category_distribution(db, repo_id),
        }
    )


@router.get("/repositories/{repo_id}/health", response_model=RepositoryHealth)
def repository_health(
    repo_id: RepoRead, db: DbSession, user: AuthenticatedUser
) -> RepositoryHealth:
    return A.compute_health(db, A.get_repository_or_404(db, repo_id))


@router.get("/repositories/{repo_id}/trends", response_model=TrendSeries)
def repository_trends(
    repo_id: RepoRead,
    db: DbSession,
    user: AuthenticatedUser,
    granularity: Granularity = Query("daily"),
    metric: TrendMetric = Query("commits"),
    limit: int = Query(365, ge=1, le=2000),
) -> TrendSeries:
    repo = A.get_repository_or_404(db, repo_id)
    return A.compute_trends(db, repo, granularity=granularity, metric=metric, limit=limit)


@router.get("/repositories/{repo_id}/contributors", response_model=Page[ContributorOut])
def repository_contributors(
    repo_id: RepoRead,
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> Page[ContributorOut]:
    A.get_repository_or_404(db, repo_id)
    rows, total = A.contributor_leaderboard(
        db, repo_id, limit=page_size, offset=(page - 1) * page_size
    )
    return Page.build([ContributorOut.model_validate(r) for r in rows], total, page, page_size)


@router.get("/repositories/{repo_id}/commits", response_model=Page[CommitOut])
def repository_commits(
    repo_id: RepoRead,
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    author: str | None = None,
    is_bugfix: bool | None = None,
) -> Page[CommitOut]:
    A.get_repository_or_404(db, repo_id)
    filters = [Commit.repository_id == repo_id]
    if author:
        filters.append(Commit.author_login == author)
    if is_bugfix is not None:
        filters.append(Commit.is_bugfix == is_bugfix)

    total = db.execute(select(func.count(Commit.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Commit)
            .where(*filters)
            .order_by(Commit.authored_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([CommitOut.model_validate(c) for c in rows], total, page, page_size)


@router.get("/repositories/{repo_id}/commits/{sha}", response_model=CommitDetail)
def commit_detail(
    repo_id: int, sha: str, db: DbSession, user: AuthenticatedUser
) -> CommitDetail:
    commit = db.execute(
        select(Commit).where(Commit.repository_id == repo_id, Commit.sha == sha[:40])
    ).scalar_one_or_none()
    if commit is None:
        raise NotFoundError(f"commit {sha} not found in repository {repo_id}")
    return CommitDetail.model_validate(commit)


@router.get("/repositories/{repo_id}/issues", response_model=Page[IssueOut])
def repository_issues(
    repo_id: RepoRead,
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: IssueState | None = None,
    category: str | None = None,
    include_pull_requests: bool = False,
    search: str | None = Query(None, max_length=200),
) -> Page[IssueOut]:
    A.get_repository_or_404(db, repo_id)
    filters = [Issue.repository_id == repo_id]
    if not include_pull_requests:
        filters.append(Issue.is_pull_request.is_(False))
    if state:
        filters.append(Issue.state == state)
    if category:
        filters.append(Issue.category == category.upper())
    if search:
        filters.append(Issue.title.ilike(f"%{search}%"))

    total = db.execute(select(func.count(Issue.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Issue)
            .where(*filters)
            .order_by(Issue.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([IssueOut.model_validate(i) for i in rows], total, page, page_size)


@router.get("/repositories/{repo_id}/pull-requests", response_model=Page[PullRequestOut])
def repository_pull_requests(
    repo_id: RepoRead,
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    merged: bool | None = None,
) -> Page[PullRequestOut]:
    A.get_repository_or_404(db, repo_id)
    filters = [PullRequest.repository_id == repo_id]
    if merged is not None:
        filters.append(PullRequest.merged == merged)

    total = db.execute(select(func.count(PullRequest.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(PullRequest)
            .where(*filters)
            .order_by(PullRequest.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    return Page.build([PullRequestOut.model_validate(p) for p in rows], total, page, page_size)


@router.get("/repositories/{repo_id}/releases", response_model=list[ReleaseOut])
def repository_releases(
    repo_id: RepoRead, db: DbSession, user: AuthenticatedUser
) -> list[ReleaseOut]:
    A.get_repository_or_404(db, repo_id)
    rows = (
        db.execute(
            select(Release)
            .where(Release.repository_id == repo_id)
            .order_by(Release.published_at.desc().nullslast())
        )
        .scalars()
        .all()
    )
    return [ReleaseOut.model_validate(r) for r in rows]


@router.get("/repositories/{repo_id}/contributors/{login}", response_model=ContributorOut)
def contributor_detail(
    repo_id: int, login: str, db: DbSession, user: AuthenticatedUser
) -> ContributorOut:
    contributor = db.execute(
        select(Contributor).where(
            Contributor.repository_id == repo_id, Contributor.github_login == login
        )
    ).scalar_one_or_none()
    if contributor is None:
        raise NotFoundError(f"contributor '{login}' not found in repository {repo_id}")
    return ContributorOut.model_validate(contributor)


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_status(job_id: int, db: DbSession, user: AuthenticatedUser) -> JobOut:
    job = db.get(RefreshJob, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    data = JobOut.model_validate(job)
    data.status = job.status.value
    return data


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    db: DbSession,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    job_status_filter: JobStatus | None = Query(None, alias="status"),
) -> Page[JobOut]:
    filters = [RefreshJob.status == job_status_filter] if job_status_filter else []
    total = db.execute(select(func.count(RefreshJob.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(RefreshJob)
            .where(*filters)
            .order_by(RefreshJob.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
    )
    items = []
    for job in rows:
        item = JobOut.model_validate(job)
        item.status = job.status.value
        items.append(item)
    return Page.build(items, total, page, page_size)
