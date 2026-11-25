"""FastAPI application ของ bmad-studio.

``uvicorn bmad_studio.api.app:app`` จะสร้าง app ตอนเข้าถึง attribute ``app`` ครั้งแรก
(PEP 562) หลังตรวจการตั้งค่าและ host แล้ว — ถ้าไม่พร้อม process จบด้วย exit code 2
ก่อน bind (REQ-038 a/i, AD-4). test สร้าง app ผ่าน :func:`create_app` โดยตรง.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bmad_studio.api import access_guard
from bmad_studio.api.audit import AuditLog
from bmad_studio.api.auth import LoginRateLimiter
from bmad_studio.api.envelope import error_response
from bmad_studio import __version__
from bmad_studio.adapters.bmad import BmadScripts
from bmad_studio.adapters.state import RuntimeState
from bmad_studio.api.routes import bmad as bmad_routes
from bmad_studio.api.routes import changes as changes_routes
from bmad_studio.api.routes import projects as projects_routes
from bmad_studio.api.routes import session as session_routes
from bmad_studio.api.routes import web as web_routes
from bmad_studio.core.credentials import CredentialStore
from bmad_studio.core.errors import StartupError, StudioError
from bmad_studio.core.path_guard import PathGuard
from bmad_studio.core.flow.files import file_validators
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.writes import RevertKind, TextKind, WritePipeline
from bmad_studio.core.settings import DEFAULT_HOST, Settings, check_host, check_startup, get_settings

EXIT_STARTUP = 2

_HTTP_CODES = {
    404: ("not_found", "ไม่พบสิ่งที่ร้องขอ"),
    405: ("method_not_allowed", "ไม่รองรับ method นี้"),
}


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


# body ของ /api ใหญ่สุด: ข้อความ 2 MiB เป็น JSON (escape ได้ ~6 เท่ากรณีแย่) + ส่วนอื่น
MAX_API_BODY = 13 * 1024 * 1024


def _install_body_limit(app: FastAPI) -> None:
    """ปฏิเสธ body ใหญ่เกินก่อน parse JSON (กัน memory หมดจาก request เดียว)."""

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            length = request.headers.get("content-length")
            if length is None:
                if request.headers.get("transfer-encoding"):
                    return error_response(411, "length_required", "ต้องระบุขนาดของคำขอ")
            else:
                try:
                    size = int(length)
                except ValueError:
                    return error_response(400, "invalid_request", "ข้อมูลที่ส่งมาไม่ถูกต้อง")
                if size < 0 or size > MAX_API_BODY:
                    return error_response(413, "request_too_large", "คำขอใหญ่เกินไป")
        return await call_next(request)


def _build_pipeline(settings: Settings) -> WritePipeline:
    state = RuntimeState(settings.runtime_dir)
    root = Path(settings.veda_root or ".")
    kinds: dict = {"revert": RevertKind(state)}
    if settings.enable_text_kind:
        kinds["text"] = TextKind()
    return WritePipeline(PathGuard(root), state, settings.session_secret_value(), kinds,
                         validators=file_validators(VedaPaths(root)))


def create_app(settings: Settings | None = None, *, check: bool = True) -> FastAPI:
    """สร้าง app; ``check=False`` ใช้เฉพาะการ export OpenAPI (ไม่รับ request จริง)."""
    settings = settings or get_settings()
    if check:
        check_startup(settings)
    app = FastAPI(title="bmad-studio", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.credentials = CredentialStore(settings.credentials_env_file)
    app.state.rate_limiter = LoginRateLimiter(settings.login_rate_attempts, settings.login_rate_window_seconds)
    app.state.audit = AuditLog(settings.runtime_dir)
    app.state.bmad = BmadScripts(VedaPaths(Path(settings.veda_root or ".")), settings.bmad_script_timeout_seconds)
    app.state.writes = _build_pipeline(settings) if check else None
    _install_error_handlers(app)
    access_guard.install(app)
    _install_body_limit(app)  # เพิ่มทีหลัง = ทำงานก่อน access guard

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(session_routes.router)
    app.include_router(bmad_routes.router)
    app.include_router(changes_routes.router)
    app.include_router(projects_routes.router)
    web_routes.mount_assets(app, settings.web_dist)
    web_routes.install_shell(app)  # ต้องอยู่ท้ายสุด: catch-all ของ SPA shell
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
