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


class SectionError(BaseModel):
    code: str
    error: str
    reason: str | None = None


class SkillItem(BaseModel):
    id: str
    description: str
    override_team: bool
    override_user: bool


class AgentItem(BaseModel):
    id: str
    name: str | None = None
    title: str | None = None
    module: str | None = None
    description: str | None = None


class ResolvedConfig(BaseModel):
    """ส่วนที่มาจาก resolve_config.py — ``error`` ไม่ว่างเมื่อสคริปต์ใช้ไม่ได้ (ส่วนอื่นยังแสดงได้)."""
    ok: bool
    agents: list[AgentItem] = []
    core: dict[str, Any] = {}
    error: SectionError | None = None


class ToolStatus(BaseModel):
    uv: bool
    resolve_script: bool


class Inventory(BaseModel):
    version: str | None
    skills: list[SkillItem]
    config: ResolvedConfig
    tools: ToolStatus


class InventoryEnvelope(BaseModel):
    ok: Literal[True]
    data: Inventory


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 422, 429, 503)
}
