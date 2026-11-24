"""Story 1.2 rework (review round 1): B1 bounded rate limiter, B2 non-ASCII cookie, N1 '|' in username."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api import auth
from bmad_studio.api.access_guard import SECURITY_HEADERS
from bmad_studio.api.app import create_app
from bmad_studio.core.credentials import CredentialStore, CredentialsUnavailable, read_credentials
from bmad_studio.core.errors import StartupError
from bmad_studio.core.settings import Settings, check_startup

from .conftest import PASSWORD, USERNAME


# ---------- B1: rate limiter memory is bounded ----------

def test_keys_are_removed_when_their_window_expires() -> None:
    limiter = auth.LoginRateLimiter(attempts=5, window_seconds=60)
    for i in range(300):
        limiter.failed("10.0.0.1", f"user-{i}" * 20, now=1000)
    assert len(limiter) == 300
    # after the window, any call sweeps expired buckets: the key count returns to 0
    assert limiter.retry_after("10.0.0.2", "someone", now=1000 + 61) is None
    assert len(limiter) == 0


def test_lookup_of_expired_key_deletes_it() -> None:
    limiter = auth.LoginRateLimiter(attempts=5, window_seconds=600)
    limiter.failed("1.1.1.1", "u", now=100)
    assert limiter.retry_after("1.1.1.1", "u", now=100 + 601) is None
    assert len(limiter) == 0
    limiter.failed("1.1.1.1", "u", now=800)
    limiter.failed("1.1.1.1", "u", now=800 + 601)  # old timestamp pruned, key kept with 1 entry
    assert len(limiter) == 1


def test_lookup_never_creates_keys() -> None:
    limiter = auth.LoginRateLimiter(attempts=5, window_seconds=60)
    for i in range(100):
        assert limiter.retry_after("1.1.1.1", f"u{i}", now=10) is None
    assert len(limiter) == 0


def test_key_count_is_capped() -> None:
    limiter = auth.LoginRateLimiter(attempts=5, window_seconds=900, max_keys=50)
    for i in range(500):
        limiter.failed("10.0.0.1", f"user-{i}", now=1000 + i * 0.001)
    assert len(limiter) == 50
    # the most recent failures survive, the oldest were evicted
    assert limiter._failures.keys() >= {("10.0.0.1", "user-499")}
    assert ("10.0.0.1", "user-0") not in limiter._failures


def test_cap_prefers_evicting_expired_keys() -> None:
    limiter = auth.LoginRateLimiter(attempts=2, window_seconds=100, max_keys=3)
    limiter.failed("ip", "blocked", now=500)
    limiter.failed("ip", "blocked", now=501)
    limiter.failed("ip", "old-a", now=10)  # these expire before the next insert
    limiter.failed("ip", "old-b", now=11)
    limiter.failed("ip", "new", now=502)
    assert len(limiter) == 2
    assert limiter.retry_after("ip", "blocked", now=502) is not None  # still blocked: not evicted


def test_rate_limit_still_blocks_after_hardening(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    for _ in range(5):
        client.post("/api/login", json={"username": USERNAME, "password": "bad"})
    assert client.post("/api/login", json={"username": USERNAME, "password": PASSWORD}).status_code == 429


# ---------- B2: non-ASCII / malformed cookie -> 401, logged, with headers ----------

BAD_COOKIES = [
    "admin|1|c|d|\xe9",
    "\xe9\xe9\xe9|9999999999|c|d|abc",
    "admin|notanumber|c|d|" + "0" * 64,
    "only|three|parts",
    "a|b|c|d|e|f",
]


def _security(settings: Settings) -> list[dict]:
    path = settings.runtime_dir / "security.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.mark.parametrize("raw", BAD_COOKIES)
def test_malformed_cookie_is_401_logged_with_headers(settings: Settings, raw: str) -> None:
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    response = client.get("/api/bmad/inventory", headers={"Cookie": f"{auth.COOKIE}={raw}".encode("latin-1")})
    assert response.status_code == 401
    assert response.json()["code"] == "session_expired"
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value
    lines = _security(settings)
    assert len(lines) == 1 and lines[0]["status"] == 401 and lines[0]["path"] == "/api/bmad/inventory"


def test_non_ascii_cookie_on_public_session_route_is_not_500(settings: Settings) -> None:
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    response = client.get("/api/session", headers={"Cookie": f"{auth.COOKIE}=admin|1|c|d|\xe9".encode("latin-1")})
    assert response.status_code == 200
    assert response.json()["data"] == {"authenticated": False}


@pytest.mark.parametrize("raw", BAD_COOKIES)
def test_parse_session_returns_none_instead_of_raising(settings: Settings, credentials_file: Path, raw: str) -> None:
    assert auth.parse_session(raw, settings, CredentialStore(credentials_file)) is None


# ---------- N1: '|' in the username is refused ----------

def test_pipe_in_username_refused(tmp_path: Path, settings: Settings) -> None:
    creds = tmp_path / "pipe.env"
    creds.write_text("VEDA_DASHBOARD_USERNAME=ad|min\nVEDA_DASHBOARD_PASSWORD=x\n", encoding="utf-8")
    with pytest.raises(CredentialsUnavailable) as info:
        read_credentials(creds)
    assert "|" in str(info.value) and "x" not in str(info.value).replace("VEDA_DASHBOARD_USERNAME", "")
    with pytest.raises(StartupError):
        check_startup(settings.model_copy(update={"credentials_env_file": creds}))
