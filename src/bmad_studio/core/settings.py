"""การตั้งค่าทั้งหมดของ bmad-studio โหลดจาก ``.env`` ที่ root ของ repo ที่เดียว (REQ-052 a, AD-2).

``VEDA_ROOT`` และ ``BMAD_STUDIO_*`` อ่านที่นี่เท่านั้น; module อื่นรับ :class:`Settings`
ผ่าน :func:`get_settings` ไม่อ่าน ``os.environ`` เอง. credential ไม่อยู่ใน ``.env`` นี้
(อ่านผ่าน :mod:`bmad_studio.core.credentials`, AD-4).
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from bmad_studio.core.claude_md import DEFAULT_RESOLVER_PROJECT, check_resolver_project
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bmad_studio.core.credentials import CredentialsUnavailable, read_credentials
from bmad_studio.core.errors import StartupError

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="BMAD_STUDIO_",
        extra="ignore",
        populate_by_name=True,
    )

    veda_root: Path | None = Field(default=None, validation_alias=AliasChoices("VEDA_ROOT", "veda_root"))
    session_secret: SecretStr | None = None
    credentials_env_file: Path | None = None
    admin_allowlist: str = ""
    trusted_proxies: str = ""
    login_rate_attempts: int = 5
    login_rate_window_seconds: int = 900
    cookie_secure: bool = False
    review_url: str = "https://bridgenbrain.duckdns.org/review"
    #: repo ของ bmad-studio ในคำสั่ง resolver ที่เขียนลง CLAUDE.md/agent (review 5-1 N4) — ที่เดียว ไม่เดาจาก __file__
    resolver_project: str = DEFAULT_RESOLVER_PROJECT

    @field_validator("resolver_project")
    @classmethod
    def _resolver_project_safe(cls, value: str) -> str:
        return check_resolver_project(value)
    runtime_dir: Path = REPO_ROOT / ".runtime"
    web_dist: Path = REPO_ROOT / "web" / "dist"
    bmad_script_timeout_seconds: float = 20.0
    # kind ``text`` ของ write pipeline (เขียนทั้งไฟล์) — สำหรับ test/dev เท่านั้น (AD-5)
    enable_text_kind: bool = False

    def session_secret_value(self) -> str:
        value = self.session_secret
        if isinstance(value, SecretStr):
            return value.get_secret_value()
        return value or ""

    def admin_users(self) -> set[str]:
        return {item.strip() for item in self.admin_allowlist.split(",") if item.strip()}

    def trusted_proxy_hosts(self) -> set[str]:
        return {item.strip() for item in self.trusted_proxies.split(",") if item.strip()}


def is_loopback(host: str) -> bool:
    host = host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_startup(settings: Settings) -> None:
    """ตรวจว่าพร้อม start; raise :class:`StartupError` พร้อมรายการสิ่งที่ขาด (ไม่มีค่า secret)."""
    problems: list[str] = []
    if len(settings.session_secret_value()) < 32:
        problems.append("BMAD_STUDIO_SESSION_SECRET ไม่ได้ตั้งหรือสั้นกว่า 32 ตัวอักษร (สุ่มค่าใหม่ใน .env)")
    root = settings.veda_root
    if root is None:
        problems.append("VEDA_ROOT ไม่ได้ตั้งใน .env")
    elif not (Path(root) / "_bmad").is_dir():
        problems.append("VEDA_ROOT ไม่ได้ชี้ไปโฟลเดอร์ที่มี _bmad/")
    try:
        read_credentials(settings.credentials_env_file)
    except CredentialsUnavailable as exc:
        problems.append(str(exc))
    if problems:
        raise StartupError(problems)


def check_host(host: str) -> None:
    if not is_loopback(host):
        raise StartupError([f"host ต้องเป็น loopback (127.0.0.1) เท่านั้น ได้รับ {host!r}"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
