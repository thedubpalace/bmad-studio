"""``GET /api/bmad/inventory`` — BMAD ที่ติดตั้งใน veda (REQ-039, AD-10)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bmad_studio.adapters.bmad import BmadUnavailable, list_skills, read_version
from bmad_studio.api.envelope import ok
from bmad_studio.api.schemas import ERRORS, ConfigViewEnvelope, InventoryEnvelope
from bmad_studio.core.config_model import config_view
from starlette.concurrency import run_in_threadpool

router = APIRouter()


def _agents(config: dict) -> list[dict[str, object]]:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return []
    items = []
    for agent_id, raw in agents.items():
        info = raw if isinstance(raw, dict) else {}
        items.append({
            "id": str(agent_id),
            **{key: (str(info[key]) if info.get(key) is not None else None)
               for key in ("name", "title", "module", "description")},
        })
    return items


@router.get("/api/bmad/inventory", response_model=InventoryEnvelope, responses=ERRORS)
def inventory(request: Request) -> JSONResponse:
    scripts = request.app.state.bmad
    paths = scripts.paths
    skills = [skill.__dict__ for skill in list_skills(paths)]
    try:
        config = scripts.resolve_config()
        core = config.get("core") if isinstance(config.get("core"), dict) else {}
        resolved = {"ok": True, "agents": _agents(config), "core": core, "error": None}
    except BmadUnavailable as exc:
        resolved = {"ok": False, "agents": [], "core": {},
                    "error": {"code": exc.code, "error": exc.message_th, "reason": exc.reason}}
    return ok({
        "version": read_version(paths),
        "skills": skills,
        "config": resolved,
        "tools": {"uv": scripts.uv() is not None, "resolve_script": scripts.resolve_config_script.is_file()},
    })


@router.get("/api/bmad/config", response_model=ConfigViewEnvelope, responses=ERRORS)
async def bmad_config(request: Request) -> JSONResponse:
    """config 3 ชั้น (REQ-040): base/effective จาก resolve_config.py เท่านั้น (AD-10)."""
    return ok(await run_in_threadpool(config_view, request.app.state.bmad))
