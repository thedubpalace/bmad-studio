"""ปิดขั้นออกแบบ UI ของโปรเจกต์ที่มี frontend ผ่าน **ทุกทางเขียน** ต้องถามหรือเตือน (REQ-046 b, CLAUDE.md, review 4-3 B1).

ทางเขียนที่ทำให้ ux ของโปรเจกต์หนึ่งปิดได้ — ทั้งหมดผ่านฟังก์ชันชุดนี้ชุดเดียว:

======================================  ================================  =====================================
ทาง                                     ใครถูกตรวจ                         ผล
======================================  ================================  =====================================
kind ``flow`` (overlay)                 โปรเจกต์นั้น                        warning + ต้อง ``acknowledged``
kind ``flow-default``                   ทุกโปรเจกต์ที่ ux ปิด **ใหม่**         warning ต่อโปรเจกต์ + ต้อง ``acknowledged``
revert / ลบ overlay                     โปรเจกต์นั้น                        warning (typed confirmation ของ revert = การยืนยัน)
revert / ลบ ``.veda/flow.default.yaml``  ทุกโปรเจกต์ที่ ux ปิดใหม่              warning (เช่นเดียวกัน)
======================================  ================================  =====================================

pipeline เรียก :func:`advisories` กับทุก kind (ผล = ``warnings[]``); kind ``flow``/``flow-default`` เรียกฟังก์ชันเดียวกัน
เพื่อตัดสินว่าต้องมี ack ไหม — ข้อความจึงตรงกันและ pipeline ตัดรายการซ้ำได้.

การยืนยัน (ack ของ flow/flow-default และ typed confirmation ของ revert/ลบ) ผูกกับ **ชุดโปรเจกต์ที่แสดงใน preview**
(:func:`ack_scope` เก็บใน metadata ของ preview ที่ลงลายเซ็น) — ตอน commit pipeline คำนวณชุดใหม่ใต้ lock ถ้ามีโปรเจกต์
ที่ไม่ได้แสดง (เช่น เพิ่มเข้าทะเบียนโปรเจกต์ หรือ hasFrontend เปลี่ยนหลัง preview) = 409 ``ux_ack_stale`` ต้องเปิด preview ใหม่
(retro epic-4 A3; ครอบคลุม flow, flow-default, revert, ลบ).
"ปิด" นิยามที่ :func:`bmad_studio.core.flow.editor.ux_warnings` (รวม ``optional: true`` — review 4-1 N2).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow.editor import ACK_UX, has_frontend_of, ux_warnings
from bmad_studio.core.flow.loader import FlowYamlError, load_yaml
from bmad_studio.core.flow.merge import FlowInvalid, _read, load_default, merge, overlay_path, parse_overlay
from bmad_studio.core.flow.schema import Flow
from bmad_studio.core.flow.status import find_project, read_registry
from bmad_studio.core.flow.validate import Catalog, FlowIssues, load_packaged_default, parse_flow
from bmad_studio.core.path_guard import FLOW_DEFAULT_RULE, FLOW_PROJECT_RULE
from bmad_studio.core.paths import VedaPaths

Advisory = Callable[[str, "bytes | None"], FlowIssues]


def _tagged(project: str, issues: FlowIssues) -> FlowIssues:
    return [{**i, "project": project, "error": f"โปรเจกต์ {project}: {i['error']}"} for i in issues]


def _effective(default: Flow, overlay_raw: bytes | None, catalog: Catalog | None) -> Flow | None:
    """effective ของโปรเจกต์ (None = ผิดกฎ — validator รายงานแยก ไม่เดาเรื่อง ux)."""
    if overlay_raw is None:
        return default
    overlay, issues = parse_overlay(overlay_raw)
    if overlay is None:
        return None
    return merge(default, overlay, catalog)[0]


def _current_default(paths: VedaPaths, catalog: Catalog | None) -> Flow | None:
    try:
        return load_default(paths, catalog)[0]
    except FlowInvalid:
        return None


def project_ux(paths: VedaPaths, project: str, new_effective: Flow | None) -> FlowIssues:
    """flow ใหม่ของโปรเจกต์ (หลัง merge) ปิด ux ไหม — ทุกครั้งที่ผลยังปิด (ผู้ยืนยันคือคนที่บันทึกครั้งนี้)."""
    if new_effective is None:
        return []
    try:
        entry = find_project(paths, project)
    except StudioError:
        return []
    return _tagged(project, ux_warnings(new_effective, has_frontend_of(entry)))


def default_ux(paths: VedaPaths, new_default: Flow | None, catalog: Catalog | None) -> FlowIssues:
    """โปรเจกต์ที่ ux จะปิด **ใหม่** เพราะ default เปลี่ยน (ทั้งที่มีและไม่มี overlay) — ไม่นับที่ปิดอยู่แล้ว."""
    if new_default is None:
        return []
    old_default = _current_default(paths, catalog) or load_packaged_default()
    try:
        entries = read_registry(paths)
    except StudioError:
        return []  # check_projects รายงาน registry_unreadable เป็น error อยู่แล้ว
    out: FlowIssues = []
    for entry in entries:
        frontend = has_frontend_of(entry)
        if frontend is False:
            continue
        name = str(entry["name"])
        try:
            raw = _read(overlay_path(paths, name), f".veda/projects/{name}/flow.yaml")
        except FlowInvalid:
            continue  # overlay เสีย = โปรเจกต์ใช้ไม่ได้อยู่แล้ว (warning ของ check_projects)
        new = _effective(new_default, raw, catalog)
        if new is None or not ux_warnings(new, frontend):
            continue
        old = _effective(old_default, raw, catalog)
        if old is not None and ux_warnings(old, frontend):
            continue  # ปิดอยู่แล้วก่อนการแก้นี้
        out.extend(_tagged(name, ux_warnings(new, frontend)))
    return out


def _parse_default(raw: bytes) -> Flow | None:
    try:
        return parse_flow(load_yaml(raw))[0]
    except FlowYamlError:
        return None


def advisories(paths: VedaPaths) -> dict[str, Advisory]:
    """คำเตือนต่อ rule สำหรับทุก kind ของ write pipeline: ``(rel, proposed bytes | None=ลบ) -> warnings``."""

    def flow_project(rel: str, proposed: bytes | None) -> FlowIssues:
        project = rel.split("/")[2] if rel.count("/") == 3 else ""
        if not project:
            return []
        # default ปัจจุบันเสีย = ประเมินกับ packaged default (ไม่เงียบ — fail closed ฝั่งคำเตือน)
        default = _current_default(paths, None) or load_packaged_default()
        return project_ux(paths, project, _effective(default, proposed, None))

    def flow_default(_rel: str, proposed: bytes | None) -> FlowIssues:
        new_default = load_packaged_default() if proposed is None else _parse_default(proposed)
        return default_ux(paths, new_default, None)

    return {FLOW_PROJECT_RULE: safe(flow_project), FLOW_DEFAULT_RULE: safe(flow_default)}


def safe(advisory: Advisory) -> Advisory:
    """advisory ล้มต้องไม่ทำให้ preview เป็น 500 — แจ้งเป็นคำเตือนว่าตรวจไม่ได้แทน."""
    def run(rel: str, proposed: bytes | None) -> FlowIssues:
        try:
            return list(advisory(rel, proposed))
        except Exception:  # noqa: BLE001 — validator ของ rule รายงานความผิดของไฟล์แยกอยู่แล้ว
            return [{"code": "ux_check_failed", "error": "ตรวจไม่ได้ว่าการแก้นี้ปิดขั้นออกแบบ UI ของโปรเจกต์ใดไหม — ตรวจ diff ให้ดี"}]
    return run


#: ค่าใน :func:`ack_scope` แทน "ตรวจไม่ได้" — ไม่ใช่ชื่อโปรเจกต์ที่ถูกต้อง (ชื่อโปรเจกต์ห้ามมี ``*``)
SCOPE_UNCHECKED = "*unchecked*"


def ack_scope(warnings: FlowIssues) -> list[str]:
    """ชุดโปรเจกต์ที่การยืนยันครอบคลุม (เรียง, ไม่ซ้ำ): ชื่อจากคำเตือน ux + :data:`SCOPE_UNCHECKED` ถ้าตรวจไม่ได้."""
    names = {str(w["project"]) for w in warnings if w.get("code") == ACK_UX and w.get("project")}
    if any(w.get("code") == ACK_UX and not w.get("project") for w in warnings) or any(
            w.get("code") == "ux_check_failed" for w in warnings):
        names.add(SCOPE_UNCHECKED)
    return sorted(names)


def scope_grew(shown: list[str], now: list[str]) -> list[str]:
    """โปรเจกต์ที่ ux จะปิดตอนนี้แต่ไม่ได้แสดงใน preview (ว่าง = การยืนยันเดิมยังครอบคลุม)."""
    return sorted(set(now) - set(shown))


def ack_errors(warnings: FlowIssues, acknowledged: list[str]) -> FlowIssues:
    """kind ที่ผู้ใช้แก้เอง (flow / flow-default): คำเตือน ux ต้องยืนยันใน ``payload.acknowledged``."""
    if any(w["code"] == ACK_UX for w in warnings) and ACK_UX not in acknowledged:
        return [{"code": "ux_disable_unconfirmed", "track": "full",
                 "error": "ปิดขั้นออกแบบ UI ของโปรเจกต์ที่มี frontend ต้องยืนยันแยกก่อน"}]
    return []


def parse_acknowledged(payload: Any, allowed: frozenset[str], limit: int) -> list[str]:
    value = payload.get("acknowledged")
    value = [] if value is None else value
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(c, str) and c in allowed for c in value):
        raise StudioError("invalid_request", "payload.acknowledged ต้องเป็นรายการ code ของคำเตือนที่รู้จัก", 422)
    return value


__all__ = ["SCOPE_UNCHECKED", "ack_errors", "ack_scope", "advisories", "default_ux", "parse_acknowledged",
           "project_ux", "scope_grew"]
