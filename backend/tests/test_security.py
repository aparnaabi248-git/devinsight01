"""Security layer: password hashing and JWT issue/verify."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_is_never_stored_in_plaintext():
    hashed = hash_password("SuperSecret123")
    assert hashed != "SuperSecret123"
    assert hashed.startswith("$2")  # bcrypt
    assert "SuperSecret123" not in hashed


def test_password_verification_round_trip():
    hashed = hash_password("SuperSecret123")
    assert verify_password("SuperSecret123", hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_long_password_does_not_raise():
    # bcrypt truncates at 72 bytes; the helper must not blow up.
    hashed = hash_password("x" * 500 + "Tail123")
    assert verify_password("x" * 500 + "Tail123", hashed) is True


def test_token_round_trip_carries_identity_and_role():
    token, expires = create_access_token(subject=42, role="analyst")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "analyst"
    assert payload["iss"] == settings.PROJECT_NAME
    assert payload["jti"]
    assert expires.tzinfo is not None


def test_expired_token_is_rejected():
    token, _ = create_access_token(
        subject=1, role="viewer", expires_delta=timedelta(seconds=-10)
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {
            "sub": "1",
            "role": "admin",
            "iss": settings.PROJECT_NAME,
            "exp": 4_000_000_000,
            "iat": 1,
        },
        "a-different-secret-entirely",
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)


def test_token_with_wrong_issuer_is_rejected():
    forged = jwt.encode(
        {"sub": "1", "role": "admin", "iss": "someone-else", "exp": 4_000_000_000},
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.InvalidIssuerError):
        decode_access_token(forged)
