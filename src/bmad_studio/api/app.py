"""FastAPI application ของ bmad-studio.

``uvicorn bmad_studio.api.app:app`` จะสร้าง app ตอนเข้าถึง attribute ``app`` ครั้งแรก
(PEP 562) หลังตรวจการตั้งค่าและ host แล้ว — ถ้าไม่พร้อม process จบด้วย exit code 2
ก่อน bind (REQ-038 a/i, AD-4). test สร้าง app ผ่าน :func:`create_app` โดยตรง.
"""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bmad_studio.core.errors import StartupError, StudioError
from bmad_studio.core.settings import DEFAULT_HOST, Settings, check_host, check_startup, get_settings

EXIT_STARTUP = 2

_HTTP_CODES = {
    404: ("not_found", "ไม่พบสิ่งที่ร้องขอ"),
    405: ("method_not_allowed", "ไม่รองรับ method นี้"),
}


def error_response(status: int, code: str, message_th: str, details: object | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, object] = {"ok": False, "code": code, "error": message_th}
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})})


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StudioError)
    async def _studio_error(_request: Request, exc: StudioError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message_th, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "คำขอไม่สำเร็จ"))
        return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(part) for part in err.get("loc", ())) for err in exc.errors()]
        return error_response(422, "invalid_request", "ข้อมูลที่ส่งมาไม่ถูกต้อง", {"fields": fields})

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, _exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", "เกิดข้อผิดพลาดภายในระบบ")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    check_startup(settings)
    app = FastAPI(title="bmad-studio", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    _install_error_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


def requested_host(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> str:
    """host ที่ uvicorn จะ bind ตาม command line (``--host``) หรือ ``UVICORN_HOST``."""
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    host = environ.get("UVICORN_HOST") or DEFAULT_HOST
    for index, arg in enumerate(argv):
        if arg == "--host" and index + 1 < len(argv):
            host = argv[index + 1]
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
    return host


def build_checked_app() -> FastAPI:
    try:
        check_host(requested_host())
        return create_app()
    except StartupError as exc:
        print("bmad-studio ไม่ start: การตั้งค่าไม่พร้อม", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(EXIT_STARTUP) from None


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    global _app
    if name == "app":
        if _app is None:
            _app = build_checked_app()
        return _app
    raise AttributeError(name)
