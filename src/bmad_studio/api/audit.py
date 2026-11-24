"""Audit แบบ append-only (JSON lines) ใน ``.runtime/`` ของแอป (AD-4, REQ-038 h).

- ``audit.jsonl`` — login ok/fail และ mutation phase ``requested``/``result``
- ``security.log`` — ทุก request ที่ถูกปฏิเสธ (401/403/429) เป็น JSON line

ห้ามใส่ body, cookie, token หรือ password ลงใน record. การเขียนที่ล้มเหลว raise ``OSError``
ให้ผู้เรียกตัดสิน (mutation ต้องถูกปฏิเสธ 503 ถ้าเขียน ``requested`` ไม่ได้).
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

FORBIDDEN_KEYS = frozenset({"password", "cookie", "token", "csrf", "body", "authorization"})


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AuditLog:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.audit_path = self.runtime_dir / "audit.jsonl"
        self.security_path = self.runtime_dir / "security.log"
        self._lock = threading.Lock()

    def _append(self, path: Path, record: dict[str, object]) -> None:
        clean = {key: value for key, value in record.items() if key.lower() not in FORBIDDEN_KEYS}
        line = json.dumps({"at": utc_now(), **clean}, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()

    def audit(self, record: dict[str, object]) -> None:
        self._append(self.audit_path, record)

    def security(self, record: dict[str, object]) -> bool:
        """บันทึกการปฏิเสธแบบ best effort — การปฏิเสธต้องเกิดขึ้นแม้ log เขียนไม่ได้."""
        try:
            self._append(self.security_path, record)
            return True
        except OSError:
            return False
