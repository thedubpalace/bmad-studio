"""ตาราง access ของ bmad-studio ที่เดียว (AD-3, port จาก app-dashboard ``services/access_policy.py``).

ทุก request ถูกตรวจกับ :data:`RULES` ก่อนถึง router:

- rule ``public`` ไม่ต้องมีอะไร (shell ที่ไม่มีข้อมูล, login, session probe, ``/health``)
- rule อื่นต้องมี session ที่ลงลายเซ็นถูก (401); mutation ต้องมี ``Origin`` ที่ตรงและ ``X-CSRF-Token`` (403)
- role ต้องมี permission ของ rule (403 ``forbidden``)
- route ที่ไม่อยู่ในตาราง = 403 ``not_allowed`` แม้เป็น admin — route ใหม่จึงปิดอยู่จนกว่าจะเพิ่มที่นี่
  และ ``tests/test_access_policy.py`` fail ถ้า route ที่ลงทะเบียนไม่มี rule
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from starlette.routing import compile_path

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

PUBLIC = "public"
STUDIO_READ = "studio.read"     # อ่าน inventory/flow/agent/config/สถานะ git
STUDIO_WRITE = "studio.write"   # preview/commit การเขียนไฟล์, revert, git commit
SESSION_SELF = "session.self"   # logout

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({SESSION_SELF}),
    "admin": frozenset({STUDIO_READ, STUDIO_WRITE, SESSION_SELF}),
}


@dataclass(frozen=True)
class Rule:
    method: str
    template: str
    permission: str
    regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regex", compile_path(self.template)[0])

    @property
    def mutation(self) -> bool:
        return self.method not in SAFE_METHODS


def _rules(permission: str, method: str, *templates: str) -> list[Rule]:
    return [Rule(method, template, permission) for template in templates]


RULES: list[Rule] = [
    # หน้า shell ไม่มีข้อมูล; แสดงฟอร์ม login จนกว่า /api/session จะบอกว่ามี session
    *_rules(PUBLIC, "GET", "/", "/login"),
    *_rules(PUBLIC, "POST", "/api/login"),
    *_rules(PUBLIC, "GET", "/api/session"),
    # health probe ของ monitor.ps1 — ตอบ {"ok": true} เท่านั้น
    *_rules(PUBLIC, "GET", "/health"),
    *_rules(SESSION_SELF, "POST", "/api/logout"),
]


def match(method: str, path: str) -> Rule | None:
    """rule ของ request นี้ หรือ ``None`` (deny). HEAD ตอบแบบ GET."""
    lookup = "GET" if method == "HEAD" else method
    for rule in RULES:
        if rule.method == lookup and rule.regex.match(path):
            return rule
    return None


def is_static(method: str, path: str) -> bool:
    """ไฟล์ static ของ SPA (``/assets/*``) — public, ไม่มีข้อมูล."""
    return method in {"GET", "HEAD"} and path.startswith("/assets/") and ".." not in path and "\\" not in path


def allowed(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
