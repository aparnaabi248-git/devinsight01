"""Shared response envelopes: pagination, error, health."""

from __future__ import annotations

import math
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Uniform pagination envelope used by every list endpoint."""

    items: list[T]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    pages: int = Field(..., ge=0)

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if page_size else 0,
        )


class ErrorResponse(BaseModel):
    detail: str
    code: str = "error"
    request_id: str | None = None
    context: dict | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    models_loaded: list[str] = Field(default_factory=list)
    timestamp: str


class MessageResponse(BaseModel):
    message: str
    model_config = ConfigDict(from_attributes=True)
