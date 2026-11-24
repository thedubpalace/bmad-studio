"""Fixture กลาง: tmp veda + credential file ปลอม — ห้ามแตะ veda/app-dashboard จริงใน test."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bmad_studio.core.settings import Settings

SECRET = "s" * 48
USERNAME = "admin"
PASSWORD = "correct horse battery staple"
OTHER_SECRET_VALUE = "ghp_DO_NOT_LEAK_THIS_TOKEN_123"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("BMAD_STUDIO_") or key in {"VEDA_ROOT", "UVICORN_HOST"}:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def veda_root(tmp_path: Path) -> Path:
    root = tmp_path / "veda"
    (root / "_bmad" / "scripts").mkdir(parents=True)
    (root / "_bmad" / "custom").mkdir()
    (root / ".claude" / "skills").mkdir(parents=True)
    (root / ".claude" / "agents").mkdir()
    (root / ".veda" / "projects").mkdir(parents=True)
    return root


@pytest.fixture
def credentials_file(tmp_path: Path) -> Path:
    path = tmp_path / "app-dashboard.env.runtime"
    path.write_text(
        "# app-dashboard runtime env\n"
        f"GITHUB_TOKEN={OTHER_SECRET_VALUE}\n"
        f"VEDA_DASHBOARD_USERNAME={USERNAME}\n"
        f'VEDA_DASHBOARD_PASSWORD="{PASSWORD}"\n'
        "VEDA_DASHBOARD_SESSION_SECRET=dashboard-own-secret-not-ours\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def settings(veda_root: Path, credentials_file: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        veda_root=veda_root,
        session_secret=SECRET,
        credentials_env_file=credentials_file,
        runtime_dir=tmp_path / "runtime",
    )
