"""Skill catalog + ที่ใช้ใน flow (story 6-2, REQ-051, AD-1/AD-10) — อ่านอย่างเดียว.

- รายการ skill มาจาก :meth:`Catalog.scan` ตัวเดียวกับ validator (โฟลเดอร์ ``.claude/skills/<id>/SKILL.md``) —
  ชื่อที่ catalog บอกว่า "มี" ตรงกับที่ validator ยอมรับเสมอ
- "ใช้ใน" = ขั้นของ flow ที่ใช้งานจริง: flow เริ่มต้น และ flow ที่ใช้จริงของทุกโปรเจกต์ใน registry ที่มีไฟล์ overlay
  (โปรเจกต์ที่ไม่มี overlay ใช้ flow เริ่มต้น จึงนับรวมใน "flow เริ่มต้น"). merge โดยไม่ตรวจ catalog เพื่อให้เห็น
  skill ที่ถูกอ้างแต่ไม่มีโฟลเดอร์ (validator ยังรายงาน ``unknown_skill`` เป็น error ตามเดิม)
- "ปิด skill" ในความหมายของแอป = ไม่ถูกอ้างใน flow — ไม่มีโค้ดใดลบ/แก้ ``SKILL.md`` (AD-2 deny)
"""
from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bmad_studio.adapters import fs
from bmad_studio.core.catalog import _description, _ok
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow.merge import FlowInvalid, effective_for, load_default, overlay_path
from bmad_studio.core.flow.schema import Flow
from bmad_studio.core.flow.status import read_registry
from bmad_studio.core.flow.validate import Catalog
from bmad_studio.core.paths import VedaPaths

DEFAULT_FLOW = "default"
MAX_SKILL_MD = 256 * 1024
NEWLINE = bytes([10])
CR = bytes([13])
BMAD_PREFIX = "bmad-"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def contained_file(paths: VedaPaths, skill: str, name: str) -> Path | None:
    """``.claude/skills/<skill>/<name>`` ถ้าเป็นไฟล์ปกติที่อยู่ใน ``.claude/skills`` จริง — ไม่ตาม symlink/junction
    และไม่รับ hard link (review 6-2: junction/hard link ทำให้อ่านไฟล์นอก catalog ได้). ไม่ผ่าน = ``None``."""
    root = paths.skills_dir
    current = root
    for part in (skill, name):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        return None
    try:
        real, base = os.path.realpath(current), os.path.realpath(root)
    except OSError:
        return None
    if os.path.commonpath([os.path.normcase(real), os.path.normcase(base)]) != os.path.normcase(base):
        return None
    return current


def _refs(flow: Flow, owner: str, kind: str) -> list[dict[str, Any]]:
    out = []
    for track_id, track in flow.tracks.items():
        for stage in track.stages:
            if stage.skill:
                out.append({"skill": stage.skill, "flow": owner, "kind": kind, "track": track_id, "stage": stage.id,
                            "enabled": stage.enabled})
    return out


USE_KEYS = ("flow", "kind", "track", "stage", "enabled")


def usage(paths: VedaPaths) -> dict[str, Any]:
    """การอ้าง skill ในทุก flow ที่ใช้งาน + flow ที่อ่านไม่ได้ (ไม่เดา — รายงานแยก).

    ``known`` = False เมื่ออ่าน flow เริ่มต้นไม่ได้ (การนับทั้งหมดไม่น่าเชื่อถือ — UI ต้องไม่บอกว่า "ไม่ถูกใช้").
    """
    refs: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    try:
        loaded: tuple[Flow, str] | None = load_default(paths, None)
    except (FlowInvalid, StudioError):
        loaded = None
        unreadable.append({"flow": DEFAULT_FLOW, "kind": "default"})
    flows = 0
    if loaded is not None:
        refs += _refs(loaded[0], DEFAULT_FLOW, "default")
        flows = 1
    try:
        projects = read_registry(paths)
    except StudioError:
        projects = []
        unreadable.append({"flow": "registry.json", "kind": "projects-list"})
    for entry in projects:
        name = entry["name"]
        if not os.path.lexists(overlay_path(paths, name)):
            continue
        if loaded is None:
            unreadable.append({"flow": name, "kind": "project"})
            continue
        try:
            effective = effective_for(paths, name, None, loaded)
        except (FlowInvalid, StudioError):
            unreadable.append({"flow": name, "kind": "project"})
            continue
        flows += 1
        refs += _refs(effective.flow, name, "project")
    return {"refs": refs, "unreadable": unreadable, "flows": flows, "known": loaded is not None}


def _overrides(paths: VedaPaths, skill: str) -> dict[str, bool]:
    custom = paths.bmad_custom_dir
    return {"team": (custom / f"{skill}.toml").is_file(), "user": (custom / f"{skill}.user.toml").is_file()}


def _entry(paths: VedaPaths, skill: str, used: Mapping[str, Any] | None) -> dict[str, Any]:
    refs = used["refs"] if used else []
    mine = [r for r in refs if r["skill"] == skill]
    overrides = _overrides(paths, skill)
    skill_md = contained_file(paths, skill, "SKILL.md")
    return {
        # field เดิมของ story 4-1 (StageInspector): id, description — รูปไม่เปลี่ยน
        "id": skill,
        "description": _description(skill_md) if skill_md else "",
        "group": "bmad" if skill.startswith(BMAD_PREFIX) else "veda",
        "overrideTeam": overrides["team"],
        "overrideUser": overrides["user"],
        "customizable": contained_file(paths, skill, "customize.toml") is not None,
        # นับเฉพาะ flow ที่มีขั้นซึ่งเปิดอยู่ใช้ skill นี้; usedIn แสดงทุกขั้น (รวมที่ปิด พร้อม enabled)
        "flowCount": len({(r["kind"], r["flow"]) for r in mine if r["enabled"]}),
        "usageKnown": bool(used and used["known"]),
        "usedIn": [{k: r[k] for k in USE_KEYS} for r in mine],
    }


def _present(catalog: Catalog) -> set[str]:
    return {name for name in catalog.skills if _ok(name)}


def skill_catalog(paths: VedaPaths, catalog: Catalog | None = None, *, with_usage: bool = True) -> list[dict[str, Any]]:
    """``GET /api/skills`` — ทุก skill ที่มีโฟลเดอร์ เรียงตามชื่อ (เพิ่มกลุ่ม/override/การอ้างอิงจาก 4-1).
    ``with_usage=False`` (``?usage=0``) ข้ามการ merge flow ทุกโปรเจกต์ — ให้ตัวเลือก skill ของ flow editor."""
    catalog = catalog or Catalog.scan(paths)
    used = usage(paths) if with_usage else None
    return [_entry(paths, name, used) for name in sorted(_present(catalog))]


def missing_skills(paths: VedaPaths, catalog: Catalog | None = None) -> dict[str, Any]:
    """skill ที่ flow ใดอ้างแต่ไม่มีโฟลเดอร์ (REQ-051 a) + flow ที่อ่านไม่ได้."""
    present = _present(catalog or Catalog.scan(paths))
    used = usage(paths)
    missing: dict[str, list[dict[str, Any]]] = {}
    for ref in used["refs"]:
        if ref["skill"] not in present:
            missing.setdefault(ref["skill"], []).append({k: ref[k] for k in USE_KEYS})
    return {"missing": [{"id": k, "usedIn": v, "flowCount": len({(r["kind"], r["flow"]) for r in v})}
                        for k, v in sorted(missing.items())],
            "unreadable": used["unreadable"], "flows": used["flows"], "known": used["known"]}


def _read_head(path: Path) -> tuple[bytes, bool]:
    with open(path, "rb") as handle:  # อ่านอย่างเดียว ไม่เกิน MAX+1 (ไม่โหลดทั้งไฟล์ใหญ่)
        head = handle.read(MAX_SKILL_MD + 1)
    return head[:MAX_SKILL_MD], len(head) > MAX_SKILL_MD


def _decode_head(head: bytes, truncated: bool) -> str:
    if not truncated:
        return fs.decode(head).text
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        cut = head[: len(head) - len(head) % 2]  # UTF-16: ตัดที่ขอบ 2 byte
        text = cut.decode("utf-16", errors="ignore").replace("\r\n", "\n")
        return text.rsplit("\n", 1)[0] if "\n" in text else text  # ไม่แสดงบรรทัดที่ถูกตัดครึ่ง
    if NEWLINE in head:
        head = head.rsplit(NEWLINE, 1)[0].rstrip(CR)
    return fs.to_lf(head.decode("utf-8-sig", errors="ignore"))  # ตัดกลางตัวอักษรได้ — ทิ้งเศษท้าย


def skill_detail(paths: VedaPaths, skill: str) -> dict[str, Any]:
    """``GET /api/skills/<id>`` — ใช้ใน + ``SKILL.md`` แบบอ่านอย่างเดียว (ตัดที่ :data:`MAX_SKILL_MD`)."""
    catalog = Catalog.scan(paths)
    if not _ok(skill) or skill not in catalog.skills:
        raise StudioError("skill_not_found", "ไม่พบ skill นี้ใน .claude/skills", 404)
    used = usage(paths)
    entry = _entry(paths, skill, used)
    text, truncated, error = "", False, None
    path = contained_file(paths, skill, "SKILL.md")
    if path is None:
        error = "not_contained"
    else:
        try:
            head, truncated = _read_head(path)
            text = _decode_head(head, truncated)
        except (StudioError, OSError, UnicodeError):
            text, error = "", "unreadable"
    return {**entry, "skillMd": text, "skillMdTruncated": truncated, "skillMdLimit": MAX_SKILL_MD,
            "skillMdError": error, "skillMdPath": f".claude/skills/{skill}/SKILL.md",
            "unreadable": used["unreadable"]}


__all__ = ["contained_file", "missing_skills", "skill_catalog", "skill_detail", "usage"]
