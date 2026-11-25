"""แก้ flow เริ่มต้น ``.veda/flow.default.yaml`` พร้อมตรวจข้ามโปรเจกต์ (story 4-3, REQ-045/047, AD-5/7/8).

ทุกการเปลี่ยนที่ทำให้ default เปลี่ยน — แก้, สร้าง, revert และ **ลบ** (กลับไปใช้ packaged default) — ต้องตรวจ
overlay ของทุกโปรเจกต์ใน registry กับ default ที่จะเป็นผล (AD-7):

- โปรเจกต์ที่ **ผ่าน** กับ default ปัจจุบันแต่จะ **ผิด** กับ default ใหม่ = ``errors[project_flow_broken]`` (บล็อก)
- โปรเจกต์ที่ผิดอยู่แล้ว (overlay เสีย หรือผิดกับ default ปัจจุบันด้วย) = ``warnings[project_flow_already_broken]``
  — ไม่บล็อก เพื่อให้แก้ default ที่เสียได้ (ถ้า default ปัจจุบันเสีย ใช้ packaged default เป็นฐานเทียบ)

ตรวจทั้งตอน preview และซ้ำตอน commit ใต้ lock ``flow-files`` ผ่าน file validator / delete validator ของ pipeline
(:mod:`bmad_studio.core.flow.files`). อ่านอย่างเดียว ไม่มีโค้ดในโมดูลนี้ที่เขียนไฟล์.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bmad_studio.adapters import fs
from bmad_studio.core import textedit
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow import policy
from bmad_studio.core.flow.editor import (
    PRESERVE_MAX_BYTES,
    _commented,
    count_overrides,
    disk_state,
    field_options,
    lost_comments,
    policy_fix,
    project_rel,
    tracks_view,
)
from bmad_studio.core.flow.loader import FlowYamlError, load_yaml
from bmad_studio.core.flow.merge import DEFAULT_REL, FlowInvalid, _read, load_default, merge, overlay_path, parse_overlay, to_overlay
from bmad_studio.core.flow.schema import Flow
from bmad_studio.core.flow.status import read_registry
from bmad_studio.core.flow.validate import (
    Catalog,
    FlowIssues,
    issue,
    load_packaged_default,
    parse_flow,
    validate_flow,
    yaml_issue,
)
from bmad_studio.core.path_guard import FLOW_DEFAULT_RULE, GuardedPath
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.writes import Proposal

BROKEN = "project_flow_broken"
ALREADY_BROKEN = "project_flow_already_broken"


def default_path(paths: VedaPaths):
    return paths.veda_state_dir / "flow.default.yaml"


def baseline_default(paths: VedaPaths, catalog: Catalog | None) -> Flow:
    """default ที่ใช้อยู่ตอนนี้ (ฐานเทียบว่าโปรเจกต์ "พังใหม่" ไหม) — เสียอยู่ = packaged default."""
    try:
        return load_default(paths, catalog)[0]
    except FlowInvalid:
        return load_packaged_default()


def _summary(name: str, issues: FlowIssues) -> str:
    more = f" (และอีก {len(issues) - 1} ข้อ)" if len(issues) > 1 else ""
    return f"โปรเจกต์ {name}: {issues[0]['error']}{more}"


def check_projects(paths: VedaPaths, new_default: Flow, catalog: Catalog | None,
                   baseline: Flow | None = None) -> tuple[FlowIssues, FlowIssues]:
    """(errors, warnings) ของ overlay ทุกโปรเจกต์ใน registry เมื่อ default กลายเป็น ``new_default``."""
    baseline = baseline if baseline is not None else baseline_default(paths, catalog)
    errors: FlowIssues = []
    warnings: FlowIssues = []
    try:
        entries = read_registry(paths)  # ชื่อผ่าน valid_project_name แล้ว
    except StudioError as exc:
        # อ่านรายชื่อโปรเจกต์ไม่ได้ = ตรวจไม่ได้ = บล็อก (fail closed) แต่เป็นผลตรวจที่อ่านได้ ไม่ใช่ 503
        return [issue("registry_unreadable", f"ตรวจ flow ของโปรเจกต์ไม่ได้: {exc.message_th}")], []
    for entry in entries:
        name = str(entry["name"])
        rel = project_rel(name)
        try:
            raw = _read(overlay_path(paths, name), rel)
        except FlowInvalid as exc:
            warnings.append({**issue(ALREADY_BROKEN, _summary(name, exc.issues)), "project": name})
            continue
        if raw is None:
            continue  # ไม่มี overlay = ใช้ default ตรง ๆ (default ถูกตรวจแล้ว)
        overlay, parse_issues = parse_overlay(raw)
        if overlay is None:
            warnings.append({**issue(ALREADY_BROKEN, _summary(name, parse_issues)), "project": name})
            continue
        new_issues = merge(new_default, overlay, catalog)[1]
        if not new_issues:
            continue
        item = {"project": name, "reason": new_issues[0]["code"]}
        if new_issues[0].get("stage"):
            item["stage"] = new_issues[0]["stage"]
        if merge(baseline, overlay, catalog)[1]:
            warnings.append({**issue(ALREADY_BROKEN, _summary(name, new_issues)), **item})
        else:
            errors.append({**issue(BROKEN, _summary(name, new_issues) + " — default ใหม่จะทำให้โปรเจกต์นี้ใช้ไม่ได้"), **item})
    return errors, warnings


# -- validator ของ pipeline (preview + ตรวจซ้ำตอน commit ใต้ lock) ---------------------------------------

def validate_default_bytes(paths: VedaPaths, raw: bytes) -> FlowIssues:
    """bytes ใหม่ของ ``.veda/flow.default.yaml`` (แก้/สร้าง/revert) → errors ของไฟล์เอง + โปรเจกต์ที่จะพัง."""
    from bmad_studio.core.flow.validate import validate_flow_bytes

    catalog = Catalog.scan(paths)
    flow, issues = validate_flow_bytes(raw, catalog)
    if flow is None:
        return issues
    return check_projects(paths, flow, catalog)[0]


def validate_default_delete(paths: VedaPaths) -> FlowIssues:
    """ลบ ``.veda/flow.default.yaml`` = กลับไปใช้ packaged default → ตรวจทุกโปรเจกต์กับ packaged default."""
    catalog = Catalog.scan(paths)
    packaged = load_packaged_default()
    issues = validate_flow(packaged, catalog)
    if issues:
        return [issue("default_invalid", "default ที่มากับแอปใช้กับ catalog นี้ไม่ได้ — ลบไฟล์ flow เริ่มต้นไม่ได้")] + issues
    return check_projects(paths, packaged, catalog)[0]


# -- มุมมองของ editor --------------------------------------------------------------------------------

def default_view(paths: VedaPaths) -> dict[str, Any]:
    """รูปเดียวกับ :func:`bmad_studio.core.flow.editor.editor_view` — ฐานเทียบ ("ค่า default") = packaged default."""
    catalog = Catalog.scan(paths)
    packaged = load_packaged_default()
    issues: FlowIssues = []
    load_error = False
    flow = packaged
    try:
        raw = _read(default_path(paths), DEFAULT_REL)
    except FlowInvalid as exc:
        raw, issues, load_error = None, exc.issues, True
    if raw is not None:
        try:
            parsed, issues = parse_flow(load_yaml(raw))
        except FlowYamlError as exc:
            parsed, issues = None, [yaml_issue(exc)]
        if parsed is None:
            load_error = True
        else:
            flow = parsed
    if not load_error:
        issues = validate_flow(flow, catalog)
    fix = None
    if load_error:
        fix = packaged.model_dump()
    elif any(i["code"] == policy.LOCKED for i in issues):
        fix = policy_fix(flow, packaged, issues, catalog)
    try:
        overrides = count_overrides(to_overlay(flow, packaged))
    except FlowInvalid:
        overrides = 0
    disk, editable, reason = disk_state(default_path(paths), raw, load_error, "flow เริ่มต้น")
    # โปรเจกต์ที่ใช้ไม่ได้กับ default ปัจจุบันอยู่แล้ว — บอกตั้งแต่เปิดหน้า ไม่ต้องรอแก้
    warnings: FlowIssues = []
    if not load_error and not issues:
        broken_now, already = check_projects(paths, flow, catalog, baseline=flow)
        warnings = broken_now + already
    if not editable:
        fix = None
    exists = raw is not None or load_error
    return {
        "project": "",
        "target": DEFAULT_REL,
        "hasFrontend": None,
        "base_sha256": None if disk is None else fs.sha256_hex(disk),
        "editable": editable,
        "notEditableReason": reason,
        "exists": exists,
        "flow": {"default": DEFAULT_REL if exists else "packaged", "overlay": None},
        "version": 1,
        "tracks": tracks_view(flow, packaged),
        "defaultFlow": packaged.model_dump(),
        "issues": issues,
        "loadError": load_error,
        "policyFix": fix,
        "overrides": overrides,
        "warnings": warnings,
        "options": field_options(),
    }


def check_default_draft(paths: VedaPaths, data: Any) -> dict[str, Any]:
    """payload ของ ``POST /api/flow-default/validate`` — รูปเดียวกับของโปรเจกต์ + ผลตรวจข้ามโปรเจกต์."""
    catalog = Catalog.scan(paths)
    packaged = load_packaged_default()
    flow, issues = parse_flow(data)
    if flow is None:
        return {"errors": issues, "warnings": [], "ackRequired": [], "overlay": None, "overrides": 0,
                "resetsToDefault": False, "tracks": None}
    errors = validate_flow(flow, catalog)
    warnings: FlowIssues = []
    try:
        overlay: dict[str, Any] | None = to_overlay(flow, packaged)
    except FlowInvalid as exc:
        errors.extend(exc.issues)
        overlay = None
    if not errors:
        broken, already = check_projects(paths, flow, catalog)
        errors.extend(broken)
        warnings.extend(already)
    return {"errors": errors, "warnings": warnings, "ackRequired": [], "overlay": overlay,
            "overrides": count_overrides(overlay), "resetsToDefault": overlay == {"extends": "default"},
            "tracks": tracks_view(flow, packaged)}


# -- YAML ของ flow เต็ม -------------------------------------------------------------------------------

def _clean(flow: Flow) -> dict[str, Any]:
    """dict ของ flow สำหรับเขียน: ตัด field ที่เป็น None (ค่าตั้งต้นของ schema) เหมือน template."""
    data = flow.model_dump()
    for track in data["tracks"].values():
        if track.get("entry_condition") is None:
            track.pop("entry_condition", None)
        for stage in track["stages"]:
            for key in [k for k, v in stage.items() if v is None]:
                del stage[key]
    return data


def _same_flow(text: str, flow: Flow) -> bool:
    """text แปลงกลับได้ flow เดียวกัน (ไม่สนลำดับขั้นในรายการ — ลำดับมาจาก after, AD-7)."""
    try:
        parsed, issues = parse_flow(load_yaml(text))
    except FlowYamlError:
        return False
    if parsed is None or issues:
        return False

    def norm(f: Flow) -> dict[str, Any]:
        return {name: (t.entry_condition, sorted((s.model_dump() for s in t.stages), key=lambda s: s["id"]))
                for name, t in f.tracks.items()}

    return norm(parsed) == norm(flow)


def _in_file_order(data: dict[str, Any], current_text: str) -> dict[str, Any]:
    """เรียงขั้นตามลำดับในไฟล์เดิม (ขั้นใหม่ต่อท้าย) — diff แตะเฉพาะขั้นที่เปลี่ยนจริง."""
    try:
        old = load_yaml(current_text)
    except FlowYamlError:
        return data
    old_tracks = old.get("tracks") if isinstance(old, dict) else None
    if not isinstance(old_tracks, dict):
        return data  # ไฟล์เดิมเสีย: ไม่มีลำดับให้ตาม
    for name, track in data["tracks"].items():
        old_track = old_tracks.get(name)
        old_stages = old_track.get("stages") if isinstance(old_track, dict) else None
        if not isinstance(old_stages, list):
            continue
        order = {s["id"]: i for i, s in enumerate(old_stages) if isinstance(s, dict) and isinstance(s.get("id"), str)}
        track["stages"].sort(key=lambda s: order.get(s["id"], len(order)))
    return data


def _fresh(data: Mapping[str, Any]) -> str:
    import io

    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    doc = _commented(data)
    doc.yaml_set_start_comment("flow เริ่มต้นของ Veda — แก้ผ่าน bmad-studio (/flow/default); ขั้นที่ล็อกถูกบังคับในโค้ด (AD-8)")
    out = io.StringIO()
    yaml.dump(doc, out)
    return out.getvalue()


def render_default(flow: Flow, current: fs.TextDoc) -> tuple[str, FlowIssues]:
    warnings: FlowIssues = []
    data = _clean(flow)
    if current.exists and len(current.raw) <= PRESERVE_MAX_BYTES:
        try:
            text: str | None = textedit.yaml_sync(current.text, _commented(_in_file_order(data, current.text)))
        except StudioError:
            text = None
        if text is not None and _same_flow(text, flow):
            lost = lost_comments(current.text, text)
            if lost:
                warnings.append(issue("comments_dropped", f"comment ในไฟล์เดิม {lost} บรรทัดจะหายไป — ตรวจใน diff ก่อนบันทึก"))
            return text, warnings
    text = _fresh(data)
    if not _same_flow(text, flow):
        raise StudioError("flow_render_failed", "สร้างไฟล์ flow เริ่มต้นไม่ได้ (ค่าบางค่าเขียนเป็น YAML ไม่ได้)", 422)
    if current.exists:
        warnings.append(issue("format_rewritten", "รักษารูปแบบไฟล์เดิมไม่ได้ — ไฟล์จะถูกเขียนใหม่ทั้งไฟล์และ comment เดิมจะหาย"))
    return text, warnings


class FlowDefaultKind:
    """``ChangeRequest{kind: "flow-default", target: ".veda/flow.default.yaml", payload: {flow}}``.

    draft เท่ากับ packaged default = ย้ายไฟล์ไป backup (กลับไปใช้ default ของแอป) — ผ่าน delete validator ข้ามโปรเจกต์.
    """

    name = "flow-default"
    rules = frozenset({FLOW_DEFAULT_RULE})
    check_whitespace = True

    def __init__(self, paths: VedaPaths) -> None:
        self.paths = paths

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        if target.rel != DEFAULT_REL:
            raise StudioError("path_forbidden", "ไม่อนุญาตให้เขียนไฟล์นี้", 403, {"reason": "kind_rule"})
        data = payload.get("flow")
        if not isinstance(data, Mapping):
            raise StudioError("invalid_request", "ต้องส่ง payload.flow เป็น flow เต็มที่แก้แล้ว", 422)
        catalog = Catalog.scan(self.paths)
        flow, issues = parse_flow(data)
        if flow is None:
            raise StudioError("flow_invalid", "flow ที่แก้ผิด schema — ตรวจรายการปัญหา", 422, {"issues": issues})
        errors = validate_flow(flow, catalog)
        packaged = load_packaged_default()
        try:
            same_as_packaged = to_overlay(flow, packaged) == {"extends": "default"}
        except FlowInvalid as exc:
            raise StudioError("flow_invalid", "flow ที่แก้ไม่ตรงกับ track/ขั้นของ default ของแอป (ลบขั้นไม่ได้ — ปิดด้วย"
                              " enabled: false; เพิ่ม track ใหม่ไม่ได้) — ดูรายการปัญหา", 422, {"issues": exc.issues}) from None
        broken, already = check_projects(self.paths, flow, catalog) if not errors else ([], [])
        errors.extend(broken)  # pipeline ตรวจซ้ำด้วย validator ตัวเดียวกันแล้วตัดรายการซ้ำ
        if same_as_packaged:
            if not current.exists:
                errors.append(issue("no_change", "ใช้ default ที่มากับแอปอยู่แล้ว — ไม่มีอะไรต้องบันทึก"))
            return Proposal(None, list(already), errors)
        text, render_warnings = render_default(flow, current)
        doc = current if current.exists else fs.new_doc()
        return Proposal(doc.encode(text), list(already) + render_warnings, errors)


__all__ = ["FlowDefaultKind", "check_default_draft", "check_projects", "default_view", "validate_default_bytes",
           "validate_default_delete"]
