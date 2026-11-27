"""tmp veda ที่มี resolve script ทางการของ BMAD (คัดลอกจาก veda จริงแบบอ่านอย่างเดียว) — story 6-x.

ห้ามเขียน veda จริง: ทุกไฟล์ที่ test สร้างอยู่ใต้ ``tmp_path``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bmad_studio.core.settings import Settings

UV = shutil.which("uv")
needs_uv = pytest.mark.skipif(UV is None, reason="uv not installed")
REAL_VEDA = Path("C:/Users/thedu/code/veda")

CONFIG_TOML = """# Installer-managed.
[core]
user_name = "BMad"
communication_language = "English"
document_output_language = "Thai"
output_folder = ".veda/bmad-output"

[agents.bmad-agent-dev]
module = "bmm"
name = "Amelia"
description = "Writes code."

[[menu]]
code = "a"
label = "first"
"""
CONFIG_USER_TOML = """[core]
user_name = "tester"
communication_language = "Thai"
"""
TEAM_TOML = """# Team overrides — comment ต้องอยู่ต่อ
[core]
document_output_language = "English" # ภาษาเอกสาร
max_rounds = 3
"""
BUILD_CUSTOMIZE = """# DO NOT EDIT
[workflow]
activation_steps_prepend = []
activation_steps_append = []
persistent_facts = []
on_complete = ""
max_rounds = 2
strict = true

[[workflow.review_layers]]
id = "blind"
name = "Blind"
instruction = "review blind"
"""


def real_scripts_dir() -> Path:
    root = Settings().veda_root  # repo .env; อ่านอย่างเดียว
    scripts = Path(root) / "_bmad" / "scripts" if root else None
    if not scripts or not (scripts / "resolve_config.py").is_file():
        pytest.skip("official BMAD scripts not available")
    return scripts


def seed_bmad(root: Path) -> Path:
    scripts = real_scripts_dir()
    for name in ("resolve_config.py", "resolve_customization.py", "config_utils.py"):
        shutil.copy2(scripts / name, root / "_bmad" / "scripts" / name)
    (root / "_bmad" / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
    (root / "_bmad" / "config.user.toml").write_text(CONFIG_USER_TOML, encoding="utf-8")
    (root / "_bmad" / "custom" / "config.toml").write_text(TEAM_TOML, encoding="utf-8")
    skills = root / ".claude" / "skills"
    for name, customize in {"bmad-build": BUILD_CUSTOMIZE, "bmad-help": None, "veda": None}.items():
        (skills / name).mkdir(parents=True, exist_ok=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n",
                                                encoding="utf-8")
        if customize is not None:
            (skills / name / "customize.toml").write_text(customize, encoding="utf-8")
    return root


def run_script(root: Path, script: str, *args: str) -> dict:
    result = subprocess.run([UV, "run", "--no-project", "--quiet", str(root / "_bmad" / "scripts" / script), *args],
                            cwd=root, capture_output=True, check=True, timeout=60)
    return json.loads(result.stdout.decode("utf-8"))


def tree_hash(root: Path) -> dict[str, str]:
    """sha256 ของทุกไฟล์ใต้ root (ใช้ยืนยันว่า preview ไม่เขียนอะไร)."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def real_veda_hashes() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for rel in ("_bmad/config.toml", "_bmad/config.user.toml", "_bmad/custom/config.toml",
                "_bmad/custom/config.user.toml", "CLAUDE.md"):
        path = REAL_VEDA / rel
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return out
