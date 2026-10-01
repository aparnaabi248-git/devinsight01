"""initial schema — DevInsight

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

user_role = postgresql.ENUM("admin", "analyst", "viewer", name="user_role", create_type=False)
sync_status = postgresql.ENUM("pending", "running", "completed", "failed",
                              name="sync_status", create_type=False)
job_status = postgresql.ENUM("queued", "running", "completed", "failed",
                             name="job_status", create_type=False)
issue_state = postgresql.ENUM("open", "closed", name="issue_state", create_type=False)
pr_state = postgresql.ENUM("open", "closed", "merged", name="pr_state", create_type=False)
change_type = postgresql.ENUM("added", "modified", "deleted", "renamed",
                              name="change_type", create_type=False)
issue_category = postgresql.ENUM("BUG", "FEATURE_REQUEST", "DOCUMENTATION", "QUESTION",
                                  "ENHANCEMENT", "OTHER", name="issue_category",
                                  create_type=False)
risk_level = postgresql.ENUM("LOW", "MEDIUM", "HIGH", name="risk_level", create_type=False)
priority_level = postgresql.ENUM("CRITICAL", "HIGH", "MEDIUM", "LOW",
                                 name="priority_level", create_type=False)


def upgrade() -> None:
    # ------------------------------------------------------------- enums first
    for enum in (user_role, sync_status, job_status, issue_state, pr_state,
                 change_type, issue_category, risk_level, priority_level):
        enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------ users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("full_name", sa.String(160)),
        sa.Column("hashed_password", sa.String(128), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("github_login", sa.String(64)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_github_login", "users", ["github_login"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # ----------------------------------------------------------- repositories
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("owner_name", sa.String(120), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("html_url", sa.String(512), nullable=False),
        sa.Column("clone_url", sa.String(512)),
        sa.Column("default_branch", sa.String(120), nullable=False, server_default="main"),
        sa.Column("language", sa.String(80)),
        sa.Column("stars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watchers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_issues_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_kb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("license_spdx", sa.String(64)),
        sa.Column("is_fork", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_status", sync_status, nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_commits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ingested_issues", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_name", "name", name="uq_repositories_owner_name"),
    )
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"])
    op.create_index("ix_repositories_sync_state", "repositories", ["sync_status"])
    op.create_index("ix_repositories_language", "repositories", ["language"])
    op.create_index("ix_repositories_owner_id", "repositories", ["owner_id"])
    op.create_index("ix_repositories_created_at", "repositories", ["created_at"])

    # ------------------------------------------------------------ contributors
    op.create_table(
        "contributors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("github_login", sa.String(120), nullable=False),
        sa.Column("github_id", sa.BigInteger()),
        sa.Column("display_name", sa.String(200)),
        sa.Column("avatar_url", sa.String(512)),
        sa.Column("commits_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("additions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("files_touched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issues_opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issues_commented", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prs_opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prs_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviews_given", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_commit_at", sa.DateTime(timezone=True)),
        sa.Column("last_commit_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "github_login",
                            name="uq_contributors_repository_id_github_login"),
    )
    op.create_index("ix_contributors_github_login", "contributors", ["github_login"])
    op.create_index("ix_contributors_repository_id", "contributors", ["repository_id"])
    op.create_index("ix_contributors_repo_commits", "contributors",
                    ["repository_id", "commits_count"])
    op.create_index("ix_contributors_last_commit_at", "contributors", ["last_commit_at"])
    op.create_index("ix_contributors_created_at", "contributors", ["created_at"])

    # ----------------------------------------------------------------- commits
    op.create_table(
        "commits",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("parent_sha", sa.String(40)),
        sa.Column("author_login", sa.String(120)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("author_email", sa.String(320)),
        sa.Column("message", sa.Text()),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_merge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_bugfix", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "sha", name="uq_commits_repository_id_sha"),
    )
    op.create_index("ix_commits_repo_authored", "commits",
                    ["repository_id", "authored_at"])
    op.create_index("ix_commits_repo_author", "commits", ["repository_id", "author_login"])
    op.create_index("ix_commits_author_login", "commits", ["author_login"])
    op.create_index("ix_commits_is_bugfix", "commits", ["is_bugfix"])
    op.create_index("ix_commits_created_at", "commits", ["created_at"])

    # ------------------------------------------------------------ file_changes
    op.create_table(
        "file_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("commit_id", sa.BigInteger(), sa.ForeignKey("commits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("old_path", sa.String(512)),
        sa.Column("change_type", change_type, nullable=False, server_default="modified"),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("similarity", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_file_changes_commit", "file_changes", ["commit_id"])
    op.create_index("ix_file_changes_path", "file_changes", ["path"])

    # ---------------------------------------------------------- pull_requests
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("state", pr_state, nullable=False, server_default="open"),
        sa.Column("merged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("author_login", sa.String(120)),
        sa.Column("merged_by", sa.String(120)),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_comments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("merged_at", sa.DateTime(timezone=True)),
        sa.Column("html_url", sa.String(512)),
                sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "number",
                            name="uq_pull_requests_repository_id_number"),
    )
    op.create_index("ix_pr_repo_created", "pull_requests", ["repository_id", "created_at"])
    op.create_index("ix_pr_repo_merged", "pull_requests", ["repository_id", "merged"])
    op.create_index("ix_pr_merged", "pull_requests", ["merged"])
    op.create_index("ix_pr_author_login", "pull_requests", ["author_login"])
    op.create_index("ix_pull_requests_created_at", "pull_requests", ["created_at"])

    # ----------------------------------------------------------------- issues
    op.create_table(
        "issues",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("state", issue_state, nullable=False, server_default="open"),
        sa.Column("is_pull_request", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("author_login", sa.String(120)),
        sa.Column("author_association", sa.String(40)),
        sa.Column("assignee_login", sa.String(120)),
        sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("category", issue_category, nullable=True),
        sa.Column("category_confidence", sa.Float()),
        sa.Column("priority", sa.String(16)),
        sa.Column("effort_hours", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("html_url", sa.String(512)),
        sa.UniqueConstraint("repository_id", "number",
                            name="uq_issues_repository_id_number"),
    )
    op.create_index("ix_issues_repo_created", "issues", ["repository_id", "created_at"])
    op.create_index("ix_issues_repo_state", "issues", ["repository_id", "state"])
    op.create_index("ix_issues_repo_category", "issues", ["repository_id", "category"])
    op.create_index("ix_issues_is_pull_request", "issues", ["is_pull_request"])
    op.create_index("ix_issues_author_login", "issues", ["author_login"])
    # Full-text search over issue title + body.
    op.execute("CREATE INDEX ix_issues_title_fts ON issues "
               "USING gin (to_tsvector('english', title || ' ' || coalesce(body, '')))")

    # ------------------------------------------------------------ issue labels
    op.create_table(
        "issue_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("colour", sa.String(16)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_issue_labels_name"),
    )
    op.create_index("ix_issue_labels_name", "issue_labels", ["name"])

    op.create_table(
        "issue_label_map",
        sa.Column("issue_id", sa.BigInteger(),
                  sa.ForeignKey("issues.id", ondelete="CASCADE"),
                  nullable=False, primary_key=True),
        sa.Column("label_id", sa.Integer(),
                  sa.ForeignKey("issue_labels.id", ondelete="CASCADE"),
                  nullable=False, primary_key=True),
    )
    op.create_index("ix_issue_label_map_label", "issue_label_map", ["label_id"])

    # --------------------------------------------------------- issue_comments
    op.create_table(
        "issue_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("issue_id", sa.BigInteger(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_login", sa.String(120)),
        sa.Column("body", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_issue_comments_issue", "issue_comments", ["issue_id"])

    # --------------------------------------------------------------- releases
    op.create_table(
        "releases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_name", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("author_login", sa.String(120)),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_prerelease", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("html_url", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "tag_name",
                            name="uq_releases_repository_id_tag_name"),
    )
    op.create_index("ix_releases_repo_published", "releases",
                    ["repository_id", "published_at"])
    op.create_index("ix_releases_published_at", "releases", ["published_at"])

    # ---------------------------------------------------------- model registry
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(120), nullable=False),
        sa.Column("mlflow_run_id", sa.String(64)),
        sa.Column("mlflow_uri", sa.String(512)),
        sa.Column("artifact_uri", sa.String(512)),
        sa.Column("data_hash", sa.String(64)),
        sa.Column("dataset_version", sa.String(120)),
        sa.Column("n_train", sa.Integer()),
        sa.Column("n_test", sa.Integer()),
        sa.Column("features", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("hyperparameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("comparison", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_description", sa.Text()),
        sa.Column("limitations", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trained_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_model_versions_name_version"),
    )
    op.create_index("ix_model_versions_name", "model_versions", ["name"])
    op.create_index("ix_model_versions_mlflow_run_id", "model_versions", ["mlflow_run_id"])
    op.create_index("ix_model_versions_name_active", "model_versions", ["name", "is_active"])

    # ------------------------------------------------------------- predictions
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repository_id", sa.Integer(),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("model_version_id", sa.Integer(),
                  sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_ref", sa.String(255)),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("model_version_tag", sa.String(80), nullable=False),
        sa.Column("prediction", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float()),
        sa.Column("probability_json", sa.JSON()),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("explanation", sa.JSON()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_predictions_repo_created", "predictions",
                    ["repository_id", "created_at"])
    op.create_index("ix_predictions_target", "predictions", ["target_type"])
    op.create_index("ix_predictions_model_version", "predictions", ["model_version_id"])
    op.create_index("ix_predictions_input_hash", "predictions", ["input_hash"])
    op.create_index("ix_predictions_user_id", "predictions", ["user_id"])
    op.create_index("ix_predictions_created_at", "predictions", ["created_at"])

    op.create_table(
        "ml_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("algorithm", sa.String(120), nullable=False),
        sa.Column("model_version_id", sa.Integer(),
                  sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mlflow_run_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default="success"),
        sa.Column("data_hash", sa.String(64)),
        sa.Column("dataset_rows", sa.Integer()),
        sa.Column("train_seconds", sa.Float()),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cv_scores", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ml_runs_name_created", "ml_runs", ["name", "created_at"])
    op.create_index("ix_ml_runs_mlflow_run_id", "ml_runs", ["mlflow_run_id"])

    # --------------------------------------------------- analytics + job ledger
    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False, server_default="daily"),
        sa.Column("commits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issues_opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issues_closed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prs_opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prs_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("releases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bug_fixes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_contributors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "snapshot_date", "granularity",
                            name="uq_analytics_snapshots_repo_date_gran"),
    )
    op.create_index("ix_snapshots_repo_gran", "analytics_snapshots",
                    ["repository_id", "granularity", "snapshot_date"])

    op.create_table(
        "refresh_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_type", sa.String(40), nullable=False, server_default="ingest"),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="queued"),
        sa.Column("rows_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("api_calls_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refresh_jobs_status", "refresh_jobs", ["status", "created_at"])
    op.create_index("ix_refresh_jobs_repository_id", "refresh_jobs", ["repository_id"])


def downgrade() -> None:
    for table in ("refresh_jobs", "analytics_snapshots", "ml_runs", "predictions",
                  "model_versions", "releases", "issue_comments", "issue_label_map",
                  "issue_labels", "issues", "pull_requests", "file_changes", "commits",
                  "contributors", "repositories", "users"):
        op.drop_table(table)
    for enum in (priority_level, risk_level, issue_category, change_type, pr_state,
                 issue_state, job_status, sync_status, user_role):
        enum.drop(op.get_bind(), checkfirst=True)
