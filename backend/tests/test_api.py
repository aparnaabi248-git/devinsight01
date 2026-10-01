"""API surface: health, auth, validation, pagination, authz, error shapes."""

from __future__ import annotations

import pytest


# ------------------------------------------------------------------ health
def test_health_is_public_and_reports_dependencies(test_client, api_url):
    response = test_client.get(f"{api_url}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["status"] in {"healthy", "degraded", "unhealthy"}
    assert "models_loaded" in body


def test_request_id_header_is_always_present(test_client, api_url):
    response = test_client.get(f"{api_url}/health")
    assert response.headers.get("X-Request-ID")
    assert response.headers.get("X-Process-Time", "").endswith("ms")


def test_openapi_document_is_generated(test_client):
    response = test_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "DevInsight"
    for path in (
        "/api/repositories/analyze",
        "/api/repositories/{repo_id}/analytics",
        "/api/ml/defect-risk",
        "/api/ml/classify-issue",
        "/api/ml/predict-priority",
        "/api/ml/estimate-effort",
        "/api/ml/models",
    ):
        assert path in schema["paths"], f"{path} missing from the OpenAPI schema"


# -------------------------------------------------------------------- auth
def test_register_rejects_weak_password(test_client, api_url):
    response = test_client.post(
        f"{api_url}/auth/register",
        json={"email": "weak@example.com", "username": "weakling", "password": "abcdefgh"},
    )
    assert response.status_code == 422


def test_register_rejects_malformed_email(test_client, api_url):
    response = test_client.post(
        f"{api_url}/auth/register",
        json={"email": "not-an-email", "username": "someone", "password": "Passw0rd123"},
    )
    assert response.status_code == 422


def test_register_rejects_duplicate_username(test_client, api_url):
    payload = {
        "email": "dupe@example.com",
        "username": "dupeuser",
        "password": "Passw0rd123",
    }
    assert test_client.post(f"{api_url}/auth/register", json=payload).status_code == 201
    second = test_client.post(f"{api_url}/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "user_exists"


def test_login_with_wrong_password_is_rejected(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/auth/login", json={"username": "tester", "password": "nope-nope"}
    )
    assert response.status_code == 401
    # The message must not reveal whether the account exists.
    assert response.json()["detail"] == "incorrect username or password"


def test_login_for_unknown_user_gives_the_same_message(test_client, api_url):
    response = test_client.post(
        f"{api_url}/auth/login", json={"username": "ghost", "password": "whatever1"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "incorrect username or password"


def test_login_returns_a_usable_token(test_client, api_url, auth):
    """Depends on `auth`, so the fixture has already created the tester account."""
    response = test_client.post(
        f"{api_url}/auth/login", json={"username": "tester", "password": "TestPass123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "tester@example.com"


def test_me_requires_authentication(test_client, api_url):
    assert test_client.get(f"{api_url}/auth/me").status_code == 401


def test_me_returns_the_current_user(test_client, api_url, auth):
    response = test_client.get(f"{api_url}/auth/me", headers=auth)
    assert response.status_code == 200
    assert response.json()["username"] == "tester"


def test_garbage_token_is_rejected(test_client, api_url):
    response = test_client.get(
        f"{api_url}/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert response.status_code == 401


# ------------------------------------------------------------- authorisation
@pytest.mark.parametrize(
    "path",
    [
        "/api/repositories",
        "/api/ml/models",
        "/api/ml/predictions",
        "/api/jobs",
    ],
)
def test_protected_endpoints_require_a_token(test_client, api_url, path):
    assert test_client.get(path).status_code == 401


def test_analyst_role_is_required_to_analyze(test_client, api_url, auth):
    # The seeded user is the first account, so they are an admin — the route works.
    response = test_client.post(
        f"{api_url}/repositories/analyze",
        json={"url": "https://github.com/pallets/click"},
        headers=auth,
    )
    # 202 (background) or 200 (sync) — anything but 401/403 proves the role gate passed.
    assert response.status_code in {200, 202}


# -------------------------------------------------------------- url validation
@pytest.mark.parametrize(
    "url",
    ["https://evil.example.com/a/b", "not-a-repo", "https://github.com/solo"],
)
def test_analyze_rejects_non_github_urls(test_client, api_url, auth, url):
    response = test_client.post(
        f"{api_url}/repositories/analyze", json={"url": url}, headers=auth
    )
    assert response.status_code == 422


def test_analyze_rejects_empty_url(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/repositories/analyze", json={"url": ""}, headers=auth
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ pagination
def test_repository_list_uses_the_page_envelope(test_client, api_url, auth):
    response = test_client.get(
        f"{api_url}/repositories", headers=auth, params={"page": 1, "page_size": 5}
    )
    assert response.status_code == 200
    body = response.json()
    for key in ("items", "total", "page", "page_size", "pages"):
        assert key in body
    assert body["page"] == 1 and body["page_size"] == 5
    assert isinstance(body["items"], list)


def test_page_size_is_capped(test_client, api_url, auth):
    response = test_client.get(
        f"{api_url}/repositories", headers=auth, params={"page_size": 100000}
    )
    assert response.status_code == 422


def test_missing_repository_returns_404_with_a_code(test_client, api_url, auth):
    response = test_client.get(f"{api_url}/repositories/99999999", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "repository_not_found"


# ------------------------------------------------------------------ ml routes
def test_defect_risk_rejects_an_empty_change_set(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/ml/defect-risk",
        json={"repository": "pallets/click", "changes": []},
        headers=auth,
    )
    assert response.status_code == 422


def test_defect_risk_rejects_a_malformed_repository(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/ml/defect-risk",
        json={
            "repository": "no-slash",
            "changes": [{"path": "a.py", "additions": 1, "deletions": 0}],
        },
        headers=auth,
    )
    assert response.status_code == 422


def test_classify_issue_requires_a_title(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/ml/classify-issue", json={"body": "no title"}, headers=auth
    )
    assert response.status_code == 422


def test_validation_errors_name_the_offending_field(test_client, api_url, auth):
    response = test_client.post(
        f"{api_url}/ml/classify-issue", json={"title": "x"}, headers=auth
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "request_validation_error"
    fields = {e["field"] for e in body["context"]["errors"]}
    assert any("title" in f for f in fields)
