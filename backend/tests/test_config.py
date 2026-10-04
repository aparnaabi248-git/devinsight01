import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        ("postgres://user:password@db.example/devinsight", "postgresql+psycopg://user:password@db.example/devinsight"),
        ("postgresql://user:password@db.example/devinsight", "postgresql+psycopg://user:password@db.example/devinsight"),
        ("postgresql+psycopg://user:password@db.example/devinsight", "postgresql+psycopg://user:password@db.example/devinsight"),
    ],
)
def test_sqlalchemy_url_uses_psycopg_driver(database_url: str, expected: str) -> None:
    settings = Settings(_env_file=None, DATABASE_URL=database_url)

    assert settings.sqlalchemy_url == expected
