"""API dependencies: DB session, current user, role gates."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, PermissionError_
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.base import RepoPermission, UserRole
from app.db.session import get_db
from app.models.user import User

log = get_logger("api.deps")

bearer_scheme = HTTPBearer(auto_error=False, description="JWT bearer token")
DbSession = Annotated[Session, Depends(get_db)]

# viewer < analyst < admin
ROLE_ORDER = {UserRole.viewer: 0, UserRole.analyst: 1, UserRole.admin: 2}


def get_current_user_optional(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User | None:
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:
        log.warning("token rejected", extra={"context": {"error": type(exc).__name__}})
        raise AuthenticationError(
            "invalid or expired access token", code="invalid_token"
        ) from exc
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("user is inactive or no longer exists", code="inactive_user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user_optional)]


# ------------------------------------------------------- repository access gate
def require_repo_read(repo_id: int, db: DbSession, user: AuthenticatedUser) -> int:
    """Reject a caller who cannot see the repository at all."""
    from app.services import access as A

    if not A.can_read(db, user, repo_id):
        raise PermissionError_(
            f"you do not have access to repository {repo_id}", code="no_repository_access"
        )
    return repo_id


def require_repo_write(repo_id: int, db: DbSession, user: AuthenticatedUser) -> int:
    """Reject a caller who may read but not modify the repository."""
    from app.services import access as A

    permission = A.effective_permission(db, user, repo_id)
    if permission is None:
        raise PermissionError_(
            f"you do not have access to repository {repo_id}", code="no_repository_access"
        )
    if not A._at_least(permission, RepoPermission.write):  # noqa: SLF001
        raise PermissionError_(
            f"you have '{permission.value}' access to repository {repo_id}; "
            "'write' or 'admin' is required",
            code="insufficient_repository_access",
        )
    return repo_id


RepoRead = Annotated[int, Depends(require_repo_read)]
RepoWrite = Annotated[int, Depends(require_repo_write)]


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    if user is None:
        raise AuthenticationError("authentication required", code="missing_token")
    return user


AuthenticatedUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: UserRole):
    """Dependency factory: the endpoint requires at least `minimum`."""

    def _check(user: AuthenticatedUser) -> User:
        if ROLE_ORDER.get(user.role, 0) < ROLE_ORDER[minimum]:
            raise PermissionError_(
                f"role '{user.role.value}' is below the required '{minimum.value}'",
                code="insufficient_role",
            )
        return user

    return _check


RequireAnalyst = Annotated[User, Depends(require_role(UserRole.analyst))]
RequireAdmin = Annotated[User, Depends(require_role(UserRole.admin))]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
