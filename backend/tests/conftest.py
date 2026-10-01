"""Shared pytest fixtures.

The suite runs against a real PostgreSQL database because the schema uses PostgreSQL
ENUMs, GIN full-text indexes and `ON CONFLICT` upserts — none of which SQLite can
emulate faithfully. CI provisions a `postgres:16-alpine` service; locally, point
`DATABASE_URL` at any instance:

    docker run -d --name devinsight-test -p 5432:5432 \\
      -e POSTGRES_USER=devinsight -e POSTGRES_PASSWORD=devinsight \\
      -e POSTGRES_DB=devinsight_test postgres:16-alpine

If no database is reachable the DB-backed tests skip rather than fail, so the pure
unit tests (security, GitHub client, ML preprocessing) still run anywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
ML_ROOT = REPO_ROOT / "ml"

for path in (str(BACKEND_ROOT), str(ML_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-0123456789abcdef")
os.environ.setdefault("MLFLOW_TRACKING_ENABLED", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://devinsight:devinsight@localhost:5432/devinsight_test",
)


def _database_ready() -> tuple[bool, str]:
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, settings.sqlalchemy_url
    except Exception as exc:
        return False, str(exc).split("\n")[0]
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def database():
    """A clean schema per session. Skips the DB-backed tests if PostgreSQL is absent."""
    import app.models  # noqa: F401  (registers every table)
    from app.db.base import Base

    ok, detail = _database_ready()
    if not ok:
        pytest.skip(f"PostgreSQL is required for these tests: {detail}")

    from app.db.session import engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(database):
    """A session that is rolled back after each test, so tests stay independent."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def app_module(database):
    from app.main import create_app

    return create_app()


@pytest.fixture(scope="session")
def test_client(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="session")
def api_url() -> str:
    return "/api"


@pytest.fixture(scope="session")
def auth_headers(test_client, api_url) -> dict[str, str]:
    """Register once and reuse the token for the whole session."""
    response = test_client.post(
        f"{api_url}/auth/register",
        json={
            "email": "tester@example.com",
            "username": "tester",
            "password": "TestPass123",
            "full_name": "Test User",
        },
    )
    if response.status_code == 409:  # already registered in a previous run
        response = test_client.post(
            f"{api_url}/auth/login",
            json={"username": "tester", "password": "TestPass123"},
        )
    assert response.status_code in (200, 201), response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth(auth_headers) -> dict[str, str]:
    return auth_headers
