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


class DiffLine(BaseModel):
    type: Literal["context", "add", "del"]
    old: int | None = None
    new: int | None = None
    text: str
    spans: list[list[int]] = []


class DiffHunk(BaseModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine]


class Diff(BaseModel):
    hunks: list[DiffHunk]
    added: int
    removed: int
    truncated: bool = False  # true = ใหญ่เกินจะแสดงรายบรรทัด (hunks ว่าง, มีแค่ added/removed)
    old_eof_newline: bool = True  # false = ไม่มี newline ท้ายไฟล์ (UI แสดงป้าย)
    new_eof_newline: bool = True


class Issue(BaseModel):
    code: str
    error: str


class ChangePreview(BaseModel):
    preview_id: str
    path: str
    kind: str
    op: Literal["create", "modify", "delete"]
    impact: Literal["normal", "high"]
    confirm_phrase: str | None = None
    base_sha256: str | None = None
    proposed_sha256: str | None = None  # None = ย้ายไฟล์ไป backup
    diff: Diff
    warnings: list[Issue] = []
    errors: list[Issue] = []
    confirm_token: str
    expires_at: str


class ChangePreviewEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangePreview


class ChangeCommit(BaseModel):
    change_id: str
    path: str
    op: Literal["create", "modify", "delete"]
    sha256: str | None = None
    backup: str
    at: str
    replayed: bool


class ChangeCommitEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangeCommit


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 422, 429, 503)
}


class ChangeEntry(BaseModel):
    change_id: str
    at: str
    path: str
    kind: str
    op: Literal["create", "modify", "delete"]
    added: int
    removed: int
    impact: Literal["normal", "high"] = "normal"
    reverts: str | None = None
    new_sha256: str | None = None
    current_sha256: str | None = None
    changed_since: bool


class ChangeListEnvelope(BaseModel):
    ok: Literal[True]
    data: list[ChangeEntry]


class ChangeDiff(BaseModel):
    change_id: str
    path: str
    op: Literal["create", "modify", "delete"]
    kind: str
    at: str
    diff: Diff


class ChangeDiffEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangeDiff


CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (400, 401, 403, 409, 411, 413, 422, 429, 500, 503)
}
