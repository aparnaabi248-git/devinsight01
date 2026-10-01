"""Model registry — importing this module registers every table on Base.metadata."""

from app.models.activity import Commit, FileChange, PullRequest, Release
from app.models.analytics import AnalyticsSnapshot, RefreshJob
from app.models.issue import Issue, IssueComment, IssueLabel, IssueLabelMap
from app.models.ml import MlRun, ModelVersion, Prediction
from app.models.repository import Contributor, Repository
from app.models.team import RepositoryAccess, Team, TeamMember
from app.models.user import User

__all__ = [
    "User",
    "Repository",
    "Contributor",
    "Commit",
    "FileChange",
    "PullRequest",
    "Release",
    "Issue",
    "IssueLabel",
    "IssueLabelMap",
    "IssueComment",
    "Prediction",
    "ModelVersion",
    "MlRun",
    "AnalyticsSnapshot",
    "RefreshJob",
    "Team",
    "TeamMember",
    "RepositoryAccess",
]
