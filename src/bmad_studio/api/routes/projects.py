"""``GET /api/projects`` และ ``GET /api/projects/{name}/flow-status`` (REQ-050, AD-1, AD-12) — อ่านอย่างเดียว.

ทุกค่า (สถานะต่อขั้น, ``next``, ``locked``/``lockReason``, ``skipReason``, ``overridden``) มาจาก
:mod:`bmad_studio.core.flow.resolve` ตัวเดียวกับ CLI ``flow resolve`` — router แค่ส่งต่อ.
ไม่มี route ใดในไฟล์นี้ที่เขียนไฟล์ (การแก้ flow มาใน epic 4 ผ่าน write pipeline).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bmad_studio.api.envelope import error_response, ok
from bmad_studio.api.schemas import ERRORS, FlowStatusEnvelope, ProjectListEnvelope
from bmad_studio.core.flow.merge import FlowInvalid
from bmad_studio.core.flow.resolve import overview, resolve_project
from bmad_studio.core.paths import VedaPaths

router = APIRouter()
PROJECT_ERRORS = {**ERRORS, 404: ERRORS[403]}


def _paths(request: Request) -> VedaPaths:
    return request.app.state.bmad.paths


@router.get("/api/projects", response_model=ProjectListEnvelope, responses=ERRORS)
async def list_projects(request: Request) -> JSONResponse:
    return ok(await run_in_threadpool(overview, _paths(request)))


@router.get("/api/projects/{name}/flow-status", response_model=FlowStatusEnvelope, responses=PROJECT_ERRORS)
async def flow_status(request: Request, name: str,
                      track: Literal["full", "quick"] = Query("full")) -> JSONResponse:
    try:
        result = await run_in_threadpool(resolve_project, _paths(request), name, track)
    except FlowInvalid as exc:
        return error_response(422, "flow_invalid", "flow ของโปรเจกต์นี้ผิดกฎ — แก้ไฟล์ flow ก่อน",
                              {"source": exc.source, "issues": exc.issues})
    return ok({**result, "reviewUrl": request.app.state.settings.review_url or None})
