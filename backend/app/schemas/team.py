"""Team, membership and repository-access schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.base import RepoPermission, TeamRole


def slugify(value: str) -> str:
    out = []
    for char in value.lower().strip():
        out.append(char if char.isalnum() else "-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "team"


# ------------------------------------------------------------------- requests
class TeamCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    manager_username: str | None = Field(
        None, max_length=64, description="Assign the project manager by username"
    )

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return v.strip()


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None


class MemberAdd(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: TeamRole = TeamRole.member
    scoped_repository_id: int | None = Field(
        None, description="Restrict this member's team access to one repository"
    )


class MemberUpdate(BaseModel):
    role: TeamRole | None = None
    scoped_repository_id: int | None = None


class AccessGrantCreate(BaseModel):
    repository_id: int = Field(..., description="The repository being granted")
    permission: RepoPermission = RepoPermission.read
    # A grant targets either a team or a user, never both.
    team_id: int | None = None
    user_id: int | None = None
    expires_at: datetime | None = None
    note: str | None = Field(None, max_length=500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, v: str | None) -> str | None:
        return v.strip() if v else v


# ------------------------------------------------------------------ responses
class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    username: str
    email: str
    role: TeamRole
    scoped_repository_id: int | None = None
    joined_at: datetime | None = None


class AccessGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    repository_name: str | None = None
    grant_type: str
    team_id: int | None = None
    team_name: str | None = None
    user_id: int | None = None
    username: str | None = None
    permission: RepoPermission
    granted_by_id: int | None = None
    expires_at: datetime | None = None
    note: str | None = None
    is_expired: bool = False


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    manager_id: int | None = None
    manager_username: str | None = None
    is_active: bool
    member_count: int = 0
    repository_count: int = 0
    my_role: TeamRole | None = None
    capabilities: list[str] = Field(default_factory=list)
    created_at: datetime


class TeamDetail(TeamOut):
    members: list[TeamMemberOut] = Field(default_factory=list)
    repositories: list[AccessGrantOut] = Field(default_factory=list)


class RepositoryAccessSummary(BaseModel):
    repository_id: int
    full_name: str
    permission: RepoPermission | None
    source: str  # "admin" | "direct" | "team" | "none"
    via_teams: list[str] = Field(default_factory=list)


class MyAccessOut(BaseModel):
    user_id: int
    username: str
    role: str
    manages_teams: int
    repositories: list[RepositoryAccessSummary]
