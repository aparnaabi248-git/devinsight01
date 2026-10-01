"""teams and repository access control

Revision ID: 0002_team_access
Revises: 0001_initial
Create Date: 2025-01-02 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_team_access"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

team_role = postgresql.ENUM("manager", "member", "viewer", name="team_role",
                            create_type=False)
access_grant_type = postgresql.ENUM("user", "team", name="access_grant_type",
                                   create_type=False)
repo_permission = postgresql.ENUM("read", "write", "admin", name="repo_permission",
                                  create_type=False)


def upgrade() -> None:
    for enum in (team_role, access_grant_type, repo_permission):
        enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------ teams
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("manager_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_teams_slug"),
    )
    op.create_index("ix_teams_manager_id", "teams", ["manager_id"])
    op.create_index("ix_teams_is_active", "teams", ["is_active"])
    op.create_index("ix_teams_created_at", "teams", ["created_at"])

    # ----------------------------------------------------------- team_members
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", team_role, nullable=False, server_default="member"),
        # A manager can pin a member to a single repository within the team.
        sa.Column("scoped_repository_id", sa.Integer(),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("invited_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_id_user_id"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])
    op.create_index("ix_team_members_scoped_repository_id", "team_members",
                    ["scoped_repository_id"])

    # ------------------------------------------------------- repository_access
    op.create_table(
        "repository_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("team_id", sa.Integer(),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("grant_type", access_grant_type, nullable=False),
        sa.Column("permission", repo_permission, nullable=False, server_default="read"),
        sa.Column("granted_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "user_id", "team_id",
                            name="uq_repo_access_scope"),
        # A grant must address exactly one subject: either a user or a team.
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR "
            "(user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_repository_access_single_subject",
        ),
    )
    op.create_index("ix_repo_access_lookup", "repository_access",
                    ["repository_id", "grant_type"])
    op.create_index("ix_repo_access_user", "repository_access", ["user_id"])
    op.create_index("ix_repo_access_team", "repository_access", ["team_id"])


def downgrade() -> None:
    for table in ("repository_access", "team_members", "teams"):
        op.drop_table(table)
    for enum in (repo_permission, access_grant_type, team_role):
        enum.drop(op.get_bind(), checkfirst=True)
