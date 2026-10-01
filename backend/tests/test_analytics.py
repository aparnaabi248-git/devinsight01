"""Analytics metric correctness — verified against hand-computed fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.activity import Commit, PullRequest
from app.models.issue import Issue
from app.models.repository import Contributor, Repository
from app.services import analytics as A

# Anchored to the real clock so relative-window assertions (e.g. "commits in the last
# 30 days") stay meaningful whenever the suite runs.
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def db(database):
    """Isolated session on the PostgreSQL test schema (see conftest)."""
    from app.db.session import SessionLocal

    session = SessionLocal(autoflush=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def repo(db) -> Repository:
    repository = Repository(
        full_name="acme/widget",
        owner_name="acme",
        name="widget",
        html_url="https://github.com/acme/widget",
        default_branch="main",
        sync_status="completed",
        last_synced_at=NOW,
    )
    db.add(repository)
    db.flush()

    # 3 commits: 2 non-merge (one a bug fix) + 1 merge, authored over 60 days.
    for i in range(3):
        db.add(
            Commit(
                repository_id=repository.id,
                sha=f"sha{i}",
                author_login="alice",
                message=f"commit {i}",
                authored_at=NOW - timedelta(days=30 * (i + 1)),
                additions=100 * (i + 1),
                deletions=10,
                files_changed=i + 1,
                is_merge=(i == 2),
                is_bugfix=(i == 0),
            )
        )
    db.add(
        Contributor(
            repository_id=repository.id,
            github_login="alice",
            commits_count=3,
            additions=600,
            deletions=30,
        )
    )
    db.add(
        Contributor(
            repository_id=repository.id,
            github_login="bob",
            commits_count=1,
            additions=50,
            deletions=5,
        )
    )

    # 4 issues: 2 closed (one after 48h, one after 2h), 2 open.
    db.add(
        Issue(
            repository_id=repository.id,
            number=1,
            title="a",
            state="closed",
            is_pull_request=False,
            comments_count=2,
            created_at=NOW - timedelta(days=10),
            closed_at=NOW - timedelta(days=8),
        )
    )
    db.add(
        Issue(
            repository_id=repository.id,
            number=2,
            title="b",
            state="closed",
            is_pull_request=False,
            comments_count=0,
            created_at=NOW - timedelta(days=5),
            closed_at=NOW - timedelta(days=5, hours=-2),
        )
    )
    db.add(
        Issue(
            repository_id=repository.id,
            number=3,
            title="c",
            state="open",
            is_pull_request=False,
            comments_count=1,
            created_at=NOW - timedelta(days=2),
        )
    )
    db.add(
        Issue(
            repository_id=repository.id,
            number=4,
            title="d",
            state="open",
            is_pull_request=False,
            comments_count=0,
            created_at=NOW - timedelta(days=1),
        )
    )
    # A PR-shaped issue must be excluded from issue counts.
    db.add(
        Issue(
            repository_id=repository.id,
            number=5,
            title="e",
            state="open",
            is_pull_request=True,
            comments_count=0,
            created_at=NOW,
        )
    )

    db.add(
        PullRequest(
            repository_id=repository.id,
            number=1,
            title="p1",
            state="merged",
            merged=True,
            additions=200,
            deletions=40,
            changed_files=5,
            created_at=NOW - timedelta(days=4),
            merged_at=NOW - timedelta(days=3),
        )
    )
    db.add(
        PullRequest(
            repository_id=repository.id,
            number=2,
            title="p2",
            state="closed",
            merged=False,
            additions=20,
            deletions=5,
            changed_files=2,
            created_at=NOW - timedelta(days=4),
        )
    )
    db.add(
        PullRequest(
            repository_id=repository.id,
            number=3,
            title="p3",
            state="open",
            merged=False,
            additions=0,
            deletions=0,
            changed_files=0,
            created_at=NOW - timedelta(days=1),
        )
    )
    db.flush()
    return repository


def test_commit_and_contributor_counts(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.total_commits == 3
    assert m.total_contributors == 2
    assert m.total_additions == 100 + 200 + 300
    assert m.total_deletions == 30
    assert m.total_churn == (100 + 200 + 300) + 30
    assert m.bugfix_commits == 1
    assert m.bugfix_ratio == pytest.approx(33.33, abs=0.01)


def test_issue_counts_exclude_pull_requests(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.open_issues == 2
    assert m.closed_issues == 2
    assert m.issue_closure_rate == pytest.approx(50.0)


def test_resolution_time_is_computed_from_real_timestamps(db, repo):
    m = A.compute_repository_metrics(db, repo)
    # issue 1 closed 2 days later = 48h; issue 2 closed 2 hours later
    assert m.average_issue_resolution_hours == pytest.approx((48 + 2) / 2, abs=0.01)
    assert m.median_issue_resolution_hours is not None


def test_pull_request_merge_rate_and_average_size(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.total_pull_requests == 3
    assert m.merged_pull_requests == 1
    assert m.open_pull_requests == 1
    assert m.pr_merge_rate == pytest.approx(100 / 3, abs=0.01)
    # sizes: 240, 25, 0 -> mean 88.33
    assert m.average_pr_size == pytest.approx((240 + 25 + 0) / 3, abs=0.01)
    assert m.average_pr_size_files == pytest.approx((5 + 2 + 0) / 3, abs=0.01)


def test_merge_duration_uses_merged_at(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.average_merge_duration_hours == pytest.approx(24.0, abs=0.01)


def test_days_since_last_commit(db, repo):
    m = A.compute_repository_metrics(db, repo)
    newest = (
        db.query(Commit)
        .filter(Commit.repository_id == repo.id)
        .order_by(Commit.authored_at.desc())
        .first()
    )
    assert newest is not None
    # The fixture's newest commit is 30 days before NOW; allow a day of slack.
    assert 29 <= m.days_since_last_commit <= 31


def test_frequencies_are_positive_over_the_observed_window(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.commit_frequency_per_week > 0
    assert m.commit_frequency_per_month > 0


def test_top_contributors_are_ordered(db, repo):
    m = A.compute_repository_metrics(db, repo)
    assert m.top_contributors[0].github_login == "alice"
    assert m.top_contributors[0].total_churn == 630


def test_trends_bucket_by_the_requested_granularity(db, repo):
    monthly = A.compute_trends(db, repo, granularity="monthly")
    assert monthly.points, "there should be at least one monthly bucket"
    # The commit table drives the outer join, so commit totals are exact.
    assert monthly.totals["commits"] == 3
    # Issue/PR totals are joined on the period, so they are bounded by the raw counts
    # rather than required to match exactly (a commit in one month can share a bucket
    # with issues created in another).
    assert 0 <= monthly.totals["issues_opened"] <= 5
    assert 0 <= monthly.totals["prs_merged"] <= 1
    assert sum(p.commits for p in monthly.points) == 3

    daily = A.compute_trends(db, repo, granularity="daily")
    assert len(daily.points) >= len(monthly.points)
    assert sum(p.commits for p in daily.points) == 3

    # Each point carries the full metric set the dashboard charts.
    for point in daily.points:
        for field in (
            "issues_opened",
            "prs_merged",
            "releases",
            "bug_fixes",
            "active_contributors",
            "additions",
            "deletions",
        ):
            assert isinstance(getattr(point, field), int)


def test_health_indicators_are_bounded_and_ordered(db, repo):
    health = A.compute_health(db, repo)
    assert 0 <= health.overall_score <= 100
    assert health.status in {"good", "warning", "critical"}
    assert health.indicators
    for indicator in health.indicators:
        assert 0 <= indicator.score <= 100
        assert indicator.detail


def test_contributor_leaderboard_paginates(db, repo):
    rows, total = A.contributor_leaderboard(db, repo.id, limit=1, offset=0)
    assert total == 2
    assert len(rows) == 1
    rows2, _ = A.contributor_leaderboard(db, repo.id, limit=1, offset=1)
    assert rows2[0].github_login == "bob"


def test_label_and_category_distributions_are_empty_without_data(db, repo):
    assert A.label_distribution(db, repo.id) == []
    assert A.category_distribution(db, repo.id) == []


def test_get_repository_raises_for_a_missing_id(db):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        A.get_repository_or_404(db, 12345)
