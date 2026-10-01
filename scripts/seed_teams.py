"""Create the demo people, teams and access grants directly in the database.

Bypasses the HTTP layer so it works even when the GitHub API is rate-limited, and so the
demo data is reproducible from a clean database.

Usage:  python scripts/seed_teams.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "backend"), str(REPO_ROOT / "ml"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlalchemy import func, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import (  # noqa: E402
    AccessGrantType, RepoPermission, TeamRole, UserRole,
)
from app.db.session import SessionLocal  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.team import RepositoryAccess, Team, TeamMember  # noqa: E402
from app.models.user import User  # noqa: E402

# username, email, global role, display name
PEOPLE = [
    ("demo", "demo@example.com", UserRole.admin, "Demo Analyst"),
    ("priya", "priya@example.com", UserRole.analyst, "Priya — Project Manager"),
    ("raj.dev", "raj.dev@example.com", UserRole.analyst, "Raj — Backend Engineer"),
    ("ana.reviewer", "ana.reviewer@example.com", UserRole.viewer, "Ana — Code Reviewer"),
    ("mia.analyst", "mia.analyst@example.com", UserRole.analyst, "Mia — Product Analyst"),
]
PASSWORD = "TeamPass123"

# team name -> (description, manager username, members, {repo_full_name: permission})
TEAMS = {
    "Platform Engineering": (
        "Owns the shared CLI tooling and runtime libraries.",
        "priya",
        [("raj.dev", TeamRole.member, None), ("ana.reviewer", TeamRole.viewer, None)],
        {"pallets/click": RepoPermission.write,
         "pallets/flask": RepoPermission.read},
    ),
    "Product Squad": (
        "Builds and maintains the customer-facing storefront.",
        "mia.analyst",
        [("raj.dev", TeamRole.member, None)],
        {"aparnaabi248-git/sm-fashion-new": RepoPermission.admin,
         "encode/httpx": RepoPermission.read},
    ),
}


def slugify(value: str) -> str:
    out = []
    for char in value.lower().strip():
        out.append(char if char.isalnum() else "-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "team"


def main() -> int:
    db = SessionLocal()
    try:
        print("=" * 70)
        print("SEEDING PEOPLE, TEAMS AND ACCESS GRANTS")
        print("=" * 70)

        # ------------------------------------------------------------ people
        print("\n  People")
        users: dict[str, User] = {}
        for username, email, role, full_name in PEOPLE:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalar_one_or_none()
            if user is None:
                user = User(email=email, username=username, full_name=full_name,
                            hashed_password=hash_password(PASSWORD),
                            role=role, is_active=True)
                db.add(user)
                db.flush()
                print(f"    created  {username:<16} {role.value:<8} password={PASSWORD}")
            else:
                user.role = role
                user.hashed_password = hash_password(PASSWORD)
                print(f"    updated  {username:<16} {role.value}")
            users[username] = user

        # ------------------------------------------------------------ teams
        repositories = {
            repo.full_name: repo
            for repo in db.execute(select(Repository)).scalars()
        }
        print(f"\n  Teams ({len(repositories)} repositories known to the platform)")

        for name, (description, manager_username, members, grants) in TEAMS.items():
            slug = slugify(name)
            team = db.execute(select(Team).where(Team.slug == slug)).scalar_one_or_none()
            if team is None:
                team = Team(name=name, slug=slug, description=description,
                            manager_id=users[manager_username].id, is_active=True)
                db.add(team)
                db.flush()
                print(f"\n    team '{name}' (manager: {manager_username})")
            else:
                team.manager_id = users[manager_username].id
                print(f"\n    team '{name}' (existing, manager: {manager_username})")

            def ensure_member(username: str, role: TeamRole, scoped: int | None) -> None:
                existing = db.execute(
                    select(TeamMember).where(
                        (TeamMember.team_id == team.id) & (TeamMember.user_id == users[username].id)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.role = role
                    existing.scoped_repository_id = scoped
                    return
                db.add(TeamMember(
                    team_id=team.id, user_id=users[username].id, role=role,
                    scoped_repository_id=scoped, joined_at=datetime.now(UTC),
                ))

            ensure_member(manager_username, TeamRole.manager, None)
            for username, role, scoped_name in members:
                scoped_id = repositories[scoped_name].id if scoped_name else None
                ensure_member(username, role, scoped_id)
                suffix = f" (scoped to {scoped_name})" if scoped_name else ""
                print(f"      + {username:<16} {role.value}{suffix}")

            for full_name, permission in grants.items():
                repo = repositories.get(full_name)
                if repo is None:
                    print(f"      ! {full_name} is not ingested — skipped")
                    continue
                grant = db.execute(
                    select(RepositoryAccess).where(
                        (RepositoryAccess.repository_id == repo.id)
                        & (RepositoryAccess.team_id == team.id)
                    )
                ).scalar_one_or_none()
                if grant is None:
                    db.add(RepositoryAccess(
                        repository_id=repo.id, team_id=team.id,
                        grant_type=AccessGrantType.team, permission=permission,
                        granted_by_id=users[manager_username].id,
                    ))
                else:
                    grant.permission = permission
                print(f"      -> {full_name:<34} {permission.value}")

        db.commit()

        # --------------------------------------------------------- summary
        print("\n" + "=" * 70)
        print("  RESULTING ACCESS MATRIX")
        print("=" * 70)
        header = f"  {'person':<16}{'team role':<12}{'repositories reachable':<46}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        from app.services import access as A

        for username, _email, _role, _name in PEOPLE:
            user = users[username]
            visible = []
            for repo in repositories.values():
                permission = A.effective_permission(db, user, repo.id)
                if permission is not None:
                    visible.append(f"{repo.full_name}({permission.value[0]})")
            managed = sorted(A.managed_team_ids(db, user))
            roles = ",".join(
                A.team_role_for(db, user, t).value for t in managed
                if A.team_role_for(db, user, t)
            ) or "-"
            shown = ", ".join(visible[:2]) + (f" +{len(visible) - 2}" if len(visible) > 2 else "")
            print(f"  {username:<16}{roles:<12}{shown or '(none)':<46}")

        print(f"\n  Sign in at http://localhost:5173 as  demo / {PASSWORD}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"  seed failed: {type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
