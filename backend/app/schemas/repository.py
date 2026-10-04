"""Repository, commit, contributor, issue, PR, release and analytics schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.base import ChangeType, IssueCategory, IssueState, PRState, SyncStatus

Granularity = Literal["daily", "weekly", "monthly"]
TrendMetric = Literal["commits", "issues", "prs", "bug_fixes", "releases", "contributors"]


class AnalyzeRepositoryRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=512)
    ingest_history: bool = True
    max_commits: int = Field(3000, ge=50, le=20000)
    max_issues: int = Field(1000, ge=0, le=5000)
    background: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        from app.services.github_client import parse_repo_url

        parse_repo_url(v)  # raises ValidationError for non-github hosts
        return v.strip()


class AnalyzeRepositoryResponse(BaseModel):
    repository_id: int
    full_name: str
    job_id: int | None
    status: SyncStatus
    message: str


class ContributorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    github_login: str
    display_name: str | None = None
    avatar_url: str | None = None
    commits_count: int
    additions: int
    deletions: int
    total_churn: int
    files_touched: int
    issues_opened: int
    prs_opened: int
    prs_merged: int
    reviews_given: int
    first_commit_at: datetime | None = None
    last_commit_at: datetime | None = None


class FileChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    old_path: str | None = None
    change_type: ChangeType
    additions: int
    deletions: int


class CommitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sha: str
    author_login: str | None = None
    author_name: str | None = None
    message: str | None = None
    authored_at: datetime
    additions: int
    deletions: int
    churn: int
    files_changed: int
    is_merge: bool
    is_bugfix: bool


class CommitDetail(CommitOut):
    file_changes: list[FileChangeOut] = Field(default_factory=list)


class IssueLabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    colour: str | None = None


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    body: str | None = None
    state: IssueState
    is_pull_request: bool
    author_login: str | None = None
    author_association: str | None = None
    comments_count: int
    category: IssueCategory | None = None
    category_confidence: float | None = None
    priority: str | None = None
    effort_hours: float | None = None
    resolution_hours: float | None = None
    created_at: datetime
    closed_at: datetime | None = None
    html_url: str | None = None
    labels: list[IssueLabelOut] = Field(default_factory=list)


class PullRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    state: PRState
    merged: bool
    author_login: str | None = None
    merged_by: str | None = None
    additions: int
    deletions: int
    size: int
    changed_files: int
    comments_count: int
    review_comments_count: int
    merge_duration_hours: float | None = None
    created_at: datetime
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    html_url: str | None = None


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_name: str
    name: str | None = None
    author_login: str | None = None
    is_draft: bool
    is_prerelease: bool
    published_at: datetime | None = None
    html_url: str | None = None


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    owner_name: str
    name: str
    description: str | None = None
    html_url: str
    default_branch: str
    language: str | None = None
    stars: int
    forks: int
    watchers: int
    open_issues_count: int
    license_spdx: str | None = None
    is_archived: bool
    sync_status: SyncStatus
    last_synced_at: datetime | None = None
    ingested_commits: int
    ingested_issues: int
    health_score: float = 0.0
    latest_commit_at: datetime | None = None
    created_at: datetime


class LabelCount(BaseModel):
    name: str
    count: int
    open: int


class CategoryCount(BaseModel):
    category: str
    count: int
    pct: float


class RepositoryMetrics(BaseModel):
    """Every metric documented in the architecture spec, computed from real data."""

    total_commits: int
    total_contributors: int
    open_issues: int
    closed_issues: int
    total_pull_requests: int
    merged_pull_requests: int
    open_pull_requests: int
    average_pr_size: float
    average_pr_size_files: float
    average_issue_resolution_hours: float | None
    median_issue_resolution_hours: float | None
    commit_frequency_per_week: float
    commit_frequency_per_month: float
    release_frequency_per_month: float
    issue_closure_rate: float
    pr_merge_rate: float
    average_merge_duration_hours: float | None
    total_additions: int
    total_deletions: int
    total_churn: int
    bugfix_commits: int
    bugfix_ratio: float
    commits_last_30d: int
    commits_last_90d: int
    issues_opened_last_30d: int
    active_contributors_last_90d: int
    days_since_last_commit: int | None
    top_contributors: list[ContributorOut] = Field(default_factory=list)
    most_changed_files: list[dict[str, Any]] = Field(default_factory=list)
    label_distribution: list[LabelCount] = Field(default_factory=list)
    category_distribution: list[CategoryCount] = Field(default_factory=list)


class TrendPoint(BaseModel):
    period: str
    commits: int = 0
    issues_opened: int = 0
    issues_closed: int = 0
    prs_opened: int = 0
    prs_merged: int = 0
    releases: int = 0
    bug_fixes: int = 0
    active_contributors: int = 0
    additions: int = 0
    deletions: int = 0


class TrendSeries(BaseModel):
    repository_id: int
    full_name: str
    granularity: Granularity
    metric: TrendMetric
    points: list[TrendPoint]
    totals: dict[str, int] = Field(default_factory=dict)


class HealthIndicator(BaseModel):
    name: str
    score: float
    status: Literal["good", "warning", "critical"]
    detail: str


class RepositoryHealth(BaseModel):
    repository_id: int
    full_name: str
    overall_score: float
    status: Literal["good", "warning", "critical"]
    indicators: list[HealthIndicator]
    defect_risk_summary: dict[str, Any] = Field(default_factory=dict)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    target: str
    status: str
    rows_ingested: int
    api_calls_made: int
    error: str | None = None
    duration_seconds: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
