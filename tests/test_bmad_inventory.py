"""Story 1.5 — BMAD inventory ผ่าน resolve script ทางการ (REQ-039, AD-10)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.adapters.bmad import BmadScripts, BmadUnavailable, list_skills, read_version
from bmad_studio.api.app import create_app
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.settings import Settings

from .conftest import PASSWORD, USERNAME

UV = shutil.which("uv")
needs_uv = pytest.mark.skipif(UV is None, reason="uv not installed")

CONFIG_TOML = """[core]
user_name = "Tester"
communication_language = "Thai"
project_name = "fixture"

[agents.bmad-agent-dev]
module = "bmm"
name = "Amelia"
title = "Senior Software Engineer"
description = "Writes code."
"""

MANIFEST = """installation:
  version: 9.8.7
  installDate: 2026-11-21T11:35:07.861Z
modules:
  - name: core
    version: 1.0.0
"""


def _real_scripts_dir() -> Path:
    root = Settings().veda_root  # repo .env; read-only source of the official scripts
    scripts = Path(root) / "_bmad" / "scripts" if root else None
    if not scripts or not (scripts / "resolve_config.py").is_file():
        pytest.skip("official BMAD scripts not available")
    return scripts


@pytest.fixture
def bmad_veda(veda_root: Path) -> Path:
    scripts = _real_scripts_dir()
    for name in ("resolve_config.py", "config_utils.py"):
        shutil.copy2(scripts / name, veda_root / "_bmad" / "scripts" / name)
    (veda_root / "_bmad" / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
    (veda_root / "_bmad" / "custom" / "config.toml").write_text('[core]\nproject_name = "overridden"\n', encoding="utf-8")
    (veda_root / "_bmad" / "_config").mkdir()
    (veda_root / "_bmad" / "_config" / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    skills = veda_root / ".claude" / "skills"
    for name, front in {
        "bmad-build": "---\nname: bmad-build\ndescription: 'Turns work into code. It''s reviewed.'\n---\n\nbody\n",
        "bmad-help": '---\nname: bmad-help\ndescription: "Answers questions"\n---\n',
        "bmad-plain": "no frontmatter here\n",
        "other-skill": "---\ndescription: not bmad\n---\n",
    }.items():
        (skills / name).mkdir()
        (skills / name / "SKILL.md").write_text(front, encoding="utf-8")
    (veda_root / "_bmad" / "custom" / "bmad-build.toml").write_text("[workflow]\n", encoding="utf-8")
    (veda_root / "_bmad" / "custom" / "bmad-help.user.toml").write_text("[workflow]\n", encoding="utf-8")
    return veda_root


def _client(settings: Settings, root: Path, **update) -> TestClient:
    client = TestClient(create_app(settings.model_copy(update={"veda_root": root, **update})))
    response = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 200
    return client


def test_skills_match_real_folders_with_description_and_overrides(bmad_veda: Path) -> None:
    skills = {s.id: s for s in list_skills(VedaPaths(bmad_veda))}
    assert set(skills) == {"bmad-build", "bmad-help", "bmad-plain"}  # only bmad-* folders
    assert skills["bmad-build"].description == "Turns work into code. It's reviewed."
    assert skills["bmad-help"].description == "Answers questions"
    assert skills["bmad-plain"].description == ""
    assert (skills["bmad-build"].override_team, skills["bmad-build"].override_user) == (True, False)
    assert (skills["bmad-help"].override_team, skills["bmad-help"].override_user) == (False, True)


def test_version_from_manifest(bmad_veda: Path) -> None:
    assert read_version(VedaPaths(bmad_veda)) == "9.8.7"


@needs_uv
def test_inventory_config_equals_running_the_script_directly(settings: Settings, bmad_veda: Path) -> None:
    direct = subprocess.run(
        [UV, "run", "--no-project", "--quiet", str(bmad_veda / "_bmad" / "scripts" / "resolve_config.py"),
         "--project-root", str(bmad_veda)],
        cwd=bmad_veda, capture_output=True, check=True, timeout=60,
    )
    expected = json.loads(direct.stdout.decode("utf-8"))
    data = _client(settings, bmad_veda).get("/api/bmad/inventory").json()["data"]
    assert data["version"] == "9.8.7"
    assert data["tools"] == {"uv": True, "resolve_script": True}
    assert data["config"]["ok"] is True and data["config"]["error"] is None
    assert data["config"]["core"] == expected["core"]
    assert data["config"]["core"]["project_name"] == "overridden"  # merged by the script, not by us
    assert [a["id"] for a in data["config"]["agents"]] == list(expected["agents"])
    assert data["config"]["agents"][0]["name"] == "Amelia"
    assert [s["id"] for s in data["skills"]] == ["bmad-build", "bmad-help", "bmad-plain"]


def test_missing_uv_keeps_skill_list(settings: Settings, bmad_veda: Path, monkeypatch) -> None:
    monkeypatch.setattr("bmad_studio.adapters.bmad.shutil.which", lambda _name: None)
    response = _client(settings, bmad_veda).get("/api/bmad/inventory")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tools"]["uv"] is False
    assert data["config"]["ok"] is False
    assert data["config"]["error"]["code"] == "bmad_unavailable"
    assert data["config"]["error"]["reason"] == "uv_missing"
    assert "uv" in data["config"]["error"]["error"]
    assert len(data["skills"]) == 3


@needs_uv
def test_script_failure_is_bmad_unavailable(settings: Settings, bmad_veda: Path) -> None:
    (bmad_veda / "_bmad" / "config.toml").write_text("[core\nbroken = ", encoding="utf-8")
    data = _client(settings, bmad_veda).get("/api/bmad/inventory").json()["data"]
    assert data["config"]["error"]["code"] == "bmad_unavailable"
    assert data["config"]["error"]["reason"] == "script_failed"
    assert len(data["skills"]) == 3


@needs_uv
def test_script_timeout_is_bmad_unavailable(settings: Settings, bmad_veda: Path) -> None:
    (bmad_veda / "_bmad" / "scripts" / "resolve_config.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    data = _client(settings, bmad_veda, bmad_script_timeout_seconds=1.5).get("/api/bmad/inventory").json()["data"]
    assert data["config"]["error"]["reason"] == "timeout"
    assert len(data["skills"]) == 3


def test_missing_script_is_bmad_unavailable(veda_root: Path) -> None:
    scripts = BmadScripts(VedaPaths(veda_root), uv_path="uv")
    with pytest.raises(BmadUnavailable) as info:
        scripts.resolve_config()
    assert info.value.reason == "script_missing" and info.value.code == "bmad_unavailable"


@needs_uv
def test_results_cached_until_input_mtime_changes(bmad_veda: Path, monkeypatch) -> None:
    scripts = BmadScripts(VedaPaths(bmad_veda))
    calls = []
    original = scripts.run_json

    def counting(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(scripts, "run_json", counting)
    first = scripts.resolve_config()
    assert scripts.resolve_config() == first and len(calls) == 1
    (bmad_veda / "_bmad" / "custom" / "config.toml").write_text('[core]\nproject_name = "again"\n', encoding="utf-8")
    assert scripts.resolve_config()["core"]["project_name"] == "again" and len(calls) == 2


def test_inventory_requires_admin(settings: Settings, bmad_veda: Path) -> None:
    anonymous = TestClient(create_app(settings.model_copy(update={"veda_root": bmad_veda})))
    assert anonymous.get("/api/bmad/inventory").status_code == 401
    viewer = _client(settings, bmad_veda, admin_allowlist="someone-else")
    assert viewer.get("/api/bmad/inventory").json()["code"] == "forbidden"
