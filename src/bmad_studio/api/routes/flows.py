"""Flow editor API (story 4-1, REQ-046/047, AD-1/AD-12) — อ่านและตรวจ draft เท่านั้น.

การบันทึกไปทาง ``POST /api/changes/preview`` kind ``flow`` (AD-5); route ในไฟล์นี้ไม่เขียนไฟล์ใด.
ทุกค่า (lock, overridden, errors/warnings) มาจาก :mod:`bmad_studio.core.flow.editor`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from bmad_studio.api.envelope import ok
from bmad_studio.api.schemas import (
    ERRORS,
    AgentNameListEnvelope,
    FlowDraftCheckEnvelope,
    FlowEditorEnvelope,
    SkillNameListEnvelope,
)
from bmad_studio.core.catalog import agent_names, skill_list
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


@router.get("/api/agents/names", response_model=AgentNameListEnvelope, responses=ERRORS)
async def list_agent_names(request: Request) -> JSONResponse:
    return ok(await run_in_threadpool(agent_names, _paths(request)))


@router.get("/api/skills", response_model=SkillNameListEnvelope, responses=ERRORS)
async def list_skills(request: Request) -> JSONResponse:
    return ok(await run_in_threadpool(skill_list, _paths(request)))
