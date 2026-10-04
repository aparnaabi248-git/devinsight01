"""repository latest_commit_at

Revision ID: 0003_latest_commit_at
Revises: 0002_team_access
Create Date: 2026-10-04 00:00:00

Why: the repository list exposed ``ingested_commits`` but nothing about *when* a
repository was last active. Because seeding caps ingestion at 900 commits per
repository, every OSS repository tied at 900 and the frontend's default-repository
heuristic had no way to break the tie meaningfully - it landed on
``encode/httpx``, whose history ends six months before today, so every dashboard
tile read zero. Storing the newest ingested commit timestamp makes "most active
repository" answerable from the list alone.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_latest_commit_at"
down_revision = "0002_team_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("latest_commit_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index for the default-view ordering. Partial: only repositories that have
    # ingested history can be "most active".
    op.create_index(
        "ix_repositories_latest_commit_at",
        "repositories",
        ["latest_commit_at"],
        postgresql_where=sa.text("latest_commit_at IS NOT NULL"),
    )

    # Backfill from the real ingested history rather than leaving every existing row
    # NULL, which would make every repository tie on the new column.
    op.execute(
        """
        UPDATE repositories AS r
        SET latest_commit_at = sub.max_authored_at
        FROM (
            SELECT repository_id, MAX(authored_at) AS max_authored_at
            FROM commits
            GROUP BY repository_id
        ) AS sub
        WHERE sub.repository_id = r.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_repositories_latest_commit_at", table_name="repositories")
    op.drop_column("repositories", "latest_commit_at")