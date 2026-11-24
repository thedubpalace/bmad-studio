"""อ่าน credential ร่วมกับ app-dashboard (AD-4).

อ่านไฟล์ env ของ app-dashboard แบบอ่านอย่างเดียว และดึง **เฉพาะ**
``VEDA_DASHBOARD_USERNAME`` / ``VEDA_DASHBOARD_PASSWORD``. บรรทัดอื่นในไฟล์ถูกข้าม
โดยไม่ parse เก็บ ไม่ log และไม่ส่งต่อ. ห้าม copy ค่าที่อ่านได้ลง repo/.runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

USERNAME_KEY = "VEDA_DASHBOARD_USERNAME"
PASSWORD_KEY = "VEDA_DASHBOARD_PASSWORD"
_WANTED = (USERNAME_KEY, PASSWORD_KEY)
DEFAULT_USERNAME = "admin"  # ค่าเดียวกับ app-dashboard เมื่อไม่ตั้ง username (ไม่มี default password)


class CredentialsUnavailable(Exception):
    """ไฟล์ไม่มี/อ่านไม่ได้ หรือ password ว่าง. ข้อความไม่มีค่า secret."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _extract(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in _WANTED:
            found[key] = _unquote(value)
    return found


def read_credentials(path: Path | None) -> Credentials:
    """อ่าน credential จาก ``path``; raise :class:`CredentialsUnavailable` เมื่อใช้ไม่ได้."""
    if path is None:
        raise CredentialsUnavailable("ไม่ได้ตั้ง BMAD_STUDIO_CREDENTIALS_ENV_FILE")
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise CredentialsUnavailable("ไม่พบไฟล์ที่ BMAD_STUDIO_CREDENTIALS_ENV_FILE ชี้ไว้") from None
    except (OSError, UnicodeDecodeError):
        raise CredentialsUnavailable("อ่านไฟล์ที่ BMAD_STUDIO_CREDENTIALS_ENV_FILE ชี้ไว้ไม่ได้") from None
    values = _extract(text)
    del text
    password = values.get(PASSWORD_KEY, "")
    if not password:
        raise CredentialsUnavailable(f"{PASSWORD_KEY} ว่างหรือไม่มีในไฟล์ credential")
    username = values.get(USERNAME_KEY, "").strip() or DEFAULT_USERNAME
    return Credentials(username=username, password=password)


class CredentialStore:
    """อ่านไฟล์ credential ใหม่เมื่อไฟล์เปลี่ยน (cache ตาม mtime+size) — ไม่ต้อง restart.

    ไฟล์หาย/อ่านไม่ได้ระหว่างรัน = :class:`CredentialsUnavailable` (fail closed, ไม่ใช้ค่าเก่า).
    """

    def __init__(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._stamp: tuple[int, int] | None = None
        self._cached: Credentials | None = None

    def __repr__(self) -> str:
        return "CredentialStore(<redacted>)"

    def current(self) -> Credentials:
        if self._path is None:
            raise CredentialsUnavailable("ไม่ได้ตั้ง BMAD_STUDIO_CREDENTIALS_ENV_FILE")
        try:
            stat = self._path.stat()
        except OSError:
            self._stamp, self._cached = None, None
            raise CredentialsUnavailable("ไม่พบไฟล์ที่ BMAD_STUDIO_CREDENTIALS_ENV_FILE ชี้ไว้") from None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._cached is None or stamp != self._stamp:
            self._stamp, self._cached = None, None
            self._cached = read_credentials(self._path)
            self._stamp = stamp
        return self._cached
