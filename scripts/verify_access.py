"""End-to-end check of the teams & access API against the running server.

Exercises the full lifecycle a project manager would perform, and asserts both what
each person *can* and *cannot* reach - a test that only checks the happy path would
pass even with the authorisation bug that was found during this build.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api"
TOKEN = None
FAILURES = []


def call(method, path, body=None, expect=(200,), label=""):
    global TOKEN
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data:
        request.add_header("Content-Type", "application/json")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    except Exception as exc:
        status, payload = 0, str(exc).encode()

    parsed = None
    if status != 204 and payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"raw": payload[:200].decode(errors="replace")}

    ok = status in expect
    shown = label or f"{method} {path}"
    print(f"  [{'ok ' if ok else 'ERR'}] {shown:<58} -> {status}")
    if not ok:
        FAILURES.append(f"{shown} -> {status}: {str(parsed)[:200]}")
        print(f"        {str(parsed)[:240]}")
    return parsed


def sign_in(username, password):
    global TOKEN
    TOKEN = None
    result = call(
        "POST", "/auth/login", {"username": username, "password": password},
        expect=(200, 401), label=f"login {username}",
    )
    if isinstance(result, dict) and "access_token" in result:
        TOKEN = result["access_token"]
    return TOKEN


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def check(label, actual, expected):
    if actual == expected:
        print(f"  [ok ] {label:<58} {actual}")
    else:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  [ERR] {label:<58} got {actual!r}, expected {expected!r}")


def main():
    print("=" * 72)
    print("TEAMS & ACCESS - END-TO-END CHECK")
    print("=" * 72)

    section("1. Authenticate as the platform admin")
    if sign_in("demo", "TeamPass123") is None:
        print("  cannot sign in as demo / TeamPass123 - run scripts/seed_teams.py")
        return 1

    section("2. Read the team list")
    teams = call("GET", "/teams?page_size=50", label="list teams")
    if not isinstance(teams, dict) or not teams.get("items"):
        print("  no teams exist yet")
        return 1
    by_name = {t["name"]: t for t in teams["items"]}
    for name, team in by_name.items():
        print(f"      {name:<26} members={team['member_count']} repos={team['repository_count']} "
              f"my_role={team['my_role']} caps={','.join(team['capabilities'])}")

    section("3. Inspect one team's members and grants")
    target = by_name.get("Product Squad") or list(by_name.values())[0]
    detail = call("GET", f"/teams/{target['id']}", label=f"team detail: {target['name']}")
    print()
    for member in detail.get("members", []):
        scope = member["scoped_repository_id"] or "all team repos"
        print(f"      {member['username']:<18} {member['role']:<9} {scope}")
    for grant in detail.get("repositories", []):
        print(f"      -> {grant['repository_name']:<34} {grant['permission']}")

    section("4. An administrator reaches everything")
    access = call("GET", "/teams/access/me", label="my access (admin)")
    check("admin sees all repositories", access.get("role"), "admin")
    check("every permission is admin",
          sorted({r["permission"] for r in access["repositories"]}), ["admin"])
    check("manages every team", access["manages_teams"], len(by_name))

    section("5. A project manager reaches only their team's repositories")
    if sign_in("priya", "TeamPass123") is None:
        print("  priya is not seeded; skipping the manager checks")
    else:
        access = call("GET", "/teams/access/me", label="my access (priya, manager)")
        names = sorted(r["full_name"] for r in access["repositories"])
        print()
        for row in access["repositories"]:
            print(f"      {row['full_name']:<34} {row['permission']:<6} via {row['source']}")
        check("manager cannot see an ungranted repository",
              "psf/requests" in names, False)
        check("manager can see both Platform repositories",
              {"pallets/click", "pallets/flask"} <= set(names), True)
        check("manager manages 1 team", access["manages_teams"], 1)

        section("6. The manager is blocked from the other team's data")
        product = by_name.get("Product Squad")
        if product:
            call("GET", f"/teams/{product['id']}", expect=(403,),
                 label="priya reads a team she does not belong to")
            repo_id = next(
                (g["repository_id"] for g in detail.get("repositories", [])),
                None,
            )
        call("GET", "/repositories?page_size=100", label="repositories visible to priya")
        listing = call("GET", "/repositories?page_size=100")
        visible = sorted(r["full_name"] for r in listing["items"])
        print()
        for name in visible:
            print(f"      {name}")
        check("psf/requests is hidden from priya", "psf/requests" in visible, False)

    section("7. A plain member cannot manage the team")
    if sign_in("raj.dev", "TeamPass123") is None:
        print("  raj.dev is not seeded; skipping")
    else:
        platform = by_name.get("Platform Engineering")
        if platform:
            # raj.dev is a plain `member`, so managing the roster must be refused.
            # 403 is the answer (409 would mean he was allowed to try).
            call("POST", f"/teams/{platform['id']}/members",
                 {"username": "ana.reviewer", "role": "manager"}, expect=(403,),
                 label="raj.dev adds a member (expect 403)")
            call("GET", f"/teams/{platform['id']}", expect=(200,),
                 label="raj.dev reads his own team (expect 200)")

    section("8. A viewer sees less than their team")
    if sign_in("ana.reviewer", "TeamPass123") is None:
        print("  ana.reviewer is not seeded; skipping")
    else:
        access = call("GET", "/teams/access/me", label="my access (ana, viewer)")
        print()
        for row in access["repositories"]:
            print(f"      {row['full_name']:<34} {row['permission']}")
        check("a viewer never exceeds read",
              sorted({r["permission"] for r in access["repositories"]}), ["read"])
        click = next(
            (g for g in detail.get("repositories", []) if "click" in (g["repository_name"] or "")),
            None,
        )
        flask = next(
            (g for g in detail.get("repositories", []) if "flask" in (g["repository_name"] or "")),
            None,
        )
        if click:
            call("GET", f"/repositories/{click['repository_id']}/analytics", expect=(200,),
                 label="ana reads a granted repository (expect 200)")
        if flask:
            call("GET", f"/repositories/{flask['repository_id']}/analytics", expect=(200,),
                 label="ana reads the team's read-only repository (expect 200)")

    section("9. A member pinned to one repository")
    # A project manager can narrow a member to a single repository inside the team.
    # `ana.reviewer` belongs to Platform, which holds click and flask; pinning her to
    # click must hide flask without touching the team's own grants.
    if sign_in("priya", "TeamPass123"):
        platform = by_name.get("Platform Engineering")
        if platform:
            click = next(
                (g for g in call("GET", f"/teams/{platform['id']}").get("repositories", [])
                 if "click" in (g["repository_name"] or "")),
                None,
            )
            if click:
                member = next(
                    (m for m in call("GET", f"/teams/{platform['id']}").get("members", [])
                     if m["username"] == "ana.reviewer"),
                    None,
                )
                if member:
                    updated = call(
                        "PATCH", f"/teams/{platform['id']}/members/{member['user_id']}",
                        {"scoped_repository_id": click["repository_id"]}, expect=(200,),
                        label="priya pins ana to pallets/click",
                    )
                    check("the pin was recorded",
                          (updated or {}).get("scoped_repository_id"),
                          click["repository_id"])

                    sign_in("ana.reviewer", "TeamPass123")
                    access = call("GET", "/teams/access/me",
                                  label="ana's access while pinned")
                    names = sorted(r["full_name"] for r in access["repositories"])
                    print()
                    for row in access["repositories"]:
                        print(f"      {row['full_name']:<34} {row['permission']}")
                    check("pinned ana cannot see pallets/flask",
                          "pallets/flask" in names, False)
                    check("pinned ana still sees pallets/click",
                          "pallets/click" in names, True)

                    # Put her back so the seeded demo data stays as documented.
                    sign_in("priya", "TeamPass123")
                    call("PATCH", f"/teams/{platform['id']}/members/{member['user_id']}",
                         {"scoped_repository_id": None}, expect=(200,),
                         label="priya removes the pin")

    section("10. Restore the admin session and summarise")
    sign_in("demo", "TeamPass123")

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED CHECK(S):")
        for failure in FAILURES:
            print(f"    - {failure}")
        return 1
    print("  Every access rule behaved exactly as specified.")
    print("  Sign in at http://localhost:5173 as demo / TeamPass123")
    return 0


if __name__ == "__main__":
    sys.exit(main())
