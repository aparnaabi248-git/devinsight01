"""Liveness / readiness endpoint."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.config import settings
from app.db.session import check_connection
from app.schemas.common import HealthResponse
from app.services.github_client import get_github_client
from app.services.ml_service import available_models

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe plus dependency status. Safe to expose unauthenticated."""
    db_ok = check_connection()
    models = available_models()
    status = "healthy" if (db_ok and models) else ("degraded" if db_ok else "unhealthy")
    return HealthResponse(
        status=status,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
        database="connected" if db_ok else "unreachable",
        models_loaded=models,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/health/github")
def github_health() -> dict:
    """GitHub API reachability and remaining quota (never exposes the token)."""
    return {
        "authenticated": bool(os.getenv("GITHUB_TOKEN") or settings.GITHUB_TOKEN),
        "rate_limit": get_github_client().get_rate_limit(),
        "token_fingerprint": get_github_client().token_fingerprint,
    }
