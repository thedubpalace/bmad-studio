"""``POST /api/login``, ``GET /api/session``, ``POST /api/logout`` (AD-4, AD-12)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bmad_studio.api import auth
from bmad_studio.api.envelope import error_response, ok
from bmad_studio.core.credentials import CredentialsUnavailable

router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


def _audit_login(request: Request, username: str, result: str, ip: str, status: int) -> None:
    request.app.state.audit.audit({
        "event": "login", "actor": username[:100], "result": result, "ip": ip, "status": status,
    })


@router.post("/api/login")
def login(body: LoginBody, request: Request) -> JSONResponse:
    state = request.app.state
    ip = auth.client_ip(request)
    username = body.username

    wait = state.rate_limiter.retry_after(ip, username)
    if wait is not None:
        state.audit.security({"event": "login", "status": 429, "code": "rate_limited", "actor": username[:100],
                              "ip": ip, "method": "POST", "path": "/api/login"})
        _safe_audit(request, username, "rate_limited", ip, 429)
        return error_response(429, "rate_limited", "พยายามเข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่",
                              {"retry_after": wait}, headers={"Retry-After": str(wait)})
    try:
        creds = state.credentials.current()
    except CredentialsUnavailable:
        _safe_audit(request, username, "credentials_unavailable", ip, 503)
        return error_response(503, "credentials_unavailable", "ระบบตรวจรหัสผ่านไม่พร้อม ยังเข้าสู่ระบบไม่ได้")

    if not auth.credentials_match(creds, username, body.password):
        state.rate_limiter.failed(ip, username)
        _safe_audit(request, username, "fail", ip, 401)
        state.audit.security({"event": "login", "status": 401, "code": "invalid_credentials",
                              "actor": username[:100], "ip": ip, "method": "POST", "path": "/api/login"})
        return error_response(401, "invalid_credentials", "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    try:
        _audit_login(request, username, "ok", ip, 200)
    except OSError:
        return error_response(503, "audit_unavailable", "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง")
    state.rate_limiter.succeeded(ip, username)
    cookie, session = auth.create_session(state.settings, creds)
    role = auth.role_for(session.username, state.settings, state.credentials)
    response = ok({"authenticated": True, "username": session.username, "role": role, "csrf": session.csrf})
    auth.set_session_cookie(response, state.settings, cookie)
    return response


def _safe_audit(request: Request, username: str, result: str, ip: str, status: int) -> None:
    try:
        _audit_login(request, username, result, ip, status)
    except OSError:
        pass  # การปฏิเสธไม่ต้องรอ audit; ไม่มีอะไรถูกเปลี่ยน


@router.get("/api/session")
def session(request: Request) -> JSONResponse:
    current = auth.read_session(request)
    if current is None:
        return ok({"authenticated": False})
    role = auth.role_for(current.username, request.app.state.settings, request.app.state.credentials)
    return ok({"authenticated": True, "username": current.username, "role": role, "csrf": current.csrf})


@router.post("/api/logout")
def logout(request: Request) -> JSONResponse:
    response = ok({"authenticated": False})
    auth.clear_session_cookie(response, request.app.state.settings)
    return response
