"""สถานะต่อขั้นของ flow จาก state ของโปรเจกต์ (AD-1, REQ-048, REQ-050) — อ่านอย่างเดียว.

แหล่ง (ทุกแหล่งอาจไม่มี — ไม่มี = ไม่ error):
- registry entry: ``requirementsReady``/``designReady``/``planReady``/``hasFrontend``/``status``/``dockerImage``
- ``.veda/projects/<n>/requirements.md``, ``sprint-status.yaml``, ``reviews/``, ``quick/*.md``

สถานะ: ``done`` เสร็จ · ``active`` มีงานค้าง/กำลังทำ/รอคนอนุมัติ · ``pending`` ยังไม่เริ่ม · ``skipped`` ข้าม (มี ``skipReason``)
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad_studio.adapters import fs
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow import policy
from bmad_studio.core.flow.loader import FlowYamlError, load_yaml
from bmad_studio.core.flow.schema import Stage
from bmad_studio.core.paths import VedaPaths

DONE, ACTIVE, PENDING, SKIPPED = "done", "active", "pending", "skipped"
DEVELOPED_STATUSES = frozenset({"developed", "running", "deployed"})
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 512 * 1024
_SAFE_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", re.ASCII)
_STORY_KEY = re.compile(r"\d+-\d+(?:-[a-z0-9-]+)?", re.ASCII)
_EPIC_KEY = re.compile(r"epic-(\d+)", re.ASCII)
_RETRO_KEY = re.compile(r"epic-(\d+)-retrospective", re.ASCII)
_SPEC_STATUS = re.compile(r"^(?:status|Status):\s*['\"]?([A-Za-z_-]+)", re.MULTILINE)
STORY_STATUSES = frozenset({"backlog", "ready-for-dev", "in-progress", "review", "done"})
QUICK_STATUSES = frozenset({"draft", "ready-for-dev", "in-progress", "review", "in-review", "done"})


class ProjectNotFound(StudioError):
    def __init__(self) -> None:
        super().__init__("project_not_found", "ไม่พบโปรเจกต์นี้ใน registry", 404)


def valid_project_name(name: object) -> bool:
    return isinstance(name, str) and bool(_SAFE_PROJECT.fullmatch(name)) and ".." not in name


def read_registry(paths: VedaPaths) -> list[dict[str, Any]]:
    """รายการโปรเจกต์ใน registry (อ่านอย่างเดียว; ไม่มีไฟล์ = ว่าง)."""
    path = paths.veda_state_dir / "projects" / "registry.json"
    try:
        raw = fs.read_bytes(path, MAX_REGISTRY_BYTES)
    except StudioError:
        raise StudioError("registry_unreadable", "อ่าน registry ไม่ได้", 503) from None
    if raw is None:
        return []
    try:
        data = json.loads(fs.decode(raw).text)
    except (ValueError, StudioError):
        raise StudioError("registry_unreadable", "registry ไม่ใช่ JSON ที่ถูกต้อง", 503) from None
    projects = data.get("projects") if isinstance(data, dict) else data
    if not isinstance(projects, list):
        raise StudioError("registry_unreadable", "registry ไม่มีรายการ projects", 503)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in projects:
        if isinstance(p, dict) and valid_project_name(p.get("name")) and p["name"] not in seen:
            seen.add(p["name"])  # ชื่อซ้ำ: ใช้รายการแรก (find_project ก็ใช้รายการแรก)
            out.append(p)
    return out


def find_project(paths: VedaPaths, name: str) -> dict[str, Any]:
    if not valid_project_name(name):
        raise ProjectNotFound()
    for entry in read_registry(paths):
        if entry.get("name") == name:  # ตรงตัวพิมพ์ — ชื่อโฟลเดอร์มาจาก registry
            return entry
    raise ProjectNotFound()


@dataclass
class ProjectState:
    name: str
    registry: Mapping[str, Any]
    has_requirements: bool = False
    stories: dict[str, str] = field(default_factory=dict)   # story key → status
    epics: dict[str, str] = field(default_factory=dict)     # epic number → status
    retros: dict[str, str] = field(default_factory=dict)    # epic number → retro status
    has_sprint: bool = False
    review_files: frozenset[str] = frozenset()
    quick: dict[str, str] = field(default_factory=dict)     # quick spec file → status
    warnings: list[dict[str, str]] = field(default_factory=list)

    def flag(self, name: str) -> bool:
        return self.registry.get(name) is True

    @property
    def status(self) -> str:
        value = self.registry.get("status")
        return value if isinstance(value, str) else ""


def _read_text(path: Path) -> str | None:
    try:
        raw = fs.read_bytes(path, MAX_STATE_BYTES)
        return None if raw is None else fs.decode(raw).text
    except (StudioError, OSError):
        return None


def load_state(paths: VedaPaths, entry: Mapping[str, Any]) -> ProjectState:
    name = str(entry["name"])
    folder = paths.veda_state_dir / "projects" / name
    state = ProjectState(name, entry)
    state.has_requirements = (folder / "requirements.md").is_file()
    sprint_path = folder / "sprint-status.yaml"
    if sprint_path.is_file():
        text = _read_text(sprint_path)
        data: Any = None
        if text is not None:
            try:
                data = load_yaml(text)
            except FlowYamlError:
                data = None
        dev = data.get("development_status") if isinstance(data, dict) else None
        if isinstance(dev, dict):
            state.has_sprint = True
            for key, value in dev.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                if m := _RETRO_KEY.fullmatch(key):
                    state.retros[m.group(1)] = value
                elif m := _EPIC_KEY.fullmatch(key):
                    state.epics[m.group(1)] = value
                elif _STORY_KEY.fullmatch(key):
                    state.stories[key] = value
                    if value not in STORY_STATUSES:
                        state.warnings.append({"code": "story_status_unknown",
                                               "error": f"story {key} มีสถานะ {value} ที่ไม่รู้จัก — ถือว่ายังไม่เสร็จ"})
        else:
            state.warnings.append({"code": "sprint_status_unreadable",
                                   "error": "อ่าน sprint-status.yaml ไม่ได้ — ใช้สถานะจาก registry แทน"})
    state.review_files = frozenset(_list(folder / "reviews", ".md"))
    for spec in _list(folder / "quick", ".md"):
        text = _read_text(folder / "quick" / spec) or ""
        match = _SPEC_STATUS.search(_head(text))
        value = match.group(1).lower().replace("_", "-") if match else "unknown"
        state.quick[spec] = value
        if value not in QUICK_STATUSES:
            state.warnings.append({"code": "quick_status_unknown",
                                   "error": f"quick/{spec}: อ่านสถานะไม่ได้ ({value}) — ไม่นับเป็นงานของขั้นใด"})
    return state


def _head(text: str) -> str:
    """front matter (ถ้ามี) ไม่งั้น 20 บรรทัดแรก — ไม่อ่าน status จาก body/code block."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[4:end]
    return "\n".join(text.split("\n")[:20])


def _list(folder: Path, suffix: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(folder) if e.is_file(follow_symlinks=False) and e.name.endswith(suffix))
    except OSError:
        return []


def _story_counts(state: ProjectState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in state.stories.values():
        counts[value] = counts.get(value, 0) + 1
    return counts


def stage_status(track: str, stage: Stage, state: ProjectState) -> tuple[str, str | None]:
    """(status, note) ของขั้นจากหลักฐาน — ไม่รวม enabled/condition (resolver ตัดสินเรื่องข้าม)."""
    if track == policy.FULL and stage.id == policy.GATE_STAGE_ID:
        # gate ของคน: ผ่านเมื่อ registry บอก requirementsReady เท่านั้น (ตั้งโดย app-dashboard)
        return (DONE, None) if state.flag(policy.GATE_FLAG) else (PENDING, "รอคนอนุมัติ requirement ที่ /review")
    if stage.produces:
        return (DONE, None) if all(state.flag(f) for f in stage.produces) else (PENDING, None)
    if track != policy.FULL:
        return _quick_status(stage, state)
    agent = stage.agent
    if agent == "project-bootstrapper":
        return DONE, None  # มีใน registry แล้ว
    if agent == policy.GATE_PREDECESSOR_AGENT:
        if state.has_requirements or state.flag(policy.GATE_FLAG):
            return DONE, None
        return PENDING, "ยังไม่มี requirements.md"
    if agent == "devops":
        if state.registry.get("dockerImage") or state.registry.get("containerName") or state.status == "deployed":
            return DONE, None
        return PENDING, None
    if agent == "reviewer" and (stage.skill == "bmad-retrospective" or stage.id == "retrospective"):
        return _retro_status(state)
    if agent == "developer":
        return _develop_status(state)
    if agent == "reviewer":
        return _review_status(state)
    return PENDING, "ไม่มีสัญญาณสถานะของขั้นนี้ — ตัดสินเองว่าเสร็จหรือยัง"


def _develop_status(state: ProjectState) -> tuple[str, str | None]:
    if not state.has_sprint:
        if state.status in DEVELOPED_STATUSES:
            return DONE, "ไม่มี sprint-status — ดูจาก registry status"
        if state.status == "developing":
            return ACTIVE, "ไม่มี sprint-status — registry status developing"
        return PENDING, None
    counts = _story_counts(state)
    if not state.stories:
        return PENDING, "sprint-status ไม่มี story"
    todo = sum(n for s, n in counts.items() if s not in {"review", "done"})  # สถานะไม่รู้จัก = ยังไม่เสร็จ
    if todo == 0:
        return DONE, None
    reworks = sorted(k for k, v in state.stories.items() if v == "in-progress" and f"{k}.md" in state.review_files)
    note = f"story ค้าง {todo} (in-progress {counts.get('in-progress', 0)}, ready-for-dev {counts.get('ready-for-dev', 0)})"
    if reworks:
        note += f"; ถูกส่งกลับแก้: {', '.join(reworks)}"
    started = counts.get("in-progress", 0) + counts.get("review", 0) + counts.get("done", 0)
    return (ACTIVE if started or counts.get("ready-for-dev") else PENDING), note


def _review_status(state: ProjectState) -> tuple[str, str | None]:
    if not state.has_sprint:
        return (DONE, "ไม่มี sprint-status — ดูจาก registry status") if state.status in DEVELOPED_STATUSES else (PENDING, None)
    counts = _story_counts(state)
    if state.stories and counts.get("done", 0) == len(state.stories):
        return DONE, None
    if counts.get("review"):
        waiting = sorted(k for k, v in state.stories.items() if v == "review")
        return ACTIVE, f"story รอ review: {', '.join(waiting)}"
    return PENDING, None


def _retro_status(state: ProjectState) -> tuple[str, str | None]:
    if not state.has_sprint:
        if state.status in DEVELOPED_STATUSES:
            return SKIPPED, "ไม่มี sprint-status (โปรเจกต์ก่อน BMAD flow) — ไม่มี epic ให้ทำ retro"
        return PENDING, None
    if not state.epics:
        return SKIPPED, "sprint-status ไม่มี epic — ไม่มี retro ให้ทำ"
    done_epics = sorted((n for n, v in state.epics.items() if v == "done"), key=int)
    waiting = [n for n in done_epics if state.retros.get(n) != "done"]
    if waiting:
        return ACTIVE, f"epic ที่จบแล้วรอ retro: {', '.join('epic-' + n for n in waiting)}"
    if state.epics and len(done_epics) == len(state.epics):
        return DONE, None
    return PENDING, None


def _quick_status(stage: Stage, state: ProjectState) -> tuple[str, str | None]:
    statuses = list(state.quick.values())
    if stage.agent == "reviewer":
        waiting = [f for f, s in state.quick.items() if s in {"review", "in-review"}]
        return (ACTIVE, f"quick spec รอ review: {', '.join(waiting)}") if waiting else (PENDING, None)
    if stage.agent == "developer":
        working = [f for f, s in state.quick.items() if s in {"in-progress", "ready-for-dev", "draft"}]
        if working:
            return ACTIVE, f"quick spec ค้าง: {', '.join(working)}"
        return PENDING, ("ยังไม่มีงาน quick" if not statuses else None)
    return PENDING, None
