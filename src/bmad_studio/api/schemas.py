"""Pydantic model ของ response (AD-12) — export เป็น OpenAPI แล้ว generate เป็น ``web/src/api/types.ts``."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ErrorEnvelope(BaseModel):
    ok: Literal[False]
    code: str
    error: str
    details: Any | None = None


class SessionData(BaseModel):
    authenticated: bool
    username: str | None = None
    role: Literal["admin", "viewer"] | None = None
    csrf: str | None = None
    review_url: str | None = None


class SessionEnvelope(BaseModel):
    ok: Literal[True]
    data: SessionData


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 422, 429, 503)
}
