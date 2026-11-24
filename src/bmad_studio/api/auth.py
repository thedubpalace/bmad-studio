"""Session ของ bmad-studio (AD-4): cookie ลงลายเซ็น, role, rate limit และ IP จริง.

credential มาจากไฟล์ของ app-dashboard (:class:`CredentialStore`) แต่ cookie, secret,
CSRF, rate limit และ audit เป็นของ bmad-studio เอง — cookie ของแอปหนึ่งใช้กับอีกแอปไม่ได้.

รูป cookie: ``username|expiry|csrf|cred_fp|sig`` โดย
``cred_fp = HMAC(secret, username|password)[:16]`` และ ``sig = HMAC-SHA256(secret, ส่วนหน้า)``.
เปลี่ยนรหัสใน app-dashboard แล้ว ``cred_fp`` ไม่ตรง → session เดิมหมดอายุทันที.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from bmad_studio.core.credentials import CredentialsUnavailable, CredentialStore, Credentials
from bmad_studio.core.settings import Settings

COOKIE = "bmad_studio_session"
TTL_SECONDS = 12 * 60 * 60
ADMIN, VIEWER = "admin", "viewer"


@dataclass(frozen=True)
class Session:
    username: str
    expiry: int
    csrf: str


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def credential_fingerprint(secret: str, creds: Credentials) -> str:
    return _sign(secret, f"{creds.username}|{creds.password}")[:16]


def credentials_match(creds: Credentials, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.encode(), creds.username.encode())
    pass_ok = hmac.compare_digest(password.encode(), creds.password.encode())
    return user_ok and pass_ok


def create_session(settings: Settings, creds: Credentials, now: float | None = None) -> tuple[str, Session]:
    secret = settings.session_secret_value()
    expiry = int(now if now is not None else time.time()) + TTL_SECONDS
    csrf = secrets.token_urlsafe(24)
    value = f"{creds.username}|{expiry}|{csrf}|{credential_fingerprint(secret, creds)}"
    return f"{value}|{_sign(secret, value)}", Session(creds.username, expiry, csrf)


def parse_session(raw: str, settings: Settings, store: CredentialStore, now: float | None = None) -> Session | None:
    parts = raw.split("|")
    if len(parts) != 5:
        return None
    username, expiry_raw, csrf, fingerprint, supplied = parts
    secret = settings.session_secret_value()
    if not hmac.compare_digest(supplied, _sign(secret, "|".join(parts[:4]))):
        return None
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return None
    if expiry < (now if now is not None else time.time()):
        return None
    try:
        creds = store.current()
    except CredentialsUnavailable:
        return None  # fail closed
    if username != creds.username or not hmac.compare_digest(fingerprint, credential_fingerprint(secret, creds)):
        return None
    return Session(username, expiry, csrf)


def read_session(request: Request) -> Session | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    return parse_session(raw, request.app.state.settings, request.app.state.credentials)


def role_for(username: str, settings: Settings, store: CredentialStore) -> str:
    """admin เฉพาะผู้อยู่ใน allowlist (ไม่ตั้ง = เฉพาะ VEDA_DASHBOARD_USERNAME); อื่น ๆ = viewer."""
    allow = settings.admin_users()
    if not allow:
        try:
            allow = {store.current().username}
        except CredentialsUnavailable:
            return VIEWER
    return ADMIN if username in allow else VIEWER


def set_session_cookie(response, settings: Settings, value: str) -> None:
    response.set_cookie(COOKIE, value, max_age=TTL_SECONDS, path="/", httponly=True,
                        samesite="strict", secure=settings.cookie_secure)


def clear_session_cookie(response, settings: Settings) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict", secure=settings.cookie_secure)


def _valid_ip(value: str) -> str | None:
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """IP ของผู้ใช้: เชื่อ ``X-Forwarded-For`` เฉพาะเมื่อ peer อยู่ใน ``BMAD_STUDIO_TRUSTED_PROXIES``.

    เดินจาก hop ขวาสุดไปซ้ายข้าม proxy ที่เชื่อถือ แล้วใช้ hop แรกที่ไม่ใช่ proxy.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = request.app.state.settings.trusted_proxy_hosts()
    if peer not in trusted:
        return peer
    header = request.headers.get("x-forwarded-for", "")
    hops = [hop for hop in (_valid_ip(part) for part in header.split(",")) if hop]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return peer


class LoginRateLimiter:
    """นับ login ที่ล้มเหลวต่อ (IP, username) ใน memory; เกินกำหนด = 429 + Retry-After."""

    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = max(1, attempts)
        self.window = max(1, window_seconds)
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        bucket = self._failures.get(key, deque())
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        return bucket

    def retry_after(self, ip: str, username: str, now: float | None = None) -> int | None:
        """วินาทีที่ต้องรอ ถ้าถูกบล็อกอยู่; ``None`` ถ้าลองได้."""
        now = time.time() if now is None else now
        with self._lock:
            bucket = self._recent((ip, username), now)
            if len(bucket) < self.attempts:
                return None
            return max(1, math.ceil(bucket[0] + self.window - now))

    def failed(self, ip: str, username: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            key = (ip, username)
            bucket = self._recent(key, now)
            bucket.append(now)
            self._failures[key] = bucket

    def succeeded(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop((ip, username), None)
