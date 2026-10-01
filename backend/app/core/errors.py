"""Domain + infrastructure error types mapped to HTTP responses by FastAPI handlers."""

from __future__ import annotations


class DevInsightError(Exception):
    """Base for all application errors. Carries an HTTP status and stable code."""

    status_code = 500
    code = "internal_error"

    def __init__(self, detail: str, *, code: str | None = None, context: dict | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        self.context = context or {}


class NotFoundError(DevInsightError):
    status_code = 404
    code = "not_found"


class ValidationError(DevInsightError):
    status_code = 422
    code = "validation_error"


class ConflictError(DevInsightError):
    status_code = 409
    code = "conflict"


class AuthenticationError(DevInsightError):
    status_code = 401
    code = "authentication_error"


class PermissionError_(DevInsightError):
    status_code = 403
    code = "permission_denied"


class RateLimitError(DevInsightError):
    status_code = 429
    code = "rate_limit_exceeded"


class GitHubError(DevInsightError):
    status_code = 502
    code = "github_error"


class ModelNotAvailableError(DevInsightError):
    status_code = 503
    code = "model_not_available"
