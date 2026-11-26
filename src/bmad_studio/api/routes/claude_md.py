"""``GET /api/claude-md/proposals`` (REQ-049, AD-9, story 5-2) — อ่านอย่างเดียว.

สถานะของ block ใน CLAUDE.md (ยังไม่ใช้/ใช้แล้ว/ล้าสมัย) และข้อเสนอแก้ guard ของ agent พร้อม diff.
การใช้ข้อเสนอ CLAUDE.md = ``POST /api/changes/preview`` kind ``claude-md`` (pipeline ปกติ); ข้อเสนอแก้ agent
**ไม่มีทางเขียน** ในแอป (บันทึก agent มาใน Epic 7) — แสดงให้คนแก้เองเท่านั้น.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bmad_studio.api.envelope import ok
from bmad_studio.api.schemas import ERRORS, ClaudeMdProposalsEnvelope
from bmad_studio.core.claude_md import proposals

router = APIRouter()


@router.get("/api/claude-md/proposals", response_model=ClaudeMdProposalsEnvelope, responses=ERRORS)
async def claude_md_proposals(request: Request) -> JSONResponse:
    return ok(await run_in_threadpool(proposals, request.app.state.bmad.paths))
