"""Flow resolver: ขั้นถัดไปของโปรเจกต์ (REQ-048, AD-1/AD-7/AD-8) — อ่านอย่างเดียว ไม่เขียนไฟล์ใด.

ลำดับ = topological ตาม ``after`` (tie-break ตามตำแหน่งในรายการ). ขั้นที่ปิด/condition เป็นเท็จ = ``skipped``.
ขั้นถัดไป:
1. ขั้นที่ ``active`` ซึ่งขั้นก่อนหน้าเสร็จ/ข้าม/active ทั้งหมด — เลือกขั้นที่ลึกสุด (เช่น reviewer ก่อน developer
   เมื่อมีทั้ง story รอ review และ story ค้าง: เคลียร์ review ก่อนตามวงจร developer ↔ reviewer)
2. ไม่งั้นขั้น ``pending`` แรกที่ขั้นก่อนหน้าเสร็จ/ข้ามหมด (ขั้น optional เป็นทางเลือกสุดท้าย)
3. ไม่มีเลย = flow จบ (``next: null``)
ด่านสุดท้าย (AD-8): ใน track full ถ้า requirementsReady ไม่จริง ขั้นถัดไปต้องเป็นขั้นก่อน gate หรือ gate เอง.
"""
from __future__ import annotations

import heapq
from typing import Any

from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow import policy
from bmad_studio.core.flow.merge import Effective, FlowInvalid, effective_for
from bmad_studio.core.flow.schema import Flow, Track
from bmad_studio.core.flow.status import (
    ACTIVE,
    DEVELOPED_STATUSES,
    DONE,
    PENDING,
    SKIPPED,
    ProjectState,
    find_project,
    load_state,
    stage_status,
)
from bmad_studio.core.flow.validate import Catalog
from bmad_studio.core.paths import VedaPaths

SATISFIED = frozenset({DONE, SKIPPED})


def topo_order(track: Track) -> list[str]:
    index = {s.id: i for i, s in enumerate(track.stages)}
    indegree = {s.id: len(s.after) for s in track.stages}
    children: dict[str, list[str]] = {}
    for s in track.stages:
        for dep in s.after:
            children.setdefault(dep, []).append(s.id)
    ready = [(index[s], s) for s, d in indegree.items() if d == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        _i, sid = heapq.heappop(ready)
        out.append(sid)
        for child in children.get(sid, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (index[child], child))
    return out  # validator รับรองว่าไม่มี cycle → ครบทุกขั้น


def _json_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return f"ข้อความ {value!r}"[:60]
    if isinstance(value, (int, float)):
        return f"ตัวเลข {value}"
    return "array" if isinstance(value, list) else "object"


def _skip_reason(stage: Any, state: ProjectState) -> str | None:
    if not stage.enabled:
        return "ปิดไว้ใน flow (enabled: false)"
    if stage.condition is not None:
        known = state.flag_known(stage.condition)
        if known is None:
            return None  # ไม่มีค่า/ไม่ใช่ true-false = ไม่รู้ — ไม่ข้ามเอง (resolve_track ใส่คำเตือน)
        if known is False:
            return f"condition {stage.condition} เป็น false ใน registry"
    return None


def resolve_track(effective: Effective, track_name: str, state: ProjectState) -> dict[str, Any]:
    track = effective.flow.tracks.get(track_name)
    if track is None:
        raise StudioError("unknown_track", f"flow ไม่มี track {track_name}", 400)
    by_id = {s.id: s for s in track.stages}
    order = topo_order(track)
    overridden = effective.overridden.get(track_name, set())
    base_track = effective.default.tracks.get(track_name)
    base_by_id = {s.id: s for s in base_track.stages} if base_track is not None else {}
    rows: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    for sid in order:
        stage = by_id[sid]
        if stage.enabled and stage.condition is not None and state.flag_known(stage.condition) is None:
            what = ("ไม่มี " + stage.condition if stage.condition not in state.registry
                    else f"{stage.condition} ไม่ใช่ true/false (เป็น {_json_kind(state.registry[stage.condition])})")
            warnings.append({"code": "condition_unknown", "stage": sid,
                             "error": f"registry {what} — ไม่ข้ามขั้น {sid} เอง ตั้งค่าเป็น true/false ใน registry"})
        reason = _skip_reason(stage, state)
        if reason is not None:
            status, note = SKIPPED, None
        else:
            status, note = stage_status(track_name, stage, state)
            if status == SKIPPED:
                reason = note
                note = None
        lock = policy.lock_reason(track_name, sid)
        rows[sid] = {
            "id": sid, "agent": stage.agent, "skill": stage.skill, "gate": stage.gate, "after": list(stage.after),
            "enabled": stage.enabled, "optional": stage.optional, "condition": stage.condition,
            "status": status, "skipReason": reason, "note": note,
            "locked": lock is not None, "lockReason": lock, "overridden": sid in overridden,
            # ค่าของขั้นนี้ใน default (None = ขั้นที่ overlay เพิ่ม) — AD-12
            "default": base_by_id[sid].model_dump(exclude={"id"}) if sid in base_by_id else None,
        }
    # gate ของคนที่ขั้นก่อนหน้าเสร็จแล้ว = กำลังรอคน (active)
    for sid in order:
        row = rows[sid]
        if row["status"] == PENDING and row["gate"] == "human" and _preds(by_id[sid], rows, SATISFIED):
            row["status"] = ACTIVE

    entry = None
    if track.entry_condition is not None:
        cond = track.entry_condition
        ok = (not cond.requirementsReady or state.flag("requirementsReady")) and state.status in cond.status
        entry = {"ok": ok, "requirementsReady": cond.requirementsReady, "status": list(cond.status),
                 "projectStatus": state.status or None,
                 "reason": None if ok else f"เข้า track {track_name} ไม่ได้: ต้อง requirementsReady และ status ∈ "
                                            f"{', '.join(cond.status)} (ตอนนี้ {state.status or 'ไม่มี'})"}

    # track ที่มี entry_condition (quick) = งานย่อยอิสระต่อกัน: งาน review ที่ค้างไม่ต้องรอ develop
    loose = track.entry_condition is not None
    blocked = False
    nxt = None if entry is not None and not entry["ok"] else _pick_next(order, by_id, rows, loose)
    if nxt is not None and track_name == policy.FULL and not state.flag(policy.GATE_FLAG):
        stage = by_id[nxt]
        # ด่านนี้ทำงานจริงกับ flow ที่ถูกต้อง: เมื่อ gate กำลังรอคน (active) แต่ developer/reviewer มีงาน active
        # _pick_next เลือกขั้น active ที่ลึกสุด — ด่านนี้ส่งกลับไปที่ขั้นก่อน gate/ตัว gate (review 3-2 N5)
        if not policy.pre_gate_allowed(stage) and nxt != policy.GATE_STAGE_ID:
            # ด่านสุดท้าย AD-8: ขั้นก่อน gate ที่ยังไม่เสร็จก่อน แล้วจึง gate
            pre = [sid for sid in order if sid in policy.ancestors(track.stages, policy.GATE_STAGE_ID)
                   and rows[sid]["status"] not in SATISFIED]
            nxt = pre[0] if pre else (policy.GATE_STAGE_ID if policy.GATE_STAGE_ID in by_id else None)
            blocked = nxt is None
    next_row = None
    if nxt is not None:
        row = rows[nxt]
        next_row = {"stage": nxt, "agent": row["agent"], "skill": row["skill"], "gate": row["gate"],
                    "optional": row["optional"], "status": row["status"], "note": row["note"]}
        if row["gate"] == "human":
            next_row["note"] = "รอคนอนุมัติ requirement ทุกข้อที่ /review ของ app-dashboard (ไม่มีใครตั้ง flag นี้แทนได้)"

    hints: list[str] = []
    if track_name == policy.FULL and state.status in DEVELOPED_STATUSES | {"developing"} and state.flag(policy.GATE_FLAG) \
            and not state.has_sprint:
        hints.append("โปรเจกต์นี้พัฒนาแล้วก่อนมี BMAD flow: งานเล็กใช้ --track quick; ฟีเจอร์ใหญ่ส่ง architect ทำ spine แบบ brownfield")
    locked = [{"stage": sid, "reason": rows[sid]["lockReason"]} for sid in order if rows[sid]["locked"]]
    if track.entry_condition is not None:
        locked.append({"field": "entry_condition", "reason": policy.LOCK_REASON_ENTRY})
    return {"track": track_name, "next": next_row, "stages": [rows[sid] for sid in order], "locked": locked,
            "entry": entry, "complete": nxt is None and not blocked and (entry is None or entry["ok"]),
            "hints": hints, "warnings": warnings}


def _preds(stage: Any, rows: dict[str, dict[str, Any]], allowed: frozenset[str]) -> bool:
    return all(rows[d]["status"] in allowed for d in stage.after if d in rows)


#: agent ของวงจรพัฒนา (story/epic ต่อเนื่อง) — งานของกันและกันค้างพร้อมกันได้ ไม่บล็อกกันเอง
LOOP_AGENTS = frozenset({"developer", "reviewer"})


def _ancestors_ok(sid: str, by_id: dict[str, Any], rows: dict[str, dict[str, Any]], loose: bool) -> bool:
    """ขั้น active จะเป็น next ได้เมื่อ**บรรพบุรุษทุกขั้น** (ไม่ใช่แค่ขั้นก่อนหน้าตรง) เสร็จ/ข้าม/active —
    ยกเว้นขั้นในวงจร developer/reviewer ที่ pending ได้ (เช่น retro ของ epic ที่จบแล้วระหว่าง epic ถัดไปยังพัฒนา)."""
    if loose:
        return True
    seen: set[str] = set()
    todo = list(by_id[sid].after)
    while todo:
        cur = todo.pop()
        if cur in seen or cur not in rows:
            continue
        seen.add(cur)
        status = rows[cur]["status"]
        if status == PENDING and not (rows[cur]["agent"] in LOOP_AGENTS and rows[sid]["agent"] in LOOP_AGENTS):
            return False
        todo.extend(by_id[cur].after)
    return True


def _pick_next(order: list[str], by_id: dict[str, Any], rows: dict[str, dict[str, Any]], loose: bool) -> str | None:
    active = [sid for sid in order if rows[sid]["status"] == ACTIVE and _ancestors_ok(sid, by_id, rows, loose)]
    if active:
        return active[-1]
    pending = [sid for sid in order if rows[sid]["status"] == PENDING and _preds(by_id[sid], rows, SATISFIED)]
    required = [sid for sid in pending if not rows[sid]["optional"]]
    if required:
        return required[0]
    return pending[0] if pending else None


def resolve_project(paths: VedaPaths, project: str, track: str = policy.FULL,
                    catalog: Catalog | None = None) -> dict[str, Any]:
    """ผลของ ``flow resolve`` — raise :class:`FlowInvalid` เมื่อ flow ผิด, :class:`StudioError` เมื่อหาโปรเจกต์ไม่เจอ."""
    entry = find_project(paths, project)
    return resolve_entry(paths, entry, track, catalog if catalog is not None else Catalog.scan(paths))


def resolve_entry(paths: VedaPaths, entry: dict[str, Any], track: str, catalog: Catalog,
                  loaded_default: tuple[Flow, str] | None = None) -> dict[str, Any]:
    """resolve ของ registry entry ที่หาไว้แล้ว — ฟังก์ชันเดียวที่ CLI และ API ใช้ (AD-1)."""
    project = str(entry["name"])
    effective = effective_for(paths, project, catalog, loaded_default)
    state = load_state(paths, entry)
    result = resolve_track(effective, track, state)
    return {
        "project": project,
        "hasSprint": state.has_sprint,
        "flow": {"default": effective.default_source, "overlay": effective.overlay_source},
        "registry": {k: entry.get(k) for k in ("status", "requirementsReady", "designReady", "planReady", "hasFrontend")},
        **result,
        "warnings": state.warnings + result["warnings"],
    }


STATUS_TH = {DONE: "เสร็จ", ACTIVE: "กำลังทำ", PENDING: "รอ", SKIPPED: "ข้าม"}


def render_text(result: dict[str, Any]) -> str:
    lines = [f"โปรเจกต์ {result['project']} · track {result['track']}"
             f" · flow: {result['flow']['default']}" + (f" + {result['flow']['overlay']}" if result['flow']['overlay'] else "")]
    nxt = result["next"]
    if result.get("entry") and not result["entry"]["ok"]:
        lines.append(f"ขั้นถัดไป: ไม่มี — {result['entry']['reason']}")
    elif nxt is None:
        lines.append("ขั้นถัดไป: ไม่มี — flow นี้จบแล้ว")
    else:
        skill = f" (skill {nxt['skill']})" if nxt["skill"] else ""
        optional = " [optional]" if nxt["optional"] else ""
        lines.append(f"ขั้นถัดไป: {nxt['stage']} → agent {nxt['agent']}{skill} · gate {nxt['gate']}{optional}")
        if nxt.get("note"):
            lines.append(f"  หมายเหตุ: {nxt['note']}")
    lines.append("ขั้นทั้งหมด:")
    for row in result["stages"]:
        mark = " [ล็อก]" if row["locked"] else ""
        extra = row["skipReason"] or row["note"]
        over = " [overlay]" if row["overridden"] else ""
        lines.append(f"  - {row['id']} ({row['agent']}): {STATUS_TH[row['status']]}{over}{mark}"
                     + (f" — {extra}" if extra else ""))
    for hint in result.get("hints", []):
        lines.append(f"คำแนะนำ: {hint}")
    for warning in result.get("warnings", []):
        lines.append(f"คำเตือน: {warning['error']}")
    return "\n".join(lines) + "\n"


def overview(paths: VedaPaths) -> list[dict[str, Any]]:
    """ทุกโปรเจกต์ใน registry พร้อม next/สถานะต่อขั้น (track full) — scan catalog และโหลด default ครั้งเดียว.

    flow ของโปรเจกต์ใดผิด = รายการนั้นมี ``error`` (ไม่ทำให้ทั้งรายการล้ม); default ผิด = ทุกรายการมี ``error``.
    """
    from bmad_studio.core.flow.merge import load_default
    from bmad_studio.core.flow.status import read_registry

    catalog = Catalog.scan(paths)
    loaded: tuple[Flow, str] | None = None
    default_error: dict[str, Any] | None = None
    try:
        loaded = load_default(paths, catalog)
    except FlowInvalid as exc:
        default_error = {"code": "flow_invalid", "source": exc.source, "issues": exc.issues}
    items = []
    for entry in read_registry(paths):
        base = {"project": entry["name"], "description": entry.get("description") if isinstance(
            entry.get("description"), str) else None, "status": entry.get("status") if isinstance(
            entry.get("status"), str) else None}
        error = default_error
        if error is None:
            try:
                result = resolve_entry(paths, entry, policy.FULL, catalog, loaded)
            except FlowInvalid as exc:
                error = {"code": "flow_invalid", "source": exc.source, "issues": exc.issues}
        if error is not None:
            items.append({**base, "error": error, "next": None, "stages": [], "overridden": False, "complete": False})
            continue
        items.append({**base, "error": None, "next": result["next"], "complete": result["complete"],
                      # ป้าย "ปรับเอง" เมื่อ overlay เปลี่ยนอะไรจริง (ไฟล์ที่มีแค่ extends: default ไม่นับ)
                      "overridden": any(s["overridden"] for s in result["stages"]),
                      "stages": [{"id": s["id"], "agent": s["agent"], "status": s["status"],
                                  "skipReason": s["skipReason"], "gate": s["gate"]} for s in result["stages"]]})
    return items


__all__ = ["FlowInvalid", "overview", "render_text", "resolve_entry", "resolve_project", "resolve_track", "topo_order"]
