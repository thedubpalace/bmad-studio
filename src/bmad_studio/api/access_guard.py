"""Middleware ที่บังคับ :mod:`bmad_studio.api.access_policy` กับทุก request (AD-3, AD-4).

ลำดับการตรวจ:
1. ไฟล์ static และ rule ``public`` ผ่าน
2. ไม่มี rule ของ (method, path) → 403 ``not_allowed`` (deny by default)
3. ไม่มี session ที่ถูกต้อง → 401 (``session_expired`` ถ้าส่ง cookie มา)
4. mutation: ``Origin`` ไม่ตรง host → 403 ``origin_mismatch``; ``X-CSRF-Token`` ขาด/ผิด → 403 ``csrf_invalid``
5. role ไม่มี permission → 403 ``forbidden``
6. mutation: เขียน audit ``requested`` (ไม่ได้ → 503 ``audit_unavailable`` และไม่เรียก handler),
   เรียก handler แล้วเขียน audit ``result``

ทุกการปฏิเสธ (401/403) ถูกบันทึกใน ``.runtime/security.log``; ทุก response ได้ security headers.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bmad_studio.api import access_policy, auth
from bmad_studio.api.envelope import error_response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
        "object-src 'none'"
    ),
}

MESSAGES = {
    "unauthenticated": "กรุณาเข้าสู่ระบบก่อน",
    "session_expired": "เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่",
    "csrf_invalid": "เซสชันไม่ตรงกัน โหลดหน้าใหม่",
    "origin_mismatch": "ปฏิเสธคำขอจากต้นทางอื่น",
    "forbidden": "บัญชีนี้ไม่มีสิทธิ์ทำรายการนี้",
    "not_allowed": "ไม่อนุญาตให้เข้าถึงเส้นทางนี้",
}


def _deny(request: Request, status: int, code: str, permission: str | None, username: str | None = None) -> JSONResponse:
    request.app.state.audit.security({
        "event": "denied", "status": status, "code": code, "permission": permission, "method": request.method,
        "path": request.url.path, "actor": username, "ip": auth.client_ip(request),
    })
    return error_response(status, code, MESSAGES[code])


def _allowed_hosts(request: Request) -> set[str]:
    hosts = {request.headers.get("host", "")}
    peer = request.client.host if request.client else ""
    if peer in request.app.state.settings.trusted_proxy_hosts():
        forwarded = request.headers.get("x-forwarded-host", "")
        hosts.update(part.strip() for part in forwarded.split(","))
    return {host.lower() for host in hosts if host}


def origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # same-origin fetch ของ browser เก่า / CLI — ยังต้องมี CSRF token
    netloc = urlparse(origin).netloc.lower()
    return bool(netloc) and netloc in _allowed_hosts(request)


def csrf_ok(request: Request, session: auth.Session) -> bool:
    supplied = request.headers.get("x-csrf-token", "")
    return bool(supplied) and hmac.compare_digest(supplied.encode(), session.csrf.encode())


def _session_ref(request: Request) -> str:
    raw = request.cookies.get(auth.COOKIE, "")
    return hashlib.sha256(raw.encode()).hexdigest()[:12] if raw else ""


async def guard(request: Request, call_next):
    method, path = request.method.upper(), request.url.path
    if access_policy.is_static(method, path):
        return await call_next(request)
    rule = access_policy.match(method, path)
    if rule is None:
        return _deny(request, 403, "not_allowed", None)
    if rule.permission == access_policy.PUBLIC:
        return await call_next(request)

    session = auth.read_session(request)
    if session is None:
        code = "session_expired" if request.cookies.get(auth.COOKIE) else "unauthenticated"
        return _deny(request, 401, code, rule.permission)
    username = session.username
    if rule.mutation:
        if not origin_ok(request):
            return _deny(request, 403, "origin_mismatch", rule.permission, username)
        if not csrf_ok(request, session):
            return _deny(request, 403, "csrf_invalid", rule.permission, username)
    state = request.app.state
    role = auth.role_for(username, state.settings, state.credentials)
    if not access_policy.allowed(role, rule.permission):
        return _deny(request, 403, "forbidden", rule.permission, username)
    request.state.session = session
    request.state.role = role
    if not rule.mutation:
        return await call_next(request)

    record = {
        "event": "mutation", "id": uuid.uuid4().hex, "actor": username, "role": role, "method": method,
        "path": path, "permission": rule.permission, "ip": auth.client_ip(request), "session": _session_ref(request),
    }
    try:
        state.audit.audit({**record, "phase": "requested"})
    except OSError:
        return error_response(503, "audit_unavailable", "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง")
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        try:
            state.audit.audit({**record, "phase": "result", "status": status})
        except OSError:
            pass


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def access_and_headers(request: Request, call_next):
        response = await guard(request, call_next)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
