"""มุมมองแก้ไข flow ต่อโปรเจกต์ + ตรวจ draft + kind ``flow`` ของ write pipeline (story 4-1, AD-1/5/7/8).

- :func:`editor_view` — effective flow ต่อ track พร้อม ``locked``/``lockReason``/``overridden``/``default``/
  ``mustFollowLocked`` (คำนวณที่นี่ที่เดียว; UI แสดงตรง ๆ), ``base_sha256`` ของ overlay และ ``policyFix``
  เมื่อไฟล์ที่โหลดมาผิด lock (แก้มือ)
- :func:`check_draft` — ตรวจ draft (effective ที่ผู้ใช้แก้) ด้วย ``validate_flow`` ชุดเดียวกับ resolver;
  ใช้ทั้ง ``POST /api/flows/<p>/validate`` และ :class:`FlowKind`
- :class:`FlowKind` — UI ส่ง effective ที่แก้แล้ว ไม่ส่ง overlay; overlay สร้างจาก ``to_overlay`` เท่านั้น
  (draft = default → ย้าย overlay ไป backup); bytes ที่ได้ผ่าน file validator ``flow-project`` ของ pipeline อีกชั้น

ปิดขั้นออกแบบ UI ในโปรเจกต์ที่มี frontend (``hasFrontend`` ไม่ใช่ ``false``) = คำเตือนที่ต้องยืนยัน: kind
``flow`` บังคับให้ส่ง code คำเตือนใน ``payload.acknowledged`` — UI ไม่ใช่กลไกเดียวที่ยืนยัน.
อ่าน registry อย่างเดียว (หา ``hasFrontend`` และชื่อโปรเจกต์) ไม่มีโค้ดในโมดูลนี้ที่เขียนไฟล์.
"""
from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from bmad_studio.adapters import fs
from bmad_studio.core import textedit
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow import policy
from bmad_studio.core.flow.merge import (
    FlowInvalid,
    _read,
    apply_overlay,
    load_default,
    overlay_path,
    parse_overlay,
    to_overlay,
)
from bmad_studio.core.flow.resolve import topo_order
from bmad_studio.core.flow.schema import Flow, Overlay, Stage, Track
from bmad_studio.core.flow.status import find_project
from bmad_studio.core.flow.validate import Catalog, FlowIssues, find_cycle, issue, needs_plan, parse_flow, schema_issues, validate_flow
from bmad_studio.core.path_guard import FLOW_PROJECT_RULE, GuardedPath
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.writes import Proposal

UX_AGENT = "ux-designer"
ACK_UX = "ux_disabled_frontend"
ACK_CODES = frozenset({ACK_UX})
MAX_ACKS = 8
#: รักษารูปแบบไฟล์เดิม (ruamel round-trip ลองหลายชุดตั้งค่า = หลายรอบต่อไฟล์) เฉพาะไฟล์ไม่เกินขนาดนี้ —
#: overlay จริง < 2 KiB; ไฟล์ที่แก้มือให้ใหญ่ 1.9 MiB ใช้ 55 วินาที (วัดแล้ว) จึงเขียนใหม่ทั้งไฟล์แทน
PRESERVE_MAX_BYTES = 64 * 1024
#: field ของแถวที่เป็น metadata ของ editor (ไม่ใช่ส่วนของ flow) — UI ตัดออกก่อนส่ง draft
META_KEYS = ("locked", "lockReason", "overridden", "overriddenFields", "default", "mustFollowLocked")


def project_rel(project: str) -> str:
    return f".veda/projects/{project}/flow.yaml"


# -- มุมมองต่อขั้น -------------------------------------------------------------------------------

def ordered(track: Track) -> list[Stage]:
    """ลำดับแสดงผล = topological ตาม ``after`` (tie-break ตามรายการ); มี cycle = ลำดับในรายการ."""
    by_id = {s.id: s for s in track.stages}
    if len(by_id) != len(track.stages) or find_cycle(track.stages) or any(
            d not in by_id for s in track.stages for d in s.after):
        return list(track.stages)
    order = topo_order(track)
    return [by_id[sid] for sid in order] if len(order) == len(track.stages) else list(track.stages)


def must_follow_locked(track_name: str, stage: Stage) -> bool:
    """ขั้นนี้ต้องอยู่หลังขั้นที่ล็อก (gate ของคน) ไหม — ตาม agent/skill เดียวกับ policy (AD-8 ข้อ 2–3)."""
    return track_name == policy.FULL and stage.id != policy.GATE_STAGE_ID and not policy.pre_gate_allowed(stage)


def stage_row(track_name: str, stage: Stage, base: Stage | None) -> dict[str, Any]:
    lock = policy.lock_reason(track_name, stage.id)
    fields = [] if base is None else [f for f in Stage.model_fields if f != "id" and getattr(stage, f) != getattr(base, f)]
    return {
        **stage.model_dump(),
        "locked": lock is not None,
        "lockReason": lock,
        "overridden": base is None or bool(fields),
        "overriddenFields": fields,
        "default": None if base is None else base.model_dump(exclude={"id"}),
        "mustFollowLocked": must_follow_locked(track_name, stage),
    }


def track_view(name: str, track: Track, default: Flow) -> dict[str, Any]:
    base_track = default.tracks.get(name)
    base = {s.id: s for s in base_track.stages} if base_track is not None else {}
    locked_fields = [] if name == policy.FULL else [{"field": "entry_condition", "reason": policy.LOCK_REASON_ENTRY}]
    return {
        "entry_condition": None if track.entry_condition is None else track.entry_condition.model_dump(),
        "stages": [stage_row(name, s, base.get(s.id)) for s in ordered(track)],
        "lockedFields": locked_fields,
    }


def tracks_view(flow: Flow, default: Flow) -> dict[str, Any]:
    return {name: track_view(name, track, default) for name, track in flow.tracks.items()}


def count_overrides(overlay: Mapping[str, Any] | None) -> int:
    """จำนวนจุดที่ปรับเอง (field ต่อขั้น + ขั้นที่เพิ่ม + entry_condition) — ป้าย "ปรับเอง N จุด"."""
    if not overlay:
        return 0
    total = 0
    for track in (overlay.get("tracks") or {}).values():
        total += int("entry_condition" in track)
        total += sum(len(patch) for patch in (track.get("stages") or {}).values())
        total += len(track.get("add") or [])
    return total


# -- คำเตือนที่ต้องยืนยัน ----------------------------------------------------------------------------

def ux_warnings(flow: Flow, has_frontend: bool | None) -> FlowIssues:
    """ปิดขั้นออกแบบ UI ในโปรเจกต์ที่มี (หรือไม่รู้ว่ามี) frontend = คำเตือนที่ต้องยืนยัน (REQ-046 a/b).

    "ปิด" ครอบคลุมทุกทางที่ทำให้ developer ไม่ถูกบล็อกด้วยงานออกแบบ: ``enabled: false``, เปลี่ยน agent,
    เอา ``designReady`` ออกจาก produces, condition อื่นที่ข้ามได้, หรือย้ายไปหลังขั้นลงมือสร้าง — ไม่ใช่แค่สวิตช์เดียว.
    """
    if has_frontend is False:
        return []
    full = flow.tracks.get(policy.FULL)
    if full is None:
        return []
    # ขั้นออกแบบที่นับ = agent ux-designer ที่เปิดอยู่, produces designReady และไม่ถูกข้ามด้วย condition อื่น
    active = [s for s in full.stages if s.agent == UX_AGENT and s.enabled and "designReady" in s.produces
              and s.condition in (None, "hasFrontend")]
    builds = [s for s in full.stages if s.enabled and needs_plan(s)]
    covered = bool(active) and all(any(u.id in policy.ancestors(full.stages, b.id) for u in active) for b in builds)
    if covered:
        return []
    who = "โปรเจกต์นี้มี frontend (hasFrontend: true)" if has_frontend else "ไม่รู้ว่าโปรเจกต์นี้มี frontend หรือไม่ (ไม่มี hasFrontend)"
    return [issue(ACK_UX, f"{who} แต่ flow ไม่มีขั้นออกแบบ UI (ux-designer) ที่เปิดอยู่ก่อนขั้นลงมือสร้าง — "
                          "developer จะไม่ถูกบล็อกด้วย designReady ของโปรเจกต์นี้ ต้องยืนยันแยก",
                  track=policy.FULL)]


def has_frontend_of(entry: Mapping[str, Any]) -> bool | None:
    value = entry.get("hasFrontend")
    return value if isinstance(value, bool) else None


# -- ตรวจ draft -----------------------------------------------------------------------------------

@dataclass
class DraftCheck:
    flow: Flow | None
    overlay: dict[str, Any] | None
    errors: FlowIssues = field(default_factory=list)
    warnings: FlowIssues = field(default_factory=list)
    default: Flow | None = None

    @property
    def ack_required(self) -> list[str]:
        return [w["code"] for w in self.warnings if w["code"] in ACK_CODES]


def check_draft(paths: VedaPaths, entry: Mapping[str, Any], data: Any, catalog: Catalog | None = None) -> DraftCheck:
    """draft (effective flow เต็มที่ผู้ใช้แก้) → errors/warnings + overlay ที่จะเขียน (None = สร้างไม่ได้)."""
    catalog = catalog if catalog is not None else Catalog.scan(paths)
    try:
        default, _source = load_default(paths, catalog)
    except FlowInvalid as exc:
        return DraftCheck(None, None, [issue("default_invalid", "flow เริ่มต้นผิดกฎ จึงตรวจ flow ต่อโปรเจกต์ไม่ได้")] + exc.issues)
    flow, issues = parse_flow(data)
    if flow is None:
        return DraftCheck(None, None, issues, default=default)
    errors = validate_flow(flow, catalog)
    try:
        overlay: dict[str, Any] | None = to_overlay(flow, default)
    except FlowInvalid as exc:
        errors.extend(exc.issues)
        overlay = None
    return DraftCheck(flow, overlay, errors, ux_warnings(flow, has_frontend_of(entry)), default)


def draft_result(check: DraftCheck) -> dict[str, Any]:
    """payload ของ ``POST /validate`` — lock metadata ของ draft มาจาก core เหมือนตอนโหลด."""
    return {
        "errors": check.errors,
        "warnings": check.warnings,
        "ackRequired": check.ack_required,
        "overlay": check.overlay,
        "overrides": count_overrides(check.overlay),
        "resetsToDefault": check.overlay == {"extends": "default"},
        "tracks": None if check.flow is None or check.default is None else tracks_view(check.flow, check.default),
    }


# -- policy fix ----------------------------------------------------------------------------------

def policy_fix(flow: Flow, default: Flow, issues: FlowIssues, catalog: Catalog | None) -> dict[str, Any]:
    """draft ที่แก้ให้ตรงนโยบาย lock: ขั้นที่ผิด lock คืนค่าเป็นของ default (ขั้นที่เพิ่มเอง = ปิด),
    entry_condition คืนค่า default — ยังผิด lock อยู่ = default ทั้งก้อน (ลบ overlay)."""
    data = flow.model_dump()
    for item in issues:
        if item.get("code") != policy.LOCKED:
            continue
        track_name = item.get("track") or policy.FULL
        track = data["tracks"].get(track_name)
        base = default.tracks.get(track_name)
        if track is None or base is None:
            continue
        stage_id = item.get("stage")
        if stage_id:
            ref = next((s for s in base.stages if s.id == stage_id), None)
            for index, stage in enumerate(track["stages"]):
                if stage["id"] == stage_id:
                    track["stages"][index] = ref.model_dump() if ref is not None else {**stage, "enabled": False}
        elif item.get("field") == "entry_condition":
            track["entry_condition"] = None if base.entry_condition is None else base.entry_condition.model_dump()
    try:
        fixed = Flow.model_validate(data)
    except ValidationError:
        return default.model_dump()
    if any(i["code"] == policy.LOCKED for i in validate_flow(fixed, catalog)):
        return default.model_dump()
    return fixed.model_dump()


# -- มุมมองทั้งหน้า ------------------------------------------------------------------------------

def editor_view(paths: VedaPaths, project: str) -> dict[str, Any]:
    entry = find_project(paths, project)
    name = str(entry["name"])
    catalog = Catalog.scan(paths)
    try:
        default, default_source = load_default(paths, catalog)
    except FlowInvalid as exc:
        raise StudioError("default_invalid", "flow เริ่มต้นผิดกฎ — แก้ flow เริ่มต้นก่อน", 422,
                          {"source": exc.source, "issues": exc.issues}) from None
    rel = project_rel(name)
    issues: FlowIssues = []
    load_error = False
    flow = default
    try:
        raw = _read(overlay_path(paths, name), rel)
    except FlowInvalid as exc:
        raw, issues, load_error = None, exc.issues, True
    if raw is not None:
        overlay, issues = parse_overlay(raw)
        if overlay is not None:
            data, issues, _touched = apply_overlay(default, overlay)
            if data is not None:
                try:
                    flow = Flow.model_validate(data)
                except ValidationError as exc:
                    issues = schema_issues(exc)
        load_error = bool(issues)
        if load_error:
            flow = default
    if not load_error:
        issues = validate_flow(flow, catalog)
    fix = None
    if load_error:
        fix = default.model_dump()
    elif any(i["code"] == policy.LOCKED for i in issues):
        fix = policy_fix(flow, default, issues, catalog)
    try:
        current_overlay: dict[str, Any] | None = to_overlay(flow, default)
    except FlowInvalid:
        current_overlay = None
    # base_sha256 = sha ของไฟล์ตามที่ pipeline อ่าน (ไม่ใช่ตาม loader ของ flow ที่จำกัด 256 KiB) — ไฟล์ที่ใหญ่/เสีย
    # ยังแทนที่ได้ผ่าน editor; ไฟล์ที่ pipeline อ่านเป็นข้อความไม่ได้ = แก้ผ่านแอปไม่ได้ (ต้องแก้มือ)
    editable, reason = True, None
    disk = raw
    if load_error and raw is None:
        try:
            disk = fs.read_bytes(overlay_path(paths, name))
        except (StudioError, OSError):
            disk = None
            editable, reason = False, "อ่านไฟล์ flow ของโปรเจกต์นี้ไม่ได้ (ใหญ่เกิน 2 MiB หรือไม่ใช่ไฟล์) — แก้ด้วยมือ"
    if disk is not None:
        try:
            fs.decode(disk)
        except StudioError:
            editable, reason = False, "ไฟล์ flow ของโปรเจกต์นี้ไม่ใช่ข้อความ UTF-8/UTF-16 — แก้ด้วยมือ"
    if not editable:
        fix = None
    has_frontend = has_frontend_of(entry)
    return {
        "project": name,
        "target": rel,
        "hasFrontend": has_frontend,
        "base_sha256": None if disk is None else fs.sha256_hex(disk),
        "editable": editable,
        "notEditableReason": reason,
        "exists": raw is not None or load_error,
        "flow": {"default": default_source, "overlay": rel if raw is not None or load_error else None},
        "version": 1,
        "tracks": tracks_view(flow, default),
        "defaultFlow": default.model_dump(),
        "issues": issues,
        "loadError": load_error,
        "policyFix": fix,
        "overrides": count_overrides(current_overlay),
        "warnings": ux_warnings(flow, has_frontend),
    }


# -- YAML ของ overlay -----------------------------------------------------------------------------

def _commented(value: Any) -> Any:
    """dict/list → โครงของ ruamel; รายการค่าเดี่ยว (after/produces/status) เป็น flow style ``[a, b]``."""
    if isinstance(value, Mapping):
        out = CommentedMap()
        for key, item in value.items():
            out[key] = _commented(item)
        return out
    if isinstance(value, list):
        seq = CommentedSeq(_commented(item) for item in value)
        if all(not isinstance(item, (Mapping, list)) for item in value):
            seq.fa.set_flow_style()
        return seq
    return value


def _fresh(overlay: Mapping[str, Any], project: str) -> str:
    yaml = YAML(typ="rt")
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    doc = _commented(overlay)
    doc.yaml_set_start_comment(f"flow ของโปรเจกต์ {project} — เก็บเฉพาะส่วนที่ต่างจาก default (สร้างโดย bmad-studio)")
    out = io.StringIO()
    yaml.dump(doc, out)
    return out.getvalue()


def _same_overlay(text: str, overlay: Mapping[str, Any]) -> bool:
    parsed, issues = parse_overlay(text)
    return parsed is not None and not issues and parsed == Overlay.model_validate(dict(overlay))


def _comment_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("#")]


def render_overlay(overlay: Mapping[str, Any], current: fs.TextDoc, project: str) -> tuple[str, FlowIssues]:
    """text ของ overlay ใหม่ + คำเตือน: แก้แบบรักษา comment/รูปแบบของไฟล์เดิมถ้าทำได้ (AD-6) ไม่งั้นเขียนใหม่
    ทั้งไฟล์ (``format_rewritten``). ruamel ผูก comment บรรทัดเต็มไว้กับ key ก่อนหน้า — ลบ key แล้ว comment ของ
    ขั้นถัดไปหายได้ จึงเทียบ comment ก่อน/หลังและเตือน ``comments_dropped`` (ไม่ลบเงียบ). ผลต้อง parse กลับได้
    overlay เดิมเสมอ ไม่งั้นปฏิเสธ (ไม่เขียนสิ่งที่ไม่ได้ตั้งใจ)."""
    warnings: FlowIssues = []
    if current.exists and len(current.raw) <= PRESERVE_MAX_BYTES:
        try:
            text: str | None = textedit.yaml_sync(current.text, _commented(overlay))
        except StudioError:
            text = None
        if text is not None and _same_overlay(text, overlay):
            kept = _comment_lines(text)
            lost = [c for c in _comment_lines(current.text) if c not in kept]
            if lost:
                warnings.append(issue("comments_dropped", f"comment ในไฟล์เดิม {len(lost)} บรรทัดจะหายไปกับขั้นที่คืนค่า default"
                                                          " — ตรวจใน diff ก่อนบันทึก"))
            return text, warnings
    text = _fresh(overlay, project)
    if not _same_overlay(text, overlay):
        raise StudioError("overlay_render_failed", "สร้างไฟล์ flow ของโปรเจกต์ไม่ได้ (ค่าบางค่าเขียนเป็น YAML ไม่ได้)", 422)
    if current.exists:
        warnings.append(issue("format_rewritten", "รักษารูปแบบไฟล์เดิมไม่ได้ — ไฟล์จะถูกเขียนใหม่ทั้งไฟล์และ comment เดิมจะหาย"))
    return text, warnings


# -- kind ``flow`` ของ write pipeline ------------------------------------------------------------

class FlowKind:
    """``ChangeRequest{kind: "flow", target: ".veda/projects/<p>/flow.yaml", payload: {flow, acknowledged?}}``."""

    name = "flow"
    rules = frozenset({FLOW_PROJECT_RULE})
    check_whitespace = True

    def __init__(self, paths: VedaPaths) -> None:
        self.paths = paths

    def _project(self, target: GuardedPath) -> Mapping[str, Any]:
        parts = target.rel.split("/")
        if len(parts) != 4 or parts[:2] != [".veda", "projects"] or parts[3] != "flow.yaml":
            raise StudioError("path_forbidden", "ไม่อนุญาตให้เขียนไฟล์นี้", 403, {"reason": "kind_rule"})
        entry = find_project(self.paths, parts[2])
        return entry  # find_project เทียบชื่อตรงตัวพิมพ์ — Demo/ บน NTFS ไม่ผ่านแม้เป็นโฟลเดอร์เดียวกับ demo/

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        entry = self._project(target)
        acknowledged = payload.get("acknowledged")
        acknowledged = [] if acknowledged is None else acknowledged
        if not isinstance(acknowledged, list) or len(acknowledged) > MAX_ACKS or not all(
                isinstance(code, str) and code in ACK_CODES for code in acknowledged):
            raise StudioError("invalid_request", "payload.acknowledged ต้องเป็นรายการ code ของคำเตือนที่รู้จัก", 422)
        if not isinstance(payload.get("flow"), Mapping):
            raise StudioError("invalid_request", "ต้องส่ง payload.flow เป็น flow ที่แก้แล้ว (ไม่ใช่ overlay)", 422)
        check = check_draft(self.paths, entry, payload["flow"])
        if check.overlay is None:
            raise StudioError("flow_invalid", "flow ที่แก้ผิดกฎจนสร้างไฟล์ไม่ได้ — ตรวจรายการปัญหา", 422,
                              {"issues": check.errors})
        # errors ของ draft ใส่ตรงนี้ด้วย (pipeline ตัดรายการซ้ำกับ file validator ``flow-project`` ที่ตรวจ bytes ซ้ำ)
        errors: FlowIssues = list(check.errors)
        missing = [code for code in check.ack_required if code not in acknowledged]
        if missing:
            errors.append(issue("ux_disable_unconfirmed",
                                "ปิดขั้นออกแบบ UI ของโปรเจกต์ที่มี frontend ต้องยืนยันแยกก่อน", track=policy.FULL))
        warnings = list(check.warnings)
        if check.overlay == {"extends": "default"}:
            # draft = default: ย้าย overlay ไป backup (ไม่มีไฟล์ = pipeline แจ้ง no_change) — default ผ่าน validator แล้ว
            return Proposal(None, warnings, errors)
        name = str(entry["name"])
        text, render_warnings = render_overlay(check.overlay, current, name)
        warnings.extend(render_warnings)
        doc = current if current.exists else fs.new_doc()
        return Proposal(doc.encode(text), warnings, errors)


__all__ = ["ACK_UX", "DraftCheck", "FlowKind", "META_KEYS", "check_draft", "draft_result", "editor_view",
           "policy_fix", "render_overlay", "tracks_view", "ux_warnings"]
