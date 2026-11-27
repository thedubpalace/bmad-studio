"""Flow editor API (story 4-1, REQ-046/047, AD-1/AD-12) — อ่านและตรวจ draft เท่านั้น.

การบันทึกไปทาง ``POST /api/changes/preview`` kind ``flow`` (AD-5); route ในไฟล์นี้ไม่เขียนไฟล์ใด.
ทุกค่า (lock, overridden, errors/warnings) มาจาก :mod:`bmad_studio.core.flow.editor`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from bmad_studio.api.envelope import ok
from bmad_studio.api.schemas import (
    ERRORS,
    AgentNameListEnvelope,
    FlowDraftCheckEnvelope,
    FlowEditorEnvelope,
    SkillDetailEnvelope,
    SkillOverrideEnvelope,
    SkillNameListEnvelope,
    SkillReferencesEnvelope,
)
from bmad_studio.core.catalog import agent_names
from bmad_studio.core.override_model import override_view
from bmad_studio.core.skill_catalog import missing_skills, skill_catalog, skill_detail
from bmad_studio.core.flow.default_editor import check_default_draft, default_view
from bmad_studio.core.flow.editor import check_draft, draft_result, editor_view
from bmad_studio.core.flow.status import find_project
from bmad_studio.core.paths import VedaPaths

router = APIRouter()
FLOW_ERRORS = {**ERRORS, 404: ERRORS[403]}


class FlowDraftBody(BaseModel):
    flow: dict[str, Any]


def _paths(request: Request) -> VedaPaths:
    return request.app.state.bmad.paths


@router.get("/api/flows/{project}", response_model=FlowEditorEnvelope, responses=FLOW_ERRORS)
async def flow_editor(request: Request, project: str) -> JSONResponse:
    return ok(await run_in_threadpool(editor_view, _paths(request), project))


def _validate(paths: VedaPaths, project: str, draft: dict[str, Any]) -> dict[str, Any]:
    return draft_result(check_draft(paths, find_project(paths, project), draft))


@router.post("/api/flows/{project}/validate", response_model=FlowDraftCheckEnvelope, responses=FLOW_ERRORS)
async def validate_draft(request: Request, project: str, body: FlowDraftBody) -> JSONResponse:
    return ok(await run_in_threadpool(_validate, _paths(request), project, body.flow))


@router.get("/api/flow-default", response_model=FlowEditorEnvelope, responses=ERRORS)
async def default_flow_editor(request: Request) -> JSONResponse:
    """flow เริ่มต้น (story 4-3) — แยก path จาก ``/api/flows/<project>`` เพื่อไม่ชนกับโปรเจกต์ชื่อ default."""
    return ok(await run_in_threadpool(default_view, _paths(request)))


@router.post("/api/flow-default/validate", response_model=FlowDraftCheckEnvelope, responses=ERRORS)
async def validate_default_draft(request: Request, body: FlowDraftBody) -> JSONResponse:
    return ok(await run_in_threadpool(check_default_draft, _paths(request), body.flow))


@router.get("/api/agents/names", response_model=AgentNameListEnvelope, responses=ERRORS)
async def list_agent_names(request: Request) -> JSONResponse:
    return ok(await run_in_threadpool(agent_names, _paths(request)))


@router.get("/api/skills", response_model=SkillNameListEnvelope, responses=ERRORS)
async def list_skills(request: Request, usage: int = Query(default=1, ge=0, le=1)) -> JSONResponse:
    """catalog ของ skill (4-1 + 6-2: กลุ่ม, override, ที่ใช้ใน flow) — field เดิม id/description คงรูป.
    ``?usage=0`` = ไม่ merge flow ทุกโปรเจกต์ (ตัวเลือก skill ของ flow editor)."""
    return ok(await run_in_threadpool(lambda: skill_catalog(_paths(request), with_usage=bool(usage))))


@router.get("/api/skill-references", response_model=SkillReferencesEnvelope, responses=ERRORS)
async def skill_references(request: Request) -> JSONResponse:
    """skill ที่ flow อ้างแต่ไม่มีโฟลเดอร์ (REQ-051 a)."""
    return ok(await run_in_threadpool(missing_skills, _paths(request)))


@router.get("/api/skills/{skill}", response_model=SkillDetailEnvelope, responses=FLOW_ERRORS)
async def get_skill(request: Request, skill: str) -> JSONResponse:
    """รายละเอียด skill + SKILL.md อ่านอย่างเดียว (ไม่มี API แก้/ลบ SKILL.md — REQ-051 b)."""
    return ok(await run_in_threadpool(skill_detail, _paths(request), skill))


@router.get("/api/skills/{skill}/override", response_model=SkillOverrideEnvelope, responses=FLOW_ERRORS)
async def get_skill_override(request: Request, skill: str) -> JSONResponse:
    """ฟิลด์ที่ปรับได้จาก customize.toml + ค่า team/user + effective จาก resolve_customization.py (REQ-041)."""
    return ok(await run_in_threadpool(override_view, request.app.state.bmad, skill))
