"""ตัวช่วยของ test เรื่อง flow: catalog ปลอมใน tmp veda + default เป็น dict ให้แก้ได้."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bmad_studio.core.flow.loader import load_yaml
from bmad_studio.core.flow.validate import DEFAULT_TEMPLATE, Catalog

AGENTS = ("project-bootstrapper", "ba", "ux-designer", "architect", "developer", "reviewer", "devops", "skill-scout")
SKILLS = ("bmad-architecture", "bmad-build", "bmad-code-review", "bmad-retrospective", "bmad-sprint-planning")
CATALOG = Catalog(frozenset(AGENTS), frozenset(SKILLS))


def seed_catalog(veda_root: Path) -> None:
    for agent in AGENTS:
        (veda_root / ".claude" / "agents" / f"{agent}.md").write_text(f"---\nname: {agent}\n---\n", encoding="utf-8")
    for skill in SKILLS:
        folder = veda_root / ".claude" / "skills" / skill
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")


_DEFAULT = load_yaml(DEFAULT_TEMPLATE.read_bytes())


def default_data() -> dict[str, Any]:
    return copy.deepcopy(_DEFAULT)


def stage(data: dict[str, Any], stage_id: str, track: str = "full") -> dict[str, Any]:
    return next(s for s in data["tracks"][track]["stages"] if s["id"] == stage_id)


def codes(issues: list[dict[str, str]]) -> set[str]:
    return {i["code"] for i in issues}


def dump(data: Any) -> str:
    import io

    from ruamel.yaml import YAML

    out = io.StringIO()
    yaml = YAML(typ="safe", pure=True)
    yaml.representer.ignore_aliases = lambda *_args: True
    yaml.dump(data, out)
    return out.getvalue()
