"""Tests for team membership and per-repository access control.

These cover the authorisation rules that decide who can see and modify a repository.
Each test pins one specific rule, and several of them are regression tests for bugs
that actually shipped in this module:

* a team manager was granted `admin` on **every** repository, not just the ones their
  team held, which leaked the whole platform to any project manager;
* a team role could exceed the team's own grant, so a member of a `read` team
  effectively administered the repository;
* `scoped_repository_id` was dropped from membership checks, so pinning a member to one
  repository did nothing.

The suite is deliberately written so that "more access" always fails: a test asserts
what a person *cannot* reach as well as what they can.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.base import AccessGrantType, RepoPermission, TeamRole, UserRole
from app.models.repository import Repository
from app.models.team import RepositoryAccess, Team, TeamMember
from app.models.user import User
from app.services import access as A


# --------------------------------------------------------------------- fixtures
def make_user(db, username: str, role: UserRole = UserRole.analyst) -> User:
    user = User(
        email=f"{username}@example.com",
        username=username,
        full_name=username.title(),
        hashed_password=hash_password("TestPass123"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def make_repo(db, full_name: str, owner: User | None = None) -> Repository:
    owner_name, name = full_name.split("/", 1)
    repo = Repository(
        full_name=full_name,
        owner_name=owner_name,
        name=name,
        owner_id=owner.id if owner else None,
        html_url=f"https://github.com/{full_name}",
        clone_url=f"https://github.com/{full_name}.git",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    return repo


def make_team(db, name: str, manager: User) -> Team:
    slug = name.lower().replace(" ", "-")
    team = Team(name=name, slug=slug, manager_id=manager.id, is_active=True)
    db.add(team)
    db.flush()
    db.add(
        TeamMember(
            team_id=team.id,
            user_id=manager.id,
            role=TeamRole.manager,
        )
    )
    db.flush()
    return team


def add_member(
    db, team: Team, user: User, role: TeamRole, scoped: Repository | None = None
) -> TeamMember:
    member = TeamMember(
        team_id=team.id,
        user_id=user.id,
        role=role,
        scoped_repository_id=scoped.id if scoped else None,
    )
    db.add(member)
    db.flush()
    return member


def grant(
    db,
    repo: Repository,
    team: Team | None = None,
    user: User | None = None,
    permission: RepoPermission = RepoPermission.read,
    expires_at=None,
) -> RepositoryAccess:
    access = RepositoryAccess(
        repository_id=repo.id,
        team_id=team.id if team else None,
        user_id=user.id if user else None,
        grant_type=AccessGrantType.team if team else AccessGrantType.user,
        permission=permission,
        expires_at=expires_at,
    )
    db.add(access)
    db.flush()
    return access


@pytest.fixture
def world(db):
    """A small, fully-described world: 2 teams, 5 people, 3 repositories."""
    repos = {
        name: make_repo(db, name)
        for name in ("pallets/click", "pallets/flask", "psf/requests")
    }
    admin = make_user(db, "admin.user", UserRole.admin)
    pm = make_user(db, "priya.pm", UserRole.analyst)  # project manager
    eng = make_user(db, "raj.eng", UserRole.analyst)  # team member
    viewer = make_user(db, "ana.viewer", UserRole.viewer)  # read-only role
    outsider = make_user(db, "no.access", UserRole.analyst)  # no grants at all

    platform = make_team(db, "Platform", pm)
    product = make_team(db, "Product", pm)
    add_member(db, platform, eng, TeamRole.member)
    add_member(db, product, eng, TeamRole.member)

    grant(db, repos["pallets/click"], platform, permission=RepoPermission.write)
    grant(db, repos["pallets/flask"], platform, permission=RepoPermission.read)

    return {
        "db": db,
        "repos": repos,
        "admin": admin,
        "pm": pm,
        "eng": eng,
        "viewer": viewer,
        "outsider": outsider,
        "platform": platform,
        "product": product,
    }


# ------------------------------------------------------------------ unit tests
class TestPermissionRanking:
    def test_strongest_picks_the_highest(self):
        assert A.strongest([RepoPermission.read, RepoPermission.admin]) is RepoPermission.admin
        assert A.strongest([RepoPermission.read, RepoPermission.write]) is RepoPermission.write

    def test_strongest_of_nothing_is_none(self):
        assert A.strongest([]) is None
        assert A.strongest([None, None]) is None

    def test_weaker_caps_to_the_lower(self):
        assert A.weaker(RepoPermission.admin, RepoPermission.read) is RepoPermission.read
        assert A.weaker(RepoPermission.read, RepoPermission.admin) is RepoPermission.read
        assert A.weaker(RepoPermission.write, RepoPermission.admin) is RepoPermission.write

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (UserRole.admin, RepoPermission.admin),
            (UserRole.analyst, RepoPermission.write),
            (UserRole.viewer, RepoPermission.read),
        ],
    )
    def test_global_role_is_the_ceiling(self, world, role, expected):
        assert (
            A.cap_by_role(RepoPermission.admin, make_user(world["db"], f"u{role.value}", role))
            is expected
        )

    def test_ceiling_never_increases_the_permission(self, world):
        viewer = world["viewer"]
        assert A.cap_by_role(RepoPermission.read, viewer) is RepoPermission.read


# ------------------------------------------------------------- effective access
class TestEffectivePermission:
    def test_admin_reaches_everything_without_a_grant(self, world):
        """A platform admin needs no grant row; that is the escape hatch."""
        for repo in world["repos"].values():
            assert (
                A.effective_permission(world["db"], world["admin"], repo.id)
                is RepoPermission.admin
            )

    def test_outsider_reaches_nothing(self, world):
        for repo in world["repos"].values():
            assert A.effective_permission(world["db"], world["outsider"], repo.id) is None

    def test_direct_grant_applies(self, world):
        grant(
            world["db"],
            world["repos"]["psf/requests"],
            user=world["eng"],
            permission=RepoPermission.read,
        )
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["psf/requests"].id
            )
            is RepoPermission.read
        )

    def test_expired_grant_does_not_apply(self, world):
        from datetime import UTC, datetime, timedelta

        grant(
            world["db"],
            world["repos"]["psf/requests"],
            user=world["eng"],
            permission=RepoPermission.read,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["psf/requests"].id
            )
            is None
        )

    def test_unexpired_grant_still_applies(self, world):
        from datetime import UTC, datetime, timedelta

        grant(
            world["db"],
            world["repos"]["psf/requests"],
            user=world["eng"],
            permission=RepoPermission.read,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["psf/requests"].id
            )
            is RepoPermission.read
        )

    def test_global_role_caps_a_team_grant(self, world, db):
        """`viewer` can never exceed `read` however generous the team's grant."""
        grant(
            db,
            world["repos"]["pallets/click"],
            world["platform"],
            permission=RepoPermission.admin,
        )
        add_member(db, world["platform"], world["viewer"], TeamRole.manager)
        assert (
            A.effective_permission(db, world["viewer"], world["repos"]["pallets/click"].id)
            is RepoPermission.read
        )


# ------------------------------------------------------- regression: team manager
class TestManagerDoesNotLeakPlatform:
    """A project manager administers their team's repositories - and only those.

    Regression test: the manager branch used to append `admin` whenever a team had no
    grant for the repository being checked, which granted every manager visibility of
    the entire platform.
    """

    def test_manager_cannot_see_a_repository_their_team_lacks(self, world):
        permission = A.effective_permission(
            world["db"], world["pm"], world["repos"]["psf/requests"].id
        )
        assert permission is None

    def test_manager_reaches_exactly_the_teams_repositories(self, world):
        reachable = A.accessible_repository_ids(world["db"], world["pm"])
        assert reachable == {
            world["repos"]["pallets/click"].id,
            world["repos"]["pallets/flask"].id,
        }

    def test_manager_permission_is_capped_by_the_team_grant(self, world):
        """The team holds `read` on flask, so the manager reads it - not administers it."""
        assert (
            A.effective_permission(
                world["db"], world["pm"], world["repos"]["pallets/flask"].id
            )
            is RepoPermission.read
        )
        assert not A.can_admin(world["db"], world["pm"], world["repos"]["pallets/flask"].id)

    def test_manager_with_an_admin_grant_may_administer(self, world, db):
        """`priya` is only an `analyst`, so the grant lands as `write`, not `admin`.

        The global role is the ceiling, and that is the whole point of having one.
        """
        grant(
            db,
            world["repos"]["pallets/click"],
            world["platform"],
            permission=RepoPermission.admin,
        )
        assert (
            A.effective_permission(db, world["pm"], world["repos"]["pallets/click"].id)
            is RepoPermission.write
        )

    def test_an_admin_role_reaches_admin_even_from_a_write_grant(self, world, db):
        """A `viewer` cannot exceed `read` and an `analyst` cannot exceed `write`."""
        grant(
            db,
            world["repos"]["pallets/click"],
            world["platform"],
            permission=RepoPermission.admin,
        )
        add_member(db, world["platform"], world["viewer"], TeamRole.manager)
        assert (
            A.effective_permission(db, world["viewer"], world["repos"]["pallets/click"].id)
            is RepoPermission.read
        )

    def test_manager_still_manages_the_team_itself(self, world):
        assert A.can_manage_team(world["db"], world["pm"], world["platform"].id)
        assert A.can_manage_access(world["db"], world["pm"], world["platform"].id)


# ------------------------------------------------------------ regression: capping
class TestTeamRoleCannotExceedItsGrant:
    """A team role may not exceed the grant the team itself holds.

    Regression test: `TEAM_ROLE_PERMISSION[manager] == admin` used to be applied
    unclamped, so a member of a `read`-only team could administer the repository.
    """

    def test_member_of_a_read_team_gets_read(self, world):
        # `eng` is already a `member` of Platform via the fixture, whose flask grant
        # is `read`. The member role must not lift that to `admin`.
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["pallets/flask"].id
            )
            is RepoPermission.read
        )
        assert not A.can_admin(world["db"], world["eng"], world["repos"]["pallets/flask"].id)

    def test_manager_of_a_read_team_gets_read(self, world):
        assert (
            A.effective_permission(
                world["db"], world["pm"], world["repos"]["pallets/flask"].id
            )
            is RepoPermission.read
        )

    def test_member_of_a_write_team_gets_write(self, world):
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["pallets/click"].id
            )
            is RepoPermission.write
        )
        assert A.can_write(world["db"], world["eng"], world["repos"]["pallets/click"].id)
        assert not A.can_admin(world["db"], world["eng"], world["repos"]["pallets/click"].id)


# --------------------------------------------------------- regression: scoping
class TestScopedMembership:
    """`scoped_repository_id` pins a member to one repository inside their team.

    Regression test: the scope column was ignored when computing permissions, so
    narrowing a member's access had no effect at all.
    """

    @pytest.fixture
    def scoped(self, world):
        db = world["db"]
        team = world["platform"]
        ana = world["viewer"]
        add_member(db, team, ana, TeamRole.member, scoped=world["repos"]["pallets/click"])
        return world

    def test_scoped_member_reaches_their_repository(self, scoped):
        assert A.can_read(scoped["db"], scoped["viewer"], scoped["repos"]["pallets/click"].id)

    def test_scoped_member_is_blocked_from_the_rest_of_the_team(self, scoped):
        """`flask` is granted to the same team, but ana is pinned away from it."""
        assert (
            A.effective_permission(
                scoped["db"], scoped["viewer"], scoped["repos"]["pallets/flask"].id
            )
            is None
        )

    def test_scoped_member_does_not_appear_in_the_repository_scope(self, scoped):
        assert scoped["repos"]["pallets/click"].id in A.accessible_repository_ids(
            scoped["db"], scoped["viewer"]
        )
        assert scoped["repos"]["pallets/flask"].id not in A.accessible_repository_ids(
            scoped["db"], scoped["viewer"]
        )

    def test_scope_cannot_reach_a_repository_the_team_lacks(self, world):
        """Pinning to a repository the team was never granted grants nothing."""
        db = world["db"]
        add_member(
            db,
            world["platform"],
            world["viewer"],
            TeamRole.member,
            scoped=world["repos"]["psf/requests"],
        )
        assert (
            A.effective_permission(db, world["viewer"], world["repos"]["psf/requests"].id)
            is None
        )


# ------------------------------------------------------------------ team scoping
class TestTeamRoles:
    def test_non_member_has_no_role(self, world):
        assert A.team_role_for(world["db"], world["outsider"], world["platform"].id) is None
        assert (
            A.team_capabilities(world["db"], world["outsider"], world["platform"].id)
            == frozenset()
        )

    def test_manager_has_all_capabilities(self, world):
        assert (
            A.team_capabilities(world["db"], world["pm"], world["platform"].id)
            == A.ALL_CAPABILITIES
        )

    def test_member_can_view_but_not_manage(self, world):
        caps = A.team_capabilities(world["db"], world["eng"], world["platform"].id)
        assert caps == frozenset({"view"})
        assert not A.can_manage_team(world["db"], world["eng"], world["platform"].id)

    def test_viewer_of_the_team_cannot_manage(self, world, db):
        add_member(db, world["platform"], world["viewer"], TeamRole.viewer)
        assert not A.can_manage_team(db, world["viewer"], world["platform"].id)
        assert not A.can_manage_access(db, world["viewer"], world["platform"].id)

    def test_platform_admin_can_manage_any_team(self, world):
        assert A.can_manage_team(world["db"], world["admin"], world["platform"].id)
        assert A.managed_team_ids(world["db"], world["admin"]) >= {
            world["platform"].id,
            world["product"].id,
        }

    def test_an_admin_is_reported_as_manager_of_every_team(self, test_client, auth, db):
        """`my_role` must not read as `null` beside full capabilities."""
        make_repo(db, "acme/team-admin-repo")
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Admin View"}).json()
        assert team["my_role"] == "manager"
        assert sorted(team["capabilities"]) == [
            "manage_access",
            "manage_members",
            "view",
        ]

    def test_managed_team_ids_includes_both_ways_of_being_a_manager(self, world):
        """Being `teams.manager_id` or a `manager`-role member both count."""
        other = make_user(world["db"], "second.pm", UserRole.analyst)
        make_team(world["db"], "Second", other)
        managed = A.managed_team_ids(world["db"], world["pm"])
        assert world["platform"].id in managed
        assert world["product"].id in managed
        # `second.pm` owns their team but is not a member of the first two.
        assert world["platform"].id not in A.managed_team_ids(world["db"], other)

    def test_inactive_team_grants_nothing(self, world):
        world["platform"].is_active = False
        world["db"].flush()
        assert (
            A.effective_permission(
                world["db"], world["eng"], world["repos"]["pallets/click"].id
            )
            is None
        )


# ------------------------------------------------------- regression: ownership
class TestOwnershipIsSelfServiceBootstrap:
    """Whoever submits a repository link keeps access to it.

    Without this, a newly registered user is locked out of their own account: analysts
    may call `analyze`, the repository is created carrying their `owner_id`, but with
    access granted only through teams they could never see what they just added.
    """

    def test_the_submitter_can_read_their_own_repository(self, world, db):
        mine = make_repo(db, "contoso/mine", owner=world["eng"])
        assert A.can_read(db, world["eng"], mine.id)

    def test_the_submitter_gets_write_so_they_can_refresh_it(self, world, db):
        mine = make_repo(db, "contoso/mine", owner=world["eng"])
        assert A.effective_permission(db, world["eng"], mine.id) is RepoPermission.write
        assert A.can_write(db, world["eng"], mine.id)

    def test_ownership_is_still_capped_by_the_global_role(self, world, db):
        """A `viewer` who submitted a link does not become an analyst."""
        mine = make_repo(db, "contoso/theirs", owner=world["viewer"])
        assert A.effective_permission(db, world["viewer"], mine.id) is RepoPermission.read
        assert not A.can_write(db, world["viewer"], mine.id)

    def test_ownership_appears_in_the_repository_scope(self, world, db):
        mine = make_repo(db, "contoso/mine", owner=world["eng"])
        ids = A.accessible_repository_ids(db, world["eng"])
        assert mine.id in ids
        names = {
            r.full_name
            for r in db.execute(
                A.apply_repository_scope(select(Repository), db, world["eng"])
            ).scalars()
        }
        assert "contoso/mine" in names

    def test_ownership_does_not_leak_to_other_people(self, world, db):
        """The submitter's ownership grants nobody else anything."""
        mine = make_repo(db, "contoso/mine", owner=world["eng"])
        assert A.effective_permission(db, world["pm"], mine.id) is None
        assert A.effective_permission(db, world["outsider"], mine.id) is None
        assert mine.id not in A.accessible_repository_ids(db, world["outsider"])

    def test_an_unowned_repository_grants_nothing(self, world, db):
        """Repositories imported by the ETL scripts have no owner and stay team-gated."""
        unowned = make_repo(db, "acme/team-only")
        assert A.effective_permission(db, world["outsider"], unowned.id) is None
        assert A.effective_permission(db, world["eng"], unowned.id) is None

    def test_the_strongest_of_ownership_and_a_grant_wins(self, world, db):
        """Ownership is `write`; a weaker explicit grant must not downgrade it."""
        mine = make_repo(db, "contoso/mine", owner=world["eng"])
        grant(db, mine, user=world["eng"], permission=RepoPermission.read)
        assert A.effective_permission(db, world["eng"], mine.id) is RepoPermission.write

    def test_a_stronger_grant_beats_ownership(self, world, db):
        """An administrator who owns a repository may administer it."""
        mine = make_repo(db, "contoso/theirs", owner=world["admin"])
        grant(db, mine, user=world["admin"], permission=RepoPermission.admin)
        assert A.effective_permission(db, world["admin"], mine.id) is \
            RepoPermission.admin


# ---------------------------------------------------------------- query scoping
class TestRepositoryScoping:
    def test_admin_is_unrestricted(self, world):
        assert A.accessible_repository_ids(world["db"], world["admin"]) is None

    def test_empty_scope_still_returns_a_usable_statement(self, world):
        """An empty `IN ()` is invalid SQL in PostgreSQL; the filter must be false-y."""
        from sqlalchemy import select

        stmt = A.apply_repository_scope(select(Repository), world["db"], world["outsider"])
        # The statement must compile and execute without raising.
        assert world["db"].execute(stmt).scalars().all() == []

    def test_scope_narrows_to_the_two_platform_repositories(self, world):
        stmt = A.apply_repository_scope(select(Repository), world["db"], world["pm"])
        names = {r.full_name for r in world["db"].execute(stmt).scalars()}
        assert names == {"pallets/click", "pallets/flask"}

    def test_multi_team_member_sees_the_union(self, world):
        grant(
            world["db"],
            world["repos"]["psf/requests"],
            world["product"],
            permission=RepoPermission.read,
        )
        names = {
            r.full_name
            for r in world["db"]
            .execute(A.apply_repository_scope(select(Repository), world["db"], world["eng"]))
            .scalars()
        }
        assert names == {"pallets/click", "pallets/flask", "psf/requests"}


# ----------------------------------------------------------------- API surface
def sign_in(test_client, username: str) -> dict[str, str]:
    """Log a freshly-created user in and return an Authorization header."""
    token = test_client.post(
        "/api/auth/login", json={"username": username, "password": "TestPass123"}
    )
    assert token.status_code == 200, token.text
    return {"Authorization": f"Bearer {token.json()['access_token']}"}


class TestTeamsApi:
    def test_create_team_makes_the_creator_its_manager(self, test_client, auth, db):
        response = test_client.post(
            "/api/teams", headers=auth, json={"name": "Payments Squad"}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["slug"] == "payments-squad"
        assert body["my_role"] == "manager"
        assert body["manager_username"] == "tester"
        assert sorted(body["capabilities"]) == ["manage_access", "manage_members", "view"]

    def test_duplicate_team_name_is_rejected(self, test_client, auth):
        test_client.post("/api/teams", headers=auth, json={"name": "Duplicated"})
        again = test_client.post("/api/teams", headers=auth, json={"name": "Duplicated"})
        assert again.status_code == 409
        assert again.json()["code"] == "team_exists"

    def test_list_only_shows_your_teams(self, test_client, auth, db):
        make_user(db, "stranger", UserRole.analyst)
        db.commit()
        headers = sign_in(test_client, "stranger")
        test_client.post("/api/teams", headers=auth, json={"name": "Secret Squad"})
        listed = test_client.get("/api/teams", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["items"] == []

    def test_add_member_then_change_their_role(self, test_client, auth, db):
        make_user(db, "new.dev", UserRole.analyst)
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Growth"}).json()
        added = test_client.post(
            f"/api/teams/{team['id']}/members",
            headers=auth,
            json={"username": "new.dev", "role": "viewer"},
        )
        assert added.status_code == 201, added.text
        assert added.json()["role"] == "viewer"

        promoted = test_client.patch(
            f"/api/teams/{team['id']}/members/{added.json()['user_id']}",
            headers=auth,
            json={"role": "member"},
        )
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "member"

    def test_adding_the_same_member_twice_conflicts(self, test_client, auth, db):
        make_user(db, "twice.dev", UserRole.analyst)
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Twice"}).json()
        payload = {"username": "twice.dev"}
        assert (
            test_client.post(
                f"/api/teams/{team['id']}/members", headers=auth, json=payload
            ).status_code
            == 201
        )
        second = test_client.post(
            f"/api/teams/{team['id']}/members", headers=auth, json=payload
        )
        assert second.status_code == 409
        assert second.json()["code"] == "already_a_member"

    def test_unknown_username_is_404(self, test_client, auth):
        team = test_client.post("/api/teams", headers=auth, json={"name": "Ghosts"}).json()
        response = test_client.post(
            f"/api/teams/{team['id']}/members", headers=auth, json={"username": "nobody"}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "user_not_found"

    def test_remove_member(self, test_client, auth, db):
        make_user(db, "leaving.dev", UserRole.analyst)
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Leavers"}).json()
        member = test_client.post(
            f"/api/teams/{team['id']}/members",
            headers=auth,
            json={"username": "leaving.dev"},
        ).json()
        removed = test_client.delete(
            f"/api/teams/{team['id']}/members/{member['user_id']}", headers=auth
        )
        assert removed.status_code == 204
        remaining = test_client.get(f"/api/teams/{team['id']}", headers=auth)
        assert remaining.json()["member_count"] == 1  # only the manager

    def test_a_plain_member_cannot_manage_the_team(self, test_client, auth, db):
        team = test_client.post("/api/teams", headers=auth, json={"name": "Guarded"}).json()
        make_user(db, "plain.dev", UserRole.analyst)
        db.commit()
        test_client.post(
            f"/api/teams/{team['id']}/members", headers=auth, json={"username": "plain.dev"}
        )
        headers = sign_in(test_client, "plain.dev")

        make_user(db, "victim.dev", UserRole.analyst)
        db.commit()
        blocked = test_client.post(
            f"/api/teams/{team['id']}/members",
            headers=headers,
            json={"username": "victim.dev"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "not_team_manager"

    def test_non_member_cannot_even_read_the_team(self, test_client, auth, db):
        team = test_client.post("/api/teams", headers=auth, json={"name": "Closed"}).json()
        make_user(db, "curious.dev", UserRole.analyst)
        db.commit()
        headers = sign_in(test_client, "curious.dev")
        response = test_client.get(f"/api/teams/{team['id']}", headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "not_a_team_member"

    def test_grant_and_revoke_repository_access(self, test_client, auth, db):
        repo = make_repo(db, "acme/team-widget")
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Widgets"}).json()

        created = test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": repo.id, "permission": "write"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["repository_name"] == "acme/team-widget"
        assert created.json()["permission"] == "write"

        detail = test_client.get(f"/api/teams/{team['id']}", headers=auth).json()
        assert detail["repository_count"] == 1

        revoked = test_client.delete(
            f"/api/teams/{team['id']}/access/{created.json()['id']}", headers=auth
        )
        assert revoked.status_code == 204
        assert (
            test_client.get(f"/api/teams/{team['id']}", headers=auth).json()[
                "repository_count"
            ]
            == 0
        )

    def test_regranting_updates_in_place(self, test_client, auth, db):
        repo = make_repo(db, "acme/team-gadget")
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Gadgets"}).json()
        first = test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": repo.id, "permission": "read"},
        )
        second = test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": repo.id, "permission": "admin"},
        )
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["permission"] == "admin"

    def test_grant_to_a_missing_repository_is_404(self, test_client, auth):
        team = test_client.post("/api/teams", headers=auth, json={"name": "Empty"}).json()
        response = test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": 999_999, "permission": "read"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "repository_not_found"

    def test_team_grant_may_not_target_a_user(self, test_client, auth, db):
        repo = make_repo(db, "acme/team-thing")
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Things"}).json()
        response = test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": repo.id, "user_id": 1},
        )
        assert response.status_code == 422

    def test_admin_sees_every_repository_as_admin(self, test_client, auth, db):
        # Other tests in this session share the database, so compare against the
        # repository count rather than an absolute number.
        make_repo(db, "acme/team-one")
        make_repo(db, "acme/team-two")
        db.commit()
        expected = db.execute(select(func.count(Repository.id))).scalar_one()
        body = test_client.get("/api/teams/access/me", headers=auth).json()
        assert len(body["repositories"]) == expected
        assert all(r["permission"] == "admin" for r in body["repositories"])
        assert all(r["source"] == "admin" for r in body["repositories"])

    def test_a_user_with_no_grants_sees_nothing(self, test_client, auth, db):
        make_repo(db, "acme/team-hidden")
        db.commit()
        make_user(db, "nobody.here", UserRole.analyst)
        db.commit()
        headers = sign_in(test_client, "nobody.here")
        body = test_client.get("/api/teams/access/me", headers=headers).json()
        assert body["repositories"] == []
        assert body["manages_teams"] == 0

    def test_team_inherited_access_names_the_team(self, test_client, auth, db):
        repo = make_repo(db, "acme/team-inherited")
        db.commit()
        team = test_client.post("/api/teams", headers=auth, json={"name": "Inheritors"}).json()
        test_client.post(
            f"/api/teams/{team['id']}/access",
            headers=auth,
            json={"repository_id": repo.id, "permission": "read"},
        )
        make_user(db, "heir.dev", UserRole.analyst)
        db.commit()
        test_client.post(
            f"/api/teams/{team['id']}/members", headers=auth, json={"username": "heir.dev"}
        )
        headers = sign_in(test_client, "heir.dev")
        body = test_client.get("/api/teams/access/me", headers=headers).json()
        assert len(body["repositories"]) == 1
        entry = body["repositories"][0]
        assert entry["source"] == "team"
        assert entry["via_teams"] == ["Inheritors"]


class TestRepositoryRoutesAreGated:
    """The access service is only useful if the data routes actually call it."""

    def test_anonymous_cannot_read_a_repository(self, test_client, db):
        repo = make_repo(db, "acme/team-private")
        db.commit()
        response = test_client.get(f"/api/repositories/{repo.id}")
        assert response.status_code == 401

    def test_a_user_without_access_is_refused(self, test_client, auth, db):
        repo = make_repo(db, "acme/team-team-restricted")
        db.commit()
        make_user(db, "blocked.dev", UserRole.analyst)
        db.commit()
        headers = sign_in(test_client, "blocked.dev")
        response = test_client.get(f"/api/repositories/{repo.id}", headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "no_repository_access"

    def test_a_user_can_read_a_repository_they_ingested(self, test_client, auth, db):
        """The bootstrap path: `analyze` sets `owner_id`, so the submitter keeps access."""
        mine = make_repo(db, "contoso/submitted", owner=_user_object(db, "tester"))
        db.commit()
        response = test_client.get(f"/api/repositories/{mine.id}", headers=auth)
        assert response.status_code == 200
        assert response.json()["full_name"] == "contoso/submitted"

    def test_the_listing_hides_ungranted_repositories(self, test_client, auth, db):
        make_repo(db, "acme/team-visible")
        make_repo(db, "acme/team-invisible")
        db.commit()
        make_user(db, "partial.dev", UserRole.analyst)
        db.commit()

        grant_user = test_client.post(
            "/api/teams/access",
            headers=auth,
            json={
                "permission": "read",
                "user_id": _user_id(db, "partial.dev"),
                "repository_id": _repo_id(db, "acme/team-visible"),
            },
        )
        assert grant_user.status_code == 201, grant_user.text

        headers = sign_in(test_client, "partial.dev")
        listing = test_client.get("/api/repositories?page_size=100", headers=headers)
        assert listing.status_code == 200, listing.text
        names = {r["full_name"] for r in listing.json()["items"]}
        assert "acme/team-visible" in names
        assert "acme/team-invisible" not in names


def _user_id(db, username: str) -> int:
    return db.execute(select(User.id).where(User.username == username)).scalar_one()


def _repo_id(db, full_name: str) -> int:
    return db.execute(
        select(Repository.id).where(Repository.full_name == full_name)
    ).scalar_one()


def _user_object(db, username: str) -> User:
    return db.execute(select(User).where(User.username == username)).scalar_one()
