"""default + overlay → effective flow และ effective → overlay (AD-7, REQ-045 c).

- default = ``.veda/flow.default.yaml`` ถ้ามี ไม่งั้น ``defaults/flow.default.yaml`` ที่มากับแพ็กเกจ (REQ-048 a)
- overlay ``.veda/projects/<name>/flow.yaml`` = ``extends: default`` + ``tracks.<t>.stages.<id>: {field ที่ต่าง}``
  + ``tracks.<t>.add: [stage]`` + ``tracks.<t>.entry_condition`` — field ที่เขียน (แม้เป็น ``null``) = ค่าใหม่,
  ไม่เขียน = ตาม default ("รีเซ็ต" = ลบ key)
- :func:`to_overlay` เป็นทางเดียวที่สร้าง overlay: ค่าที่เท่ากับ default ถูกตัดทิ้ง

ทุกฟังก์ชันอ่านอย่างเดียว; ผล merge ต้องผ่าน :func:`~bmad_studio.core.flow.validate.validate_flow` เสมอ
(AD-8 ตรวจกับ effective ไม่ใช่กับ overlay — overlay ที่ดูเล็กก็ข้าม gate ไม่ได้).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bmad_studio.adapters import fs
from bmad_studio.core.flow.loader import MAX_FLOW_BYTES, FlowYamlError, load_yaml
from bmad_studio.core.flow.schema import Flow, Overlay, Stage
from bmad_studio.core.flow.validate import (
    DEFAULT_TEMPLATE,
    Catalog,
    FlowIssues,
    issue,
    schema_issues,
    validate_flow,
    validate_flow_bytes,
    yaml_issue,
)
from bmad_studio.core.paths import VedaPaths

DEFAULT_REL = ".veda/flow.default.yaml"
PACKAGED = "packaged"
_STAGE_FIELDS = tuple(name for name in Stage.model_fields if name != "id")
#: field ของ Stage ที่ค่า None ถูกต้อง (overlay เขียน null เพื่อล้างได้) — มาจาก schema ไม่ hard-code
_NULLABLE = frozenset(name for name, info in Stage.model_fields.items() if info.default is None)


class FlowInvalid(Exception):
    """flow (default หรือ overlay) ผิด schema/กฎ — resolver คืน error แทนการเดา (REQ-048 d)."""

    def __init__(self, source: str, issues: FlowIssues) -> None:
        super().__init__(source)
        self.source = source
        self.issues = issues


@dataclass
class Effective:
    flow: Flow
    default: Flow
    default_source: str               # "packaged" หรือ ".veda/flow.default.yaml"
    overlay_source: str | None = None  # ".veda/projects/<n>/flow.yaml" หรือ None
    overlay: Overlay | None = None
    overridden: dict[str, set[str]] = field(default_factory=dict)  # track → stage id ที่ overlay แตะ


def _read(path: Path, rel: str) -> bytes | None:
    """bytes ของไฟล์ flow (None = ไม่มี) — ใหญ่เกิน/ไม่ใช่ไฟล์ = :class:`FlowInvalid` ไม่เดา."""
    from bmad_studio.core.errors import StudioError

    try:
        return fs.read_bytes(path, MAX_FLOW_BYTES)
    except StudioError as exc:
        if exc.code == "file_too_large":
            raise FlowInvalid(rel, [issue("yaml_invalid", "ไฟล์ flow ใหญ่เกินกำหนด") | {"reason": "yaml_too_large"}]) from None
        raise FlowInvalid(rel, [issue("flow_unreadable", "อ่านไฟล์ flow ไม่ได้ (ไม่ใช่ไฟล์หรือไม่มีสิทธิ์)")
                                | {"reason": exc.code}]) from None
    except OSError:
        raise FlowInvalid(rel, [issue("flow_unreadable", "อ่านไฟล์ flow ไม่ได้")]) from None


def load_default(paths: VedaPaths, catalog: Catalog | None) -> tuple[Flow, str]:
    path = paths.veda_state_dir / "flow.default.yaml"
    raw = _read(path, DEFAULT_REL)
    if raw is None:
        flow, issues = validate_flow_bytes(DEFAULT_TEMPLATE.read_bytes(), catalog)
        source = PACKAGED
    else:
        flow, issues = validate_flow_bytes(raw, catalog)
        source = DEFAULT_REL
    if flow is None:
        raise FlowInvalid(source, issues)
    return flow, source


def parse_overlay(raw: bytes | str) -> tuple[Overlay | None, FlowIssues]:
    try:
        data = load_yaml(raw)
    except FlowYamlError as exc:
        return None, [yaml_issue(exc)]
    if not isinstance(data, dict):
        return None, [issue("schema_invalid", "flow ต่อโปรเจกต์ต้องเป็น mapping ที่มี extends: default")]
    try:
        return Overlay.model_validate(data), []
    except ValidationError as exc:
        return None, schema_issues(exc)


def apply_overlay(default: Flow, overlay: Overlay) -> tuple[dict[str, Any] | None, FlowIssues, dict[str, set[str]]]:
    """คืน (ข้อมูล flow เต็มหลัง merge, issues, stage ที่ถูก override ต่อ track)."""
    data = default.model_dump()
    issues: FlowIssues = []
    touched: dict[str, set[str]] = {}
    for track_name, patch in overlay.tracks.items():
        track = data["tracks"].get(track_name)
        if track is None:
            issues.append(issue("unknown_track", f"overlay อ้าง track {track_name} ที่ไม่มีใน default", track=track_name))
            continue
        marks = touched.setdefault(track_name, set())
        if "entry_condition" in patch.model_fields_set:
            track["entry_condition"] = None if patch.entry_condition is None else patch.entry_condition.model_dump()
            marks.add("entry_condition")
        by_id = {stage["id"]: stage for stage in track["stages"]}
        for stage_id, stage_patch in patch.stages.items():
            target = by_id.get(stage_id)
            if target is None:
                issues.append(issue("unknown_stage", f"overlay แก้ขั้น {stage_id} ที่ไม่มีใน default (ใช้ add สำหรับขั้นใหม่)",
                                    track=track_name, stage=stage_id))
                continue
            for name in stage_patch.model_fields_set:
                value = getattr(stage_patch, name)
                if value is None and name not in _NULLABLE:
                    issues.append(issue("schema_invalid", f"ขั้น {stage_id}: {name} เป็น null ไม่ได้",
                                        track=track_name, stage=stage_id, field=name))
                    continue
                target[name] = list(value) if isinstance(value, list) else value
            if stage_patch.model_fields_set:
                marks.add(stage_id)
        for added in patch.add:
            track["stages"].append(added.model_dump())
            marks.add(added.id)
    return (None if issues else data), issues, touched


def merge(default: Flow, overlay: Overlay, catalog: Catalog | None) -> tuple[Flow | None, FlowIssues, dict[str, set[str]]]:
    data, issues, touched = apply_overlay(default, overlay)
    if data is None:
        return None, issues, touched
    try:
        flow = Flow.model_validate(data)
    except ValidationError as exc:
        return None, schema_issues(exc), touched
    issues = validate_flow(flow, catalog)
    return (None if issues else flow), issues, touched


def to_overlay(effective: Flow, default: Flow) -> dict[str, Any]:
    """overlay ที่เก็บเฉพาะส่วนต่าง (ค่าเท่ากับ default = ไม่เขียน). ลบขั้นของ default ไม่ได้ — ใช้ ``enabled: false``."""
    tracks: dict[str, Any] = {}
    gone = [name for name in default.tracks if name not in effective.tracks]
    if gone:
        raise FlowInvalid("overlay", [issue("cannot_remove_track", f"ลบ track {name} ของ default ไม่ได้", track=name)
                                      for name in gone])
    for name, track in effective.tracks.items():
        base = default.tracks.get(name)
        if base is None:
            raise FlowInvalid("overlay", [issue("unknown_track", f"track {name} ไม่มีใน default", track=name)])
        out: dict[str, Any] = {}
        if track.entry_condition != base.entry_condition:
            out["entry_condition"] = None if track.entry_condition is None else track.entry_condition.model_dump()
        base_by_id = {s.id: s for s in base.stages}
        eff_ids = {s.id for s in track.stages}
        missing = [s for s in base_by_id if s not in eff_ids]
        if missing:
            raise FlowInvalid("overlay", [issue("cannot_remove_stage", f"ลบขั้น {sid} ของ default ไม่ได้ — ปิดด้วย enabled: false",
                                                track=name, stage=sid) for sid in missing])
        patches: dict[str, Any] = {}
        added: list[dict[str, Any]] = []
        for stage in track.stages:
            ref = base_by_id.get(stage.id)
            if ref is None:
                added.append(stage.model_dump())
                continue
            diff = {f: _plain(getattr(stage, f)) for f in _STAGE_FIELDS if getattr(stage, f) != getattr(ref, f)}
            if diff:
                patches[stage.id] = diff
        if patches:
            out["stages"] = patches
        if added:
            out["add"] = added
        if out:
            tracks[name] = out
    result: dict[str, Any] = {"extends": "default"}
    if tracks:
        result["tracks"] = tracks
    return result


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value


def overlay_path(paths: VedaPaths, project: str) -> Path:
    return paths.veda_state_dir / "projects" / project / "flow.yaml"


def effective_for(paths: VedaPaths, project: str | None, catalog: Catalog | None,
                  loaded_default: tuple[Flow, str] | None = None) -> Effective:
    """flow ที่ใช้จริงของโปรเจกต์ (ไม่มี overlay = default). ผิด = :class:`FlowInvalid`.

    ``loaded_default`` = ผลของ :func:`load_default` ที่โหลดไว้แล้ว (รายการหลายโปรเจกต์ใน request เดียว).
    """
    default, source = loaded_default if loaded_default is not None else load_default(paths, catalog)
    if project is None:
        return Effective(default, default, source)
    rel = f".veda/projects/{project}/flow.yaml"
    raw = _read(overlay_path(paths, project), rel)
    if raw is None:
        return Effective(default, default, source)
    overlay, issues = parse_overlay(raw)
    if overlay is None:
        raise FlowInvalid(rel, issues)
    flow, issues, touched = merge(default, overlay, catalog)
    if flow is None:
        raise FlowInvalid(rel, issues)
    return Effective(flow, default, source, rel, overlay, {k: v for k, v in touched.items() if v})


def validate_overlay_bytes(raw: bytes, paths: VedaPaths, catalog: Catalog | None) -> FlowIssues:
    """validator ของ ``.veda/projects/<n>/flow.yaml`` สำหรับ write pipeline: merge กับ default ปัจจุบันแล้วตรวจ."""
    overlay, issues = parse_overlay(raw)
    if overlay is None:
        return issues
    try:
        default, _source = load_default(paths, catalog)
    except FlowInvalid as exc:
        return [issue("default_invalid", "flow เริ่มต้นผิดกฎ จึงตรวจ flow ต่อโปรเจกต์ไม่ได้")] + exc.issues
    return merge(default, overlay, catalog)[1]


__all__ = ["Effective", "FlowInvalid", "apply_overlay", "effective_for", "load_default", "merge", "overlay_path",
           "parse_overlay", "to_overlay", "validate_overlay_bytes"]
