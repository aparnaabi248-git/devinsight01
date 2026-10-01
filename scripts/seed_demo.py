"""Seed a demo account and ingest real GitHub data so the dashboard has content.

Usage:  python scripts/seed_demo.py [owner/name ...]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api"
REPOS = sys.argv[2:] or ["pallets/click"]
TOKEN: str | None = None

ACCOUNT = {"email": "demo@devinsight.dev", "username": "demo",
           "password": "DemoPass123", "full_name": "Demo Analyst"}


def call(method: str, path: str, body: dict | None = None, expect: tuple[int, ...] = (200,)):
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
        with urllib.request.urlopen(request, timeout=900) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    try:
        parsed = json.loads(payload) if payload else None
    except json.JSONDecodeError:
        parsed = payload[:300].decode(errors="replace")
    mark = "ok " if status in expect else "ERR"
    print(f"  [{mark}] {method:<6} {path} -> {status}")
    if status not in expect:
        print(f"        {str(parsed)[:300]}")
    return parsed


def promote_to_admin(username: str) -> bool:
    """Grant the platform-admin role directly in the database.

    Access control is enforced, so the demo account needs admin rights to reach every
    repository. Doing this in SQL avoids needing a bootstrap endpoint that would itself
    be an escalation hole.
    """
    import os
    import sys as _sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (str(root / "backend"), str(root)):
        if path not in _sys.path:
            _sys.path.insert(0, path)

    from sqlalchemy import select

    from app.db.base import UserRole
    from app.db.session import SessionLocal
    from app.models.user import User

    session = SessionLocal()
    try:
        user = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if user is None:
            return False
        user.role = UserRole.admin
        session.commit()
        return True
    except Exception as exc:  # pragma: no cover - best effort
        print(f"    could not promote: {type(exc).__name__}: {exc}")
        session.rollback()
        return False
    finally:
        session.close()
    del os


def main() -> int:
    print("=" * 60)
    print("SEEDING DevInsight demo data")
    print("=" * 60)

    health = call("GET", "/health")
    if not isinstance(health, dict):
        print("API is not reachable at", BASE)
        return 1
    print(f"  api={health.get('version')} db={health.get('database')} "
          f"models={health.get('models_loaded')}")

    # --- account ---------------------------------------------------------
    global TOKEN
    token = call("POST", "/auth/login",
                 {"username": ACCOUNT["username"], "password": ACCOUNT["password"]},
                 expect=(200, 401))
    if not isinstance(token, dict) or "access_token" not in token:
        token = call("POST", "/auth/register", ACCOUNT, expect=(200, 201))
    if isinstance(token, dict) and "access_token" in token:
        TOKEN = token["access_token"]
        print(f"  signed in as {token['user']['username']} ({token['user']['role']})")
    else:
        print("  could not authenticate")
        return 1

    # Repository access is enforced, so the demo account needs administrative rights
    # to see everything. Promote it directly if it was not the first account created.
    if token["user"]["role"] != "admin":
        print("  promoting the demo account to admin so it can see every repository…")
        promote_to_admin(ACCOUNT["username"])

    # --- ingest ----------------------------------------------------------
    for repo in REPOS:
        print(f"\n  ingesting {repo} (this hits the GitHub API — be patient)")
        result = call(
            "POST", "/repositories/analyze",
            {"url": f"https://github.com/{repo}", "max_commits": 300,
             "max_issues": 120, "background": False},
            expect=(200, 202, 429),
        )
        if isinstance(result, dict) and "message" in result:
            print(f"        {result.get('message')}")

    # --- what landed -----------------------------------------------------
    repos = call("GET", "/repositories?page_size=10")
    if isinstance(repos, dict) and repos.get("items"):
        print(f"\n  {len(repos['items'])} repository/repositories in the database:")
        for item in repos["items"]:
            print(f"    {item['full_name']:<28} {item['ingested_commits']:>5} commits  "
                  f"{item['ingested_issues']:>4} issues  status={item['sync_status']}")
        repo_id = repos["items"][0]["id"]
        metrics = call("GET", f"/repositories/{repo_id}/analytics")
        if isinstance(metrics, dict):
            print(f"\n  analytics for id={repo_id}:")
            print(f"    commits={metrics['total_commits']}  "
                  f"contributors={metrics['total_contributors']}  "
                  f"closure_rate={metrics['issue_closure_rate']}%  "
                  f"merge_rate={metrics['pr_merge_rate']}%")
    else:
        print("\n  no repositories were ingested")
        print("  (GitHub's anonymous quota is 60 req/hour — add GITHUB_TOKEN to .env "
              "and re-run for a fuller dataset)")

    print("\n" + "=" * 60)
    print("Sign in at http://localhost:5173/login")
    print(f"  username: {ACCOUNT['username']}")
    print(f"  password: {ACCOUNT['password']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
