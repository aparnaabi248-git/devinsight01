"""Analytics layer: all repository/developer/trend metrics computed from real rows.

Heavy aggregation is pushed to PostgreSQL (window functions, FILTER, date_trunc) so the
API never pulls raw fact tables into Python.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.activity import Commit, FileChange, PullRequest, Release
from app.models.issue import Issue, IssueLabel, IssueLabelMap
from app.models.ml import Prediction
from app.models.repository import Contributor, Repository
from app.schemas.repository import (
    ContributorOut,
    Granularity,
    HealthIndicator,
    RepositoryHealth,
    RepositoryMetrics,
    TrendMetric,
    TrendPoint,
    TrendSeries,
)

# Postgres date_trunc units keyed by our API granularity.
TRUNC_UNIT = {"daily": "day", "weekly": "week", "monthly": "month"}


def _pct(numerator: float, denominator: float) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _safe(value: Any, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def get_repository_or_404(db: Session, repo_id: int) -> Repository:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise NotFoundError(
            f"repository {repo_id} does not exist", code="repository_not_found"
        )
    return repo


# --------------------------------------------------------------------- metrics
def compute_repository_metrics(db: Session, repo: Repository) -> RepositoryMetrics:
    r_id = repo.id

    commit_stats = db.execute(
        select(
            func.count(Commit.id),
            func.coalesce(func.sum(Commit.additions), 0),
            func.coalesce(func.sum(Commit.deletions), 0),
            func.coalesce(func.sum(case((Commit.is_bugfix.is_(True), 1), else_=0)), 0),
            func.max(Commit.authored_at),
            func.count(distinct(Commit.author_login)),
        ).where(Commit.repository_id == r_id)
    ).one()

    contributor_count = db.execute(
        select(func.count(Contributor.id)).where(Contributor.repository_id == r_id)
    ).scalar_one()

    # `resolution_hours` is a Python @property, so it cannot be passed to func.avg().
    # Recompute the average resolution time in SQL from the two real timestamps.
    resolution_expr = func.extract("epoch", Issue.closed_at - Issue.created_at) / 3600.0

    issue_stats = db.execute(
        select(
            func.coalesce(func.sum(case((Issue.state == "open", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Issue.state == "closed", 1), else_=0)), 0),
            func.avg(resolution_expr),
        ).where(and_(Issue.repository_id == r_id, Issue.is_pull_request.is_(False)))
    ).one()

    # Median resolution time via ordered offset — accurate and index-friendly.
    median_hours = None
    if issue_stats[1]:
        median_hours = db.execute(
            select(resolution_expr.label("resolution_hours"))
            .where(
                and_(
                    Issue.repository_id == r_id,
                    Issue.is_pull_request.is_(False),
                    Issue.closed_at.isnot(None),
                )
            )
            .order_by(resolution_expr)
            .offset(issue_stats[1] // 2)
            .limit(1)
        ).scalar_one_or_none()

    pr_stats = db.execute(
        select(
            func.count(PullRequest.id),
            func.coalesce(func.sum(case((PullRequest.merged.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((PullRequest.state == "open", 1), else_=0)), 0),
            func.avg(PullRequest.additions + PullRequest.deletions),
            func.avg(PullRequest.changed_files),
            func.avg(
                func.extract("epoch", PullRequest.merged_at - PullRequest.created_at) / 3600.0
            ),
        ).where(PullRequest.repository_id == r_id)
    ).one()

    release_count, last_release = db.execute(
        select(func.count(Release.id), func.max(Release.published_at)).where(
            Release.repository_id == r_id
        )
    ).one()

    total_commits, additions, deletions, bugfixes, last_commit, distinct_authors = commit_stats

    # --- activity windows -------------------------------------------------
    now = datetime.now(UTC)

    def _commits_since(days: int) -> int:
        return db.execute(
            select(func.count(Commit.id)).where(
                and_(
                    Commit.repository_id == r_id,
                    Commit.authored_at >= now - timedelta(days=days),
                )
            )
        ).scalar_one()

    commits_30, commits_90 = _commits_since(30), _commits_since(90)
    issues_30 = db.execute(
        select(func.count(Issue.id)).where(
            and_(
                Issue.repository_id == r_id,
                Issue.is_pull_request.is_(False),
                Issue.created_at >= now - timedelta(days=30),
            )
        )
    ).scalar_one()
    active_90 = db.execute(
        select(func.count(distinct(Commit.author_login))).where(
            and_(
                Commit.repository_id == r_id,
                Commit.authored_at >= now - timedelta(days=90),
                Commit.author_login.isnot(None),
            )
        )
    ).scalar_one()

    # --- frequencies over the observed history window ---------------------
    window_start = db.execute(
        select(func.min(Commit.authored_at)).where(Commit.repository_id == r_id)
    ).scalar_one()
    release_window_start = db.execute(
        select(func.min(Release.published_at)).where(Release.repository_id == r_id)
    ).scalar_one()

    weeks = months = 0.0
    if window_start and total_commits:
        span_days = max(1.0, (now - window_start).total_seconds() / 86400.0)
        weeks, months = span_days / 7.0, span_days / 30.44

    release_months = 0.0
    if release_window_start and release_count:
        release_months = max(
            1.0, (now - release_window_start).total_seconds() / 86400.0 / 30.44
        )

    top_contributors = [
        ContributorOut.model_validate(c)
        for c in db.execute(
            select(Contributor)
            .where(Contributor.repository_id == r_id)
            .order_by(Contributor.commits_count.desc())
            .limit(10)
        ).scalars()
    ]

    most_changed = [
        {"path": path, "changes": n, "additions": adds, "deletions": dels}
        for path, n, adds, dels in db.execute(
            select(
                FileChange.path,
                func.count(FileChange.id),
                func.coalesce(func.sum(FileChange.additions), 0),
                func.coalesce(func.sum(FileChange.deletions), 0),
            )
            .join(Commit, Commit.id == FileChange.commit_id)
            .where(Commit.repository_id == r_id)
            .group_by(FileChange.path)
            .order_by(func.count(FileChange.id).desc())
            .limit(15)
        ).all()
    ]

    closed_issues = int(issue_stats[1])
    return RepositoryMetrics(
        total_commits=int(total_commits or 0),
        total_contributors=int(contributor_count or 0),
        open_issues=int(issue_stats[0] or 0),
        closed_issues=closed_issues,
        total_pull_requests=int(pr_stats[0] or 0),
        merged_pull_requests=int(pr_stats[1] or 0),
        open_pull_requests=int(pr_stats[2] or 0),
        average_pr_size=round(_safe(pr_stats[3]), 2),
        average_pr_size_files=round(_safe(pr_stats[4]), 2),
        average_issue_resolution_hours=(
            round(float(issue_stats[2]), 2) if issue_stats[2] is not None else None
        ),
        median_issue_resolution_hours=(
            round(float(median_hours), 2) if median_hours is not None else None
        ),
        commit_frequency_per_week=round(total_commits / weeks, 2) if weeks else 0.0,
        commit_frequency_per_month=round(total_commits / months, 2) if months else 0.0,
        release_frequency_per_month=round(release_count / release_months, 2)
        if release_months
        else 0.0,
        issue_closure_rate=_pct(closed_issues, closed_issues + int(issue_stats[0] or 0)),
        pr_merge_rate=_pct(int(pr_stats[1] or 0), int(pr_stats[0] or 0)),
        average_merge_duration_hours=(
            round(float(pr_stats[5]), 2) if pr_stats[5] is not None else None
        ),
        total_additions=int(additions or 0),
        total_deletions=int(deletions or 0),
        total_churn=int(additions or 0) + int(deletions or 0),
        bugfix_commits=int(bugfixes or 0),
        bugfix_ratio=_pct(int(bugfixes or 0), int(total_commits or 0)),
        commits_last_30d=commits_30,
        commits_last_90d=commits_90,
        issues_opened_last_30d=issues_30,
        active_contributors_last_90d=int(active_90 or 0),
        days_since_last_commit=((now - last_commit).days if last_commit else None),
        top_contributors=top_contributors,
        most_changed_files=most_changed,
    )


# ---------------------------------------------------------------------- trends
def compute_trends(
    db: Session,
    repo: Repository,
    # Literal types, not `str`: these land directly in the `TrendSeries` response, and
    # FastAPI validates the query parameter against the same Literals, so an unknown
    # value is rejected at the edge rather than silently defaulting below.
    granularity: Granularity = "daily",
    metric: TrendMetric = "commits",
    limit: int = 365,
) -> TrendSeries:
    unit = TRUNC_UNIT.get(granularity, "day")
    r_id = repo.id

    commit_buckets = (
        select(
            func.date_trunc(unit, Commit.authored_at).label("period"),
            func.count(Commit.id).label("commits"),
            func.coalesce(func.sum(Commit.additions), 0).label("additions"),
            func.coalesce(func.sum(Commit.deletions), 0).label("deletions"),
            func.coalesce(func.sum(case((Commit.is_bugfix.is_(True), 1), else_=0)), 0).label(
                "bug_fixes"
            ),
            func.count(distinct(Commit.author_login)).label("active_contributors"),
        )
        .where(Commit.repository_id == r_id)
        .group_by("period")
        .subquery()
    )

    issue_buckets = (
        select(
            func.date_trunc(unit, Issue.created_at).label("period"),
            func.count(Issue.id).label("issues_opened"),
            func.coalesce(func.sum(case((Issue.closed_at.isnot(None), 1), else_=0)), 0).label(
                "issues_closed"
            ),
        )
        .where(and_(Issue.repository_id == r_id, Issue.is_pull_request.is_(False)))
        .group_by("period")
        .subquery()
    )

    pr_buckets = (
        select(
            func.date_trunc(unit, PullRequest.created_at).label("period"),
            func.count(PullRequest.id).label("prs_opened"),
            func.coalesce(func.sum(case((PullRequest.merged.is_(True), 1), else_=0)), 0).label(
                "prs_merged"
            ),
        )
        .where(PullRequest.repository_id == r_id)
        .group_by("period")
        .subquery()
    )

    release_buckets = (
        select(
            func.date_trunc(unit, Release.published_at).label("period"),
            func.count(Release.id).label("releases"),
        )
        .where(and_(Release.repository_id == r_id, Release.published_at.isnot(None)))
        .group_by("period")
        .subquery()
    )

    merged = (
        select(
            commit_buckets.c.period,
            func.coalesce(commit_buckets.c.commits, 0),
            func.coalesce(commit_buckets.c.additions, 0),
            func.coalesce(commit_buckets.c.deletions, 0),
            func.coalesce(commit_buckets.c.bug_fixes, 0),
            func.coalesce(commit_buckets.c.active_contributors, 0),
            func.coalesce(issue_buckets.c.issues_opened, 0),
            func.coalesce(issue_buckets.c.issues_closed, 0),
            func.coalesce(pr_buckets.c.prs_opened, 0),
            func.coalesce(pr_buckets.c.prs_merged, 0),
            func.coalesce(release_buckets.c.releases, 0),
        )
        .select_from(commit_buckets)
        .outerjoin(issue_buckets, issue_buckets.c.period == commit_buckets.c.period)
        .outerjoin(pr_buckets, pr_buckets.c.period == commit_buckets.c.period)
        .outerjoin(release_buckets, release_buckets.c.period == commit_buckets.c.period)
        .order_by(commit_buckets.c.period)
        .limit(limit)
    )

    points: list[TrendPoint] = []
    totals = {
        "commits": 0,
        "issues_opened": 0,
        "issues_closed": 0,
        "prs_opened": 0,
        "prs_merged": 0,
        "releases": 0,
        "bug_fixes": 0,
        "additions": 0,
        "deletions": 0,
    }

    for row in db.execute(merged).all():
        period = row[0]
        if period is None:
            continue
        p = TrendPoint(
            period=period.date().isoformat() if hasattr(period, "date") else str(period),
            commits=int(row[1]),
            additions=int(row[2]),
            deletions=int(row[3]),
            bug_fixes=int(row[4]),
            active_contributors=int(row[5]),
            issues_opened=int(row[6]),
            issues_closed=int(row[7]),
            prs_opened=int(row[8]),
            prs_merged=int(row[9]),
            releases=int(row[10]),
        )
        points.append(p)
        totals["commits"] += p.commits
        totals["issues_opened"] += p.issues_opened
        totals["issues_closed"] += p.issues_closed
        totals["prs_opened"] += p.prs_opened
        totals["prs_merged"] += p.prs_merged
        totals["releases"] += p.releases
        totals["bug_fixes"] += p.bug_fixes
        totals["additions"] += p.additions
        totals["deletions"] += p.deletions

    return TrendSeries(
        repository_id=repo.id,
        full_name=repo.full_name,
        granularity=granularity,
        metric=metric,
        points=points,
        totals=totals,
    )


# --------------------------------------------------------------------- health
def _indicator(name: str, score: float, good: str, warn: str) -> HealthIndicator:
    score = max(0.0, min(100.0, round(score, 2)))
    status = "good" if score >= 70 else "warning" if score >= 40 else "critical"
    return HealthIndicator(
        name=name,
        score=score,
        status=status,  # type: ignore[arg-type]
        detail=good if status == "good" else warn,
    )


def compute_health(db: Session, repo: Repository) -> RepositoryHealth:
    m = compute_repository_metrics(db, repo)
    indicators: list[HealthIndicator] = []

    if m.days_since_last_commit is None:
        recency = 0.0
    elif m.days_since_last_commit <= 7:
        recency = 100.0
    elif m.days_since_last_commit <= 30:
        recency = 100.0 - (m.days_since_last_commit - 7) * 2.2
    else:
        recency = max(5.0, 65.0 - (m.days_since_last_commit - 30) * 0.6)

    indicators.append(
        _indicator(
            "Development activity",
            recency,
            f"{m.commits_last_30d} commits in the last 30 days.",
            f"No commits for {m.days_since_last_commit} days.",
        )
    )
    indicators.append(
        _indicator(
            "Issue backlog",
            max(0.0, 100.0 - m.open_issues * 0.35),
            f"{m.issue_closure_rate}% of issues are closed.",
            f"{m.open_issues} issues are still open ({m.issue_closure_rate}% closure rate).",
        )
    )
    indicators.append(
        _indicator(
            "Code review flow",
            m.pr_merge_rate,
            f"{m.pr_merge_rate}% of pull requests are merged.",
            f"Only {m.pr_merge_rate}% of pull requests get merged — possible review bottleneck.",
        )
    )
    indicators.append(
        _indicator(
            "Bus factor",
            min(100.0, m.total_contributors * 8.0) if m.total_contributors else 0.0,
            f"{m.total_contributors} contributors — well distributed.",
            f"Only {m.total_contributors} contributors — high bus factor risk.",
        )
    )
    indicators.append(
        _indicator(
            "Defect-fix pressure",
            max(0.0, 100.0 - m.bugfix_ratio * 1.8),
            f"Bug-fix commits are {m.bugfix_ratio}% of all commits.",
            f"Bug-fix commits are {m.bugfix_ratio}% of all commits — elevated defect pressure.",
        )
    )

    overall = round(sum(i.score for i in indicators) / len(indicators), 2)
    status = "good" if overall >= 70 else "warning" if overall >= 40 else "critical"

    r_id = repo.id
    risk_rows = db.execute(
        select(Prediction.prediction, func.count(Prediction.id))
        .where(and_(Prediction.repository_id == r_id, Prediction.target_type == "defect_risk"))
        .group_by(Prediction.prediction)
    ).all()
    # The summary mixes level counts, a nested percentage map and a total, so its value
    # type is genuinely heterogeneous. Compute the counts first rather than mutating the
    # dict while iterating it.
    risk_counts = {str(level): int(count) for level, count in risk_rows}
    total_risk = sum(risk_counts.values()) or 1
    risk_summary: dict[str, Any] = {
        **risk_counts,
        "distribution_pct": {
            level: round(100.0 * count / total_risk, 2) for level, count in risk_counts.items()
        },
        "total_predictions": total_risk,
    }

    return RepositoryHealth(
        repository_id=repo.id,
        full_name=repo.full_name,
        overall_score=overall,
        status=status,  # type: ignore[arg-type]
        indicators=indicators,
        defect_risk_summary=risk_summary,
    )


# ---------------------------------------------------------------- label stats
def label_distribution(db: Session, repo_id: int, limit: int = 25) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": int(count), "open": int(opened)}
        for name, count, opened in db.execute(
            select(
                IssueLabel.name,
                func.count(IssueLabelMap.issue_id),
                func.coalesce(func.sum(case((Issue.state == "open", 1), else_=0)), 0),
            )
            .select_from(IssueLabelMap)
            .join(Issue, Issue.id == IssueLabelMap.issue_id)
            .join(IssueLabel, IssueLabel.id == IssueLabelMap.label_id)
            .where(Issue.repository_id == repo_id)
            .group_by(IssueLabel.name)
            .order_by(func.count(IssueLabelMap.issue_id).desc())
            .limit(limit)
        ).all()
    ]


def category_distribution(db: Session, repo_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Issue.category, func.count(Issue.id))
        .where(and_(Issue.repository_id == repo_id, Issue.category.isnot(None)))
        .group_by(Issue.category)
        .order_by(func.count(Issue.id).desc())
    ).all()
    total = sum(int(c) for _, c in rows) or 1
    return [
        {
            "category": str(cat),
            "count": int(count),
            "pct": round(100.0 * int(count) / total, 2),
        }
        for cat, count in rows
    ]


def contributor_leaderboard(
    db: Session, repo_id: int, limit: int = 25, offset: int = 0
) -> tuple[list[Contributor], int]:
    total = db.execute(
        select(func.count(Contributor.id)).where(Contributor.repository_id == repo_id)
    ).scalar_one()
    rows = (
        db.execute(
            select(Contributor)
            .where(Contributor.repository_id == repo_id)
            .order_by(Contributor.commits_count.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)
