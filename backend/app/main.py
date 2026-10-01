"""FastAPI application factory: middleware, error handling, router mounting, OpenAPI."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import auth, health, ml, repositories, teams
from app.core.config import settings
from app.core.errors import DevInsightError
from app.core.logging import get_logger, setup_logging

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info(
        "starting devinsight",
        extra={"context": {"env": settings.ENVIRONMENT, "version": settings.VERSION}},
    )
    yield
    log.info("shutting down devinsight")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=(
            "DevInsight — AI-powered software engineering analytics and predictive "
            "intelligence. Ingest real GitHub repository history, compute engineering "
            "metrics, and serve four trained ML models: defect risk, issue "
            "classification, issue priority and effort estimation."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "DevInsight", "url": "https://github.com/"},
        license_info={"name": "MIT"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,  # the API is token-authenticated, not cookie-based
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Attach a request id, time the request, and log the outcome."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.error(
                "unhandled request error",
                extra={
                    "context": {
                        "path": request.url.path,
                        "method": request.method,
                        "duration_ms": round(elapsed, 2),
                    }
                },
            )
            raise
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{elapsed}ms"
        if request.url.path not in {"/api/health", "/api/v1/health", "/health"}:
            log.info(
                "request",
                extra={
                    "context": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": elapsed,
                        "request_id": request_id,
                    }
                },
            )
        return response

    # ------------------------------------------------------------- error shapes
    def _error(
        request: Request, status_code: int, detail: str, code: str, context: dict | None = None
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": detail,
                "code": code,
                "request_id": getattr(request.state, "request_id", None),
                "context": context,
            },
        )

    @app.exception_handler(DevInsightError)
    async def devinsight_error_handler(request: Request, exc: DevInsightError) -> JSONResponse:
        level = log.warning if exc.status_code < 500 else log.error
        level(
            f"{exc.code}: {exc.detail}",
            extra={"context": {"path": request.url.path, **exc.context}},
        )
        return _error(request, exc.status_code, exc.detail, exc.code, exc.context or None)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "request validation failed",
            "request_validation_error",
            {
                "errors": [
                    {
                        "field": ".".join(str(p) for p in e.get("loc", []) if p != "body"),
                        "message": e.get("msg"),
                        "type": e.get("type"),
                    }
                    for e in exc.errors()[:20]
                ]
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(request, exc.status_code, str(exc.detail), f"http_{exc.status_code}")

    @app.exception_handler(SQLAlchemyError)
    async def db_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.error(
            "database error",
            extra={"context": {"path": request.url.path, "error": str(exc)[:300]}},
        )
        return _error(request, 500, "a database error occurred", "database_error")

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled exception",
            extra={"context": {"path": request.url.path, "error": str(exc)[:300]}},
        )
        return _error(request, 500, "an unexpected error occurred", "internal_error")

    # ----------------------------------------------------------------- routers
    prefix = settings.API_V1_PREFIX
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(repositories.router, prefix=prefix)
    app.include_router(ml.router, prefix=prefix)
    app.include_router(teams.router, prefix=prefix)

    @app.get("/", tags=["system"], summary="Service banner")
    def root() -> dict:
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": f"{prefix}/health",
        }

    return app


app = create_app()
