"""เสิร์ฟ SPA ที่ build แล้ว (``web/dist``): ``/assets/*`` เป็น static และทุก path อื่นที่ไม่ใช่ ``/api`` ได้ shell เดียว.

shell ไม่มีข้อมูล — หน้าจอขอ ``/api/session`` เองแล้วแสดงฟอร์ม login ถ้ายังไม่มี session (AD-3).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from bmad_studio.api.envelope import error_response


NOT_BUILT = """<!doctype html><html lang="th"><meta charset="utf-8"><title>bmad-studio</title>
<body style="background:#1b2230;color:#eef2f8;font-family:system-ui,'Segoe UI',sans-serif;padding:32px">
<p>ยังไม่ได้ build หน้าเว็บ — รัน <code>npm --prefix web run build</code> หรือ start ผ่าน start-service.ps1</p></body></html>"""


def mount_assets(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


def shell(path: str, request: Request) -> Response:
    if path == "api" or path.startswith("api/"):
        return error_response(404, "not_found", "ไม่พบสิ่งที่ร้องขอ")
    index = Path(request.app.state.settings.web_dist) / "index.html"
    if not index.is_file():
        return HTMLResponse(NOT_BUILT, status_code=503, headers={"Cache-Control": "no-store"})
    return FileResponse(index, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def install_shell(app: FastAPI) -> None:
    """ต้องเรียกหลัง route อื่นทั้งหมด: catch-all ของ SPA shell (เฉพาะ GET)."""
    app.add_api_route("/{path:path}", shell, methods=["GET"], include_in_schema=False)
