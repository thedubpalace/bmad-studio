"""``POST /api/changes/preview`` และ ``POST /api/changes/commit`` (REQ-043, AD-5, AD-12).

route เป็นแค่ทางเข้า: ตรวจรูป body แล้วส่งให้ :class:`bmad_studio.core.writes.WritePipeline`.
access guard บังคับ session + Origin + CSRF + ``studio.write`` + audit ``requested`` มาก่อนแล้ว.
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bmad_studio.api import auth
from bmad_studio.api.envelope import ok
from bmad_studio.api.schemas import CHANGE_ERRORS, ChangeCommitEnvelope, ChangePreviewEnvelope
from bmad_studio.core.errors import StudioError
from bmad_studio.core.writes import ChangeRequest

router = APIRouter()

_SHA = r"^[0-9a-f]{64}$"


class ChangeRequestBody(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=400)
    payload: dict[str, Any] = {}
    base_sha256: str | None = Field(default=None, pattern=_SHA)


class CommitBody(BaseModel):
    preview_id: str = Field(min_length=1, max_length=64)
    confirm_token: str = Field(min_length=1, max_length=200)
    typed_confirmation: str | None = Field(default=None, max_length=400)


def session_fingerprint(session: auth.Session) -> str:
    """ผูก preview กับ session นี้ (csrf สุ่มใหม่ทุก login)."""
    return hashlib.sha256(f"{session.username}|{session.expiry}|{session.csrf}".encode("utf-8")).hexdigest()


def _pipeline(request: Request):
    pipeline = request.app.state.writes
    if pipeline is None:
        raise StudioError("write_unavailable", "ระบบเขียนไฟล์ไม่พร้อม", 503)
    return pipeline


@router.post("/api/changes/preview", response_model=ChangePreviewEnvelope, responses=CHANGE_ERRORS)
def preview(body: ChangeRequestBody, request: Request) -> JSONResponse:
    session: auth.Session = request.state.session
    change = ChangeRequest(body.kind, body.target, body.payload, body.base_sha256)
    return ok(_pipeline(request).preview(change, session_fingerprint(session), session.username))


@router.post("/api/changes/commit", response_model=ChangeCommitEnvelope, responses=CHANGE_ERRORS)
def commit(body: CommitBody, request: Request) -> JSONResponse:
    session: auth.Session = request.state.session
    audit = request.app.state.audit

    def record(entry: dict[str, Any]) -> None:
        audit.audit({**entry, "role": request.state.role, "ip": auth.client_ip(request)})

    result = _pipeline(request).commit(body.preview_id, body.confirm_token, session_fingerprint(session),
                                       session.username, body.typed_confirmation, audit=record)
    return ok(result)
