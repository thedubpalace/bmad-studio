"""Story 1.1 — start ไม่ได้ถ้าการตั้งค่าไม่พร้อม, bind loopback เท่านั้น, /health."""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api.app import create_app, requested_host
from bmad_studio.core.errors import StartupError
from bmad_studio.core.settings import REPO_ROOT, Settings, check_host, check_startup, is_loopback

from .conftest import OTHER_SECRET_VALUE, PASSWORD, SECRET


def _problems(settings: Settings) -> list[str]:
    with pytest.raises(StartupError) as info:
        check_startup(settings)
    return info.value.problems


def test_ready_settings_pass(settings: Settings) -> None:
    check_startup(settings)


def test_missing_session_secret_fails(settings: Settings) -> None:
    problems = _problems(settings.model_copy(update={"session_secret": None}))
    assert any("BMAD_STUDIO_SESSION_SECRET" in p for p in problems)


def test_veda_root_without_bmad_fails(settings: Settings, tmp_path: Path) -> None:
    empty = tmp_path / "not-veda"
    empty.mkdir()
    problems = _problems(settings.model_copy(update={"veda_root": empty}))
    assert any("VEDA_ROOT" in p for p in problems)


def test_missing_veda_root_fails(settings: Settings) -> None:
    assert any("VEDA_ROOT" in p for p in _problems(settings.model_copy(update={"veda_root": None})))


def test_missing_credentials_file_fails(settings: Settings, tmp_path: Path) -> None:
    problems = _problems(settings.model_copy(update={"credentials_env_file": tmp_path / "nope.env"}))
    assert any("BMAD_STUDIO_CREDENTIALS_ENV_FILE" in p for p in problems)


def test_unreadable_credentials_path_fails(settings: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "a-directory"
    folder.mkdir()
    problems = _problems(settings.model_copy(update={"credentials_env_file": folder}))
    assert any("BMAD_STUDIO_CREDENTIALS_ENV_FILE" in p for p in problems)


def test_empty_password_fails(settings: Settings, tmp_path: Path) -> None:
    creds = tmp_path / "empty.env"
    creds.write_text("VEDA_DASHBOARD_USERNAME=admin\nVEDA_DASHBOARD_PASSWORD=\n", encoding="utf-8")
    problems = _problems(settings.model_copy(update={"credentials_env_file": creds}))
    assert any("VEDA_DASHBOARD_PASSWORD" in p for p in problems)


def test_problem_messages_never_contain_secret_values(settings: Settings, tmp_path: Path) -> None:
    bad = settings.model_copy(update={"session_secret": "short", "veda_root": tmp_path / "x"})
    text = " ".join(_problems(bad))
    for value in ("short", PASSWORD, OTHER_SECRET_VALUE, SECRET):
        assert value not in text


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_allowed(host: str) -> None:
    assert is_loopback(host)
    check_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com", ""])
def test_non_loopback_hosts_refused(host: str) -> None:
    assert not is_loopback(host)
    with pytest.raises(StartupError):
        check_host(host)


def test_requested_host_reads_uvicorn_argv_and_env() -> None:
    assert requested_host(["uvicorn", "x:app"], {}) == "127.0.0.1"
    assert requested_host(["uvicorn", "x:app", "--host", "0.0.0.0"], {}) == "0.0.0.0"
    assert requested_host(["uvicorn", "x:app", "--host=10.0.0.1"], {}) == "10.0.0.1"
    assert requested_host(["uvicorn"], {"UVICORN_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_health_is_public_and_minimal(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unknown_route_uses_error_envelope(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    body = client.get("/api/nothing-here").json()
    assert body["ok"] is False and isinstance(body["code"], str) and body["error"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_uvicorn(host: str, env_file: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in __import__("os").environ.items()
           if not k.startswith("BMAD_STUDIO_") and k != "VEDA_ROOT"}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "uvicorn", "bmad_studio.api.app:app", "--host", host, "--port", str(_free_port())],
        cwd=env_file.parent, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_process_exits_nonzero_before_bind_without_config(tmp_path: Path) -> None:
    # empty working dir; the repo .env is bypassed by pointing every value at nothing usable
    result = _run_uvicorn("127.0.0.1", tmp_path / "x", {
        "BMAD_STUDIO_SESSION_SECRET": "", "VEDA_ROOT": str(tmp_path / "missing"),
        "BMAD_STUDIO_CREDENTIALS_ENV_FILE": str(tmp_path / "missing.env"), "PYTHONPATH": str(REPO_ROOT / "src"),
    })
    assert result.returncode != 0
    assert "VEDA_ROOT" in result.stderr and "BMAD_STUDIO_SESSION_SECRET" in result.stderr
    assert "Uvicorn running" not in result.stderr


def test_process_refuses_non_loopback_host(settings: Settings, tmp_path: Path) -> None:
    result = _run_uvicorn("0.0.0.0", tmp_path / "x", {
        "BMAD_STUDIO_SESSION_SECRET": SECRET, "VEDA_ROOT": str(settings.veda_root),
        "BMAD_STUDIO_CREDENTIALS_ENV_FILE": str(settings.credentials_env_file), "PYTHONPATH": str(REPO_ROOT / "src"),
    })
    assert result.returncode != 0
    assert "loopback" in result.stderr
    assert "Uvicorn running" not in result.stderr
