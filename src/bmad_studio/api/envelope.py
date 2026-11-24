"""รูป response เดียวของ ``/api`` (AD-12): ``{"ok": true, "data": …}`` / ``{"ok": false, "code", "error"}``."""
from __future__ import annotations

from fastapi.responses import JSONResponse

NO_STORE = {"Cache-Control": "no-store"}


def ok(data: object, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status, headers=NO_STORE)


def error_response(status: int, code: str, message_th: str, details: object | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, object] = {"ok": False, "code": code, "error": message_th}
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status, headers={**NO_STORE, **(headers or {})})
