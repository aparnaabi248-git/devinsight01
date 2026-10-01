"""Password hashing and JWT issue/verify. bcrypt hashes only — never plaintext."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# bcrypt truncates at 72 bytes; passlib raises on longer input, so we pre-truncate.
BCRYPT_MAX_BYTES = 72
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _prepare(password: str) -> str:
    return password.encode("utf-8")[:BCRYPT_MAX_BYTES].decode("utf-8", "ignore")


def hash_password(password: str) -> str:
    return pwd_context.hash(_prepare(password))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(_prepare(plain), hashed)
    except ValueError:
        return False


def create_access_token(
    subject: str | int, role: str, expires_delta: timedelta | None = None
) -> tuple[str, datetime]:
    """Return (jwt, expires_at). Claims: sub, role, jti, iat, exp, iss."""
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.PROJECT_NAME,
    }
    token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.PROJECT_NAME,
    )
