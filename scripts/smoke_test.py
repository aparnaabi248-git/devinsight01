"""End-to-end smoke test against a live API: register, ingest, query, predict."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api"
TOKEN: str | None = None
FAILURES: list[str] = []


def call(
    method: str, path: str, body: dict | None = None, expect: tuple[int, ...] = (200,)
):
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
        with urllib.request.urlopen(request, timeout=180) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    except Exception as exc:  # connection refused etc.
        status, payload = 0, str(exc).encode()

    parsed: object
    try:
        parsed = json.loads(payload) if payload else None
    except json.JSONDecodeError:
        parsed = payload[:400].decode(errors="replace")

    ok = status in expect
    mark = "PASS" if ok else "FAIL"
    detail = "" if ok else f" -> {status} {str(parsed)[:200]}"
    print(f"  [{mark}] {method:<6} {path}{detail}")
    if not ok:
        FAILURES.append(f"{method} {path} -> {status}")
    return parsed


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def check_anonymous() -> None:
    """Protected routes must reject a caller with no token."""
    global TOKEN
    saved, TOKEN = TOKEN, None
    try:
        call("GET", "/repositories", expect=(401,))
        call("GET", "/ml/models", expect=(401,))
    finally:
        TOKEN = saved


def main() -> int:
    global TOKEN

    section("1. Health")
    health = call("GET", "/health")
    if isinstance(health, dict):
        print(
            f"         status={health.get('status')} db={health.get('database')} "
            f"models={health.get('models_loaded')}"
        )

    section("2. Authentication")
    token = call(
        "POST",
        "/auth/login",
        {"username": "smoke", "password": "SmokePass123"},
        expect=(200, 401),
    )
    if not isinstance(token, dict) or "access_token" not in token:
        token = call(
            "POST",
            "/auth/register",
            {
                "email": "smoke@example.com",
                "username": "smoke",
                "password": "SmokePass123",
            },
            expect=(200, 201),
        )
    if isinstance(token, dict) and "access_token" in token:
        TOKEN = token["access_token"]
        print(
            f"         authenticated as {token['user']['username']} "
            f"({token['user']['role']})"
        )
    else:
        print("  [FAIL] could not authenticate; aborting")
        return 1

    call("GET", "/auth/me")

    section("3. Repository ingestion")
    call(
        "POST",
        "/repositories/analyze",
        {
            "url": "https://github.com/pallets/click",
            "max_commits": 60,
            "max_issues": 20,
            "background": False,
        },
        expect=(200, 202),
    )
    repos = call("GET", "/repositories", {"page_size": 5})
    repo_id = None
    if isinstance(repos, dict) and repos.get("items"):
        repo_id = repos["items"][0]["id"]
        print(
            f"         repo_id={repo_id} "
            f"commits={repos['items'][0]['ingested_commits']} "
            f"issues={repos['items'][0]['ingested_issues']} "
            f"status={repos['items'][0]['sync_status']}"
        )

    section("4. Analytics")
    if repo_id:
        metrics = call("GET", f"/repositories/{repo_id}/analytics")
        if isinstance(metrics, dict):
            print(
                f"         commits={metrics['total_commits']} "
                f"contributors={metrics['total_contributors']} "
                f"open_issues={metrics['open_issues']} "
                f"closure_rate={metrics['issue_closure_rate']}%"
            )
        call("GET", f"/repositories/{repo_id}/health")
        trends = call(
            "GET", f"/repositories/{repo_id}/trends", {"granularity": "monthly"}
        )
        if isinstance(trends, dict):
            print(
                f"         trend points={len(trends['points'])} "
                f"totals={trends['totals'].get('commits')} commits"
            )
        call("GET", f"/repositories/{repo_id}/contributors", {"page_size": 5})
        call("GET", f"/repositories/{repo_id}/commits", {"page_size": 3})
        call("GET", f"/repositories/{repo_id}/issues", {"page_size": 3})
        call("GET", f"/repositories/{repo_id}/pull-requests", {"page_size": 3})
        call("GET", f"/repositories/{repo_id}/releases")

    section("5. Model registry")
    models = call("GET", "/ml/models")
    if isinstance(models, dict):
        for item in models.get("items", []):
            metrics = item.get("metrics", {})
            headline = next(
                (
                    f"{k}={metrics[k]:.4f}"
                    for k in ("f1_macro", "mae", "r2", "accuracy")
                    if k in metrics
                ),
                "—",
            )
            print(f"         {item['tag']:<24} {item['algorithm']:<22} {headline}")
    call("GET", "/ml/models/comparison")

    section("6. ML predictions")
    risk = call(
        "POST",
        "/ml/defect-risk",
        {
            "repository": "pallets/click",
            "author": "smoke",
            "commit_message": "Refactor parameter parsing",
            "changes": [
                {
                    "path": "src/click/core.py",
                    "change_type": "modified",
                    "additions": 180,
                    "deletions": 40,
                },
                {
                    "path": "tests/test_core.py",
                    "change_type": "added",
                    "additions": 60,
                    "deletions": 5,
                },
            ],
        },
        expect=(200, 503),
    )
    if isinstance(risk, dict) and "risk_level" in risk:
        print(
            f"         {risk['risk_level']} p={risk['probability']:.4f} "
            f"model={risk['model_version']}"
        )

    category = call(
        "POST",
        "/ml/classify-issue",
        {
            "title": "AttributeError: _Token has no attribute _name",
            "body": "Traceback (most recent call last): AttributeError. This is a regression.",
            "labels": ["bug"],
        },
        expect=(200, 503),
    )
    if isinstance(category, dict) and "category" in category:
        print(f"         {category['category']} p={category['confidence']:.4f}")

    priority = call(
        "POST",
        "/ml/predict-priority",
        {
            "title": "Production database is corrupted",
            "body": "We are seeing data loss. Complete outage.",
            "labels": ["bug"],
            "comments_count": 6,
        },
        expect=(200, 503),
    )
    if isinstance(priority, dict) and "priority" in priority:
        print(
            f"         {priority['priority']} p={priority['confidence']:.4f} "
            f"(disclaimer present: {'disclaimer' in priority})"
        )

    effort = call(
        "POST",
        "/ml/estimate-effort",
        {
            "title": "Implement OAuth2 device-code login flow",
            "body": "Needs a new provider, token storage and tests.",
            "labels": ["feature"],
        },
        expect=(200, 503),
    )
    if isinstance(effort, dict) and "estimated_hours" in effort:
        print(
            f"         {effort['estimated_hours']} h "
            f"[{effort['estimate_low_hours']} – {effort['estimate_high_hours']}]"
        )

    section("7. Prediction audit log")
    predictions = call("GET", "/ml/predictions", {"page_size": 5})
    if isinstance(predictions, dict):
        print(f"         {predictions['total']} predictions recorded")
        for item in predictions.get("items", [])[:4]:
            print(
                f"         {item['target_type']:<16} {item['prediction']:<10} "
                f"{item['model_version_tag']}"
            )

    section("8. Negative cases")
    call("GET", "/repositories/999999", expect=(404,))
    call(
        "POST",
        "/repositories/analyze",
        {"url": "https://evil.example.com/a/b"},
        expect=(401, 422),
    )
    # A model that was never trained must surface as 503, not 500.
    call("GET", "/ml/evaluation/not_a_real_model", expect=(404, 503, 422))
    check_anonymous()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"SMOKE TEST FAILED — {len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("SMOKE TEST PASSED — every endpoint responded as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
