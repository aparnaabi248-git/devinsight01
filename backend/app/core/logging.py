"""Structured logging with secret redaction. Never log tokens or passwords."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.core.config import settings

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "auth",
    "password",
    "secret",
    "secret_key",
    "github_token",
    "api_key",
    "apikey",
    "client_secret",
    "cookie",
    "set-cookie",
}
REDACTED = "***REDACTED***"

_configured = False


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in SENSITIVE_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = _redact(extra)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self.COLORS.get(record.levelname, "")
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {colour}{record.levelname:<8}{self.RESET} "
            f"{record.name:<28} {_redact(record.getMessage())}"
        )
        extra = getattr(record, "context", None)
        if extra:
            base += " " + json.dumps(_redact(extra), default=str)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.LOG_JSON else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL)
    for noisy in ("httpx", "httpcore", "urllib3", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
