"""Authentication routes: register, login, me."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AuthenticatedUser, DbSession
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import UserRole
from app.models.user import User
from app.schemas.auth import TokenResponse, UserLogin, UserOut, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: DbSession) -> TokenResponse:
    email = payload.email.lower().strip()
    username = payload.username.strip()

    existing = db.execute(
        select(User).where(
            (func.lower(User.email) == email) | (func.lower(User.username) == username.lower())
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("that email or username is already registered", code="user_exists")

    # The first account becomes an admin; everyone after is an analyst.
    is_first = db.execute(select(func.count(User.id))).scalar_one() == 0
    user = User(
        email=email,
        username=username,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.admin if is_first else UserRole.analyst,
        is_active=True,
        last_login_at=datetime.now(UTC),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent registration of the same identity
        db.rollback()
        raise ConflictError(
            "that email or username is already registered", code="user_exists"
        ) from exc
    db.refresh(user)

    token, expires = create_access_token(user.id, user.role.value)
    return TokenResponse(
        access_token=token, expires_at=expires, user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: DbSession) -> TokenResponse:
    identifier = payload.username.strip().lower()
    user = db.execute(
        select(User).where(
            (func.lower(User.email) == identifier) | (func.lower(User.username) == identifier)
        )
    ).scalar_one_or_none()

    # Constant-ish work whether or not the user exists, to avoid user enumeration.
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise AuthenticationError("incorrect username or password", code="invalid_credentials")
    if not user.is_active:
        raise AuthenticationError("this account has been deactivated", code="inactive_user")

    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)

    token, expires = create_access_token(user.id, user.role.value)
    return TokenResponse(
        access_token=token, expires_at=expires, user=UserOut.model_validate(user)
    )


@router.get("/me", response_model=UserOut)
def me(user: AuthenticatedUser) -> UserOut:
    return UserOut.model_validate(user)
