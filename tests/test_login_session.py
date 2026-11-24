"""Story 1.2 — login ด้วย credential ของ app-dashboard, session, allowlist, rate limit, audit."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api import auth
from bmad_studio.api.app import create_app
from bmad_studio.core.credentials import Credentials, CredentialStore
from bmad_studio.core.settings import Settings

from .conftest import OTHER_SECRET_VALUE, PASSWORD, SECRET, USERNAME


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _login(client: TestClient, username: str = USERNAME, password: str = PASSWORD, **kw):
    return client.post("/api/login", json={"username": username, "password": password}, **kw)


def _rewrite_password(path: Path, new_password: str) -> None:
    before = path.stat().st_mtime_ns
    path.write_text(f"VEDA_DASHBOARD_USERNAME={USERNAME}\nVEDA_DASHBOARD_PASSWORD={new_password}\n", encoding="utf-8")
    if path.stat().st_mtime_ns == before:  # coarse mtime on some filesystems
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


def _audit_lines(settings: Settings) -> list[dict]:
    path = settings.runtime_dir / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_login_sets_signed_cookie_and_returns_csrf(settings: Settings) -> None:
    client = _client(settings)
    response = _login(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["username"] == USERNAME and data["role"] == "admin" and len(data["csrf"]) >= 20
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("bmad_studio_session=")
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie.replace("Strict", "strict")
    assert "Secure" not in set_cookie
    assert len(client.cookies.get(auth.COOKIE).strip('"').split("|")) == 5

    session = client.get("/api/session").json()["data"]
    assert session == {"authenticated": True, "username": USERNAME, "role": "admin", "csrf": data["csrf"],
                       "review_url": settings.review_url}


def test_cookie_secure_flag_when_configured(settings: Settings) -> None:
    client = _client(settings.model_copy(update={"cookie_secure": True}))
    assert "Secure" in _login(client).headers["set-cookie"]


def test_session_without_cookie_is_unauthenticated(settings: Settings) -> None:
    assert _client(settings).get("/api/session").json() == {"ok": True, "data": {"authenticated": False}}


def test_wrong_password_is_401_with_thai_message(settings: Settings) -> None:
    response = _login(_client(settings), password="nope")
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False and body["code"] == "invalid_credentials"
    assert body["error"] == "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    assert "set-cookie" not in response.headers


def test_logout_clears_cookie(settings: Settings) -> None:
    client = _client(settings)
    csrf = _login(client).json()["data"]["csrf"]
    response = client.post("/api/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert 'bmad_studio_session=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/session").json()["data"] == {"authenticated": False}


def test_password_change_applies_without_restart(settings: Settings, credentials_file: Path) -> None:
    client = _client(settings)
    _login(client)
    old_cookie = client.cookies.get(auth.COOKIE)
    _rewrite_password(credentials_file, "brand-new-password")

    # the old session is no longer valid (credential fingerprint changed)
    assert client.get("/api/session").json()["data"] == {"authenticated": False}
    store = CredentialStore(credentials_file)
    assert auth.parse_session(old_cookie.strip('"'), settings, store) is None
    # old password refused, new password accepted
    fresh = _client(settings)
    assert _login(fresh).status_code == 401
    assert _login(fresh, password="brand-new-password").status_code == 200


def test_missing_credentials_file_at_runtime_fails_closed(settings: Settings, credentials_file: Path) -> None:
    client = _client(settings)
    _login(client)
    credentials_file.unlink()
    response = _login(client)
    assert response.status_code == 503 and response.json()["code"] == "credentials_unavailable"
    assert client.get("/api/session").json()["data"] == {"authenticated": False}


def test_app_dashboard_cookie_is_not_accepted(settings: Settings) -> None:
    client = _client(settings)
    expiry = int(time.time()) + 3600
    client.cookies.set("veda_dashboard_session", f"{USERNAME}|{expiry}|csrf|{'0' * 64}")
    assert client.get("/api/session").json()["data"] == {"authenticated": False}
    # same format as ours but signed with another app's secret
    other = settings.model_copy(update={"session_secret": "x" * 48})
    forged, _ = auth.create_session(other, Credentials(USERNAME, PASSWORD))
    client.cookies.set(auth.COOKIE, forged)
    assert client.get("/api/session").json()["data"] == {"authenticated": False}


def test_our_cookie_differs_from_app_dashboard_cookie_contract(settings: Settings) -> None:
    # app-dashboard reads veda_dashboard_session with 4 fields; ours has another name and 5 fields
    value, _ = auth.create_session(settings, Credentials(USERNAME, PASSWORD))
    assert auth.COOKIE != "veda_dashboard_session"
    assert len(value.split("|")) == 5


def test_expired_and_tampered_sessions_rejected(settings: Settings, credentials_file: Path) -> None:
    store = CredentialStore(credentials_file)
    creds = store.current()
    value, _ = auth.create_session(settings, creds, now=time.time() - auth.TTL_SECONDS - 10)
    assert auth.parse_session(value, settings, store) is None
    value, _ = auth.create_session(settings, creds)
    assert auth.parse_session(value, settings, store) is not None
    tampered = value.replace(f"{USERNAME}|", "root|", 1)
    assert auth.parse_session(tampered, settings, store) is None


def test_user_outside_allowlist_is_viewer(settings: Settings) -> None:
    client = _client(settings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert _login(client).json()["data"]["role"] == "viewer"
    assert client.get("/api/session").json()["data"]["role"] == "viewer"


def test_allowlist_containing_user_is_admin(settings: Settings) -> None:
    client = _client(settings.model_copy(update={"admin_allowlist": f"someone-else, {USERNAME}"}))
    assert _login(client).json()["data"]["role"] == "admin"


def test_rate_limit_after_five_failures(settings: Settings) -> None:
    client = _client(settings)
    for _ in range(5):
        assert _login(client, password="bad").status_code == 401
    blocked = _login(client)  # even the right password is blocked now
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    retry = int(blocked.headers["retry-after"])
    assert 1 <= retry <= 900
    # another username from the same IP is a different bucket
    assert _login(client, username="other", password="bad").status_code == 401


def test_rate_limit_window_expires() -> None:
    limiter = auth.LoginRateLimiter(attempts=2, window_seconds=10)
    limiter.failed("1.1.1.1", "u", now=100)
    limiter.failed("1.1.1.1", "u", now=101)
    assert limiter.retry_after("1.1.1.1", "u", now=105) == 5
    assert limiter.retry_after("1.1.1.1", "u", now=111) is None


def test_forwarded_for_only_trusted_from_configured_proxy(settings: Settings) -> None:
    untrusted = _client(settings)
    for i in range(5):
        _login(untrusted, password="bad", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    # XFF ignored: all attempts counted against the peer, so this is blocked
    assert _login(untrusted, headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429

    trusted = _client(settings.model_copy(update={"trusted_proxies": "testclient"}))
    for _ in range(5):
        _login(trusted, password="bad", headers={"X-Forwarded-For": "203.0.113.5"})
    assert _login(trusted, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 429
    assert _login(trusted, headers={"X-Forwarded-For": "203.0.113.6"}).status_code == 200
    ips = {line["ip"] for line in _audit_lines(settings)}
    assert "203.0.113.5" in ips and "203.0.113.6" in ips


def test_audit_lines_for_login_ok_and_fail(settings: Settings) -> None:
    client = _client(settings)
    _login(client, password="bad")
    _login(client)
    lines = [line for line in _audit_lines(settings) if line["event"] == "login"]
    assert [line["result"] for line in lines] == ["fail", "ok"]
    for line in lines:
        assert line["actor"] == USERNAME and line["ip"] == "testclient"
        assert line["at"].endswith("Z")


def test_no_credential_or_foreign_secret_leaks(settings: Settings, credentials_file: Path) -> None:
    client = _client(settings)
    responses = [_login(client, password="bad"), _login(client), client.get("/api/session"), client.post("/api/logout")]
    for _ in range(6):
        responses.append(_login(client, password="bad"))
    blob = "".join(r.text + str(dict(r.headers)) for r in responses)
    for path in settings.runtime_dir.rglob("*"):
        if path.is_file():
            blob += path.read_text(encoding="utf-8")
    assert PASSWORD not in blob and OTHER_SECRET_VALUE not in blob and SECRET not in blob
    assert "dashboard-own-secret-not-ours" not in blob


def test_credential_store_keeps_only_the_two_keys(credentials_file: Path) -> None:
    store = CredentialStore(credentials_file)
    creds = store.current()
    assert creds == Credentials(USERNAME, PASSWORD)
    assert set(Credentials.__dataclass_fields__) == {"username", "password"}
    dumped = repr(store) + repr(creds) + repr(vars(store))
    assert OTHER_SECRET_VALUE not in dumped and "dashboard-own-secret-not-ours" not in dumped
    assert PASSWORD not in repr(creds) and PASSWORD not in repr(store)
