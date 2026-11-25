"""Human gate ล็อกด้วยค่าคงที่ในโค้ด ไม่ใช่ใน yaml (AD-8, REQ-047).

กฎ (ตรวจกับ flow เต็ม — default หรือ effective หลัง merge overlay):

1. track ``full`` ต้องมีขั้น ``requirements-review`` ที่ตรง :data:`GATE_STAGE` ทุก field ที่ล็อก
   (agent ``human``, ``gate: human``, ``enabled: true``, ``produces: [requirementsReady]``,
   ไม่มี condition/skill, ไม่ optional) และต้องมาหลังขั้น agent ``ba`` (ย้ายไปก่อนเก็บ requirement ไม่ได้)
2. ทุกขั้นใน ``full`` ที่ enabled ต้องมี ``requirements-review`` เป็นบรรพบุรุษใน ``after`` graph
   **ยกเว้น** ขั้นก่อน gate ที่อนุญาต: agent ∈ :data:`PRE_GATE_AGENTS` และ skill ไม่อยู่ใน
   :data:`GATED_SKILLS` — เป็น allowlist (agent ใหม่ / devops / skill-scout ก็ต้องมาหลัง gate);
   ux-designer/architect/developer/reviewer ของ AD-8 จึงถูกบังคับด้วย (ตรวจตาม agent/skill ไม่ใช่ stage id)
3. ไม่มีขั้นอื่นใดที่ ``produces: requirementsReady`` หรือใช้ agent สงวน ``human`` โดยไม่ใช่ gate ของคน
4. ทุก track ที่ไม่ใช่ ``full`` (``quick`` และ track ใหม่ใด ๆ) ต้องมี ``entry_condition`` เท่ากับ
   :data:`QUICK_ENTRY_CONDITION` ทุกค่า — แก้/ลบไม่ได้; track ``quick`` ต้องมีอยู่
"""
from __future__ import annotations

from collections.abc import Iterable

from bmad_studio.core.flow.schema import Flow, Stage, Track

LOCKED = "locked_gate_violation"
FULL = "full"
QUICK = "quick"
GATE_STAGE_ID = "requirements-review"
HUMAN_AGENT = "human"
GATE_PREDECESSOR_AGENT = "ba"
GATED_AGENTS = frozenset({"ux-designer", "architect", "developer", "reviewer"})
#: agent ที่ทำงานก่อน human gate ได้ (สร้างโปรเจกต์ + เก็บ requirement) — นอกนั้นต้องมาหลัง gate
PRE_GATE_AGENTS = frozenset({"project-bootstrapper", GATE_PREDECESSOR_AGENT})
#: skill ที่ออกแบบ/วางแผน/สร้าง/ตรวจโค้ด — ห้ามใช้ก่อน gate แม้ agent จะอยู่ใน PRE_GATE_AGENTS
GATED_SKILLS = frozenset({
    "bmad-architecture", "bmad-create-epics-and-stories", "bmad-sprint-planning", "bmad-build", "bmad-build-auto",
    "bmad-code-review", "bmad-retrospective", "bmad-qa-generate-e2e-tests", "bmad-ux", "bmad-correct-course",
    "bmad-agent-dev", "bmad-agent-architect", "bmad-agent-ux-designer",
})
GATE_FLAG = "requirementsReady"
QUICK_ENTRY_STATUSES = ("developing", "developed", "running", "deployed")
QUICK_ENTRY_CONDITION = {"requirementsReady": True, "status": list(QUICK_ENTRY_STATUSES)}

GATE_LOCKED_FIELDS = {
    "agent": HUMAN_AGENT,
    "skill": None,
    "enabled": True,
    "optional": False,
    "gate": "human",
    "produces": [GATE_FLAG],
    "condition": None,
}

LOCK_REASON_GATE = "ขั้นอนุมัติ requirement โดยคนถูกล็อก — อนุมัติที่ /review ของ app-dashboard เท่านั้น"
LOCK_REASON_ENTRY = "เงื่อนไขเข้า quick track ถูกล็อก: requirementsReady และ status ที่พัฒนาแล้ว"


def issue(code: str, message_th: str, *, track: str | None = None, stage: str | None = None,
          field: str | None = None) -> dict[str, str]:
    out = {"code": code, "error": message_th}
    if track is not None:
        out["track"] = track
    if stage is not None:
        out["stage"] = stage
    if field is not None:
        out["field"] = field
    return out


def ancestors(stages: Iterable[Stage], stage_id: str) -> set[str]:
    """บรรพบุรุษทั้งหมดของ ``stage_id`` ตาม ``after`` (ทน cycle และ id ที่ไม่มีจริง)."""
    by_id = {s.id: s for s in stages}
    seen: set[str] = set()
    todo = list(by_id[stage_id].after) if stage_id in by_id else []
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in by_id:
            todo.extend(by_id[current].after)
    return seen


def _entry_matches(track: Track) -> bool:
    cond = track.entry_condition
    return cond is not None and cond.requirementsReady is True and list(cond.status) == list(QUICK_ENTRY_STATUSES)


def check(flow: Flow) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    full = flow.tracks.get(FULL)
    if full is None:
        issues.append(issue(LOCKED, "ต้องมี track full ที่มีขั้นอนุมัติ requirement โดยคน", track=FULL))
    else:
        issues.extend(_check_full(full))
    if QUICK not in flow.tracks:
        issues.append(issue(LOCKED, "ต้องมี track quick พร้อมเงื่อนไขเข้าที่ล็อก", track=QUICK))
    for name, track in flow.tracks.items():
        if name == FULL:
            continue
        if not _entry_matches(track):
            issues.append(issue(LOCKED, f"track {name}: entry_condition ต้องเป็น requirementsReady: true และ status ∈ "
                                        f"{', '.join(QUICK_ENTRY_STATUSES)} (ล็อก แก้/ลบไม่ได้)",
                                track=name, field="entry_condition"))
        issues.extend(_check_reserved(name, track, gate_id=None))
    return issues


def _check_full(full: Track) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if full.entry_condition is not None:
        issues.append(issue(LOCKED, "track full ไม่มี entry_condition (ใช้ขั้นอนุมัติแทน)", track=FULL,
                            field="entry_condition"))
    gates = [s for s in full.stages if s.id == GATE_STAGE_ID]
    gate = gates[0] if gates else None
    if gate is None:
        issues.append(issue(LOCKED, f"ไม่มีขั้น {GATE_STAGE_ID} (อนุมัติ requirement โดยคน) — ลบไม่ได้",
                            track=FULL, stage=GATE_STAGE_ID))
    else:
        for name, expected in GATE_LOCKED_FIELDS.items():
            if getattr(gate, name) != expected:
                issues.append(issue(LOCKED, f"ขั้น {GATE_STAGE_ID}: field {name} ถูกล็อก ต้องเป็น {expected!r}",
                                    track=FULL, stage=GATE_STAGE_ID, field=name))
        by_id = {s.id: s for s in full.stages}
        before = ancestors(full.stages, GATE_STAGE_ID)
        if not any(by_id[a].agent == GATE_PREDECESSOR_AGENT and by_id[a].enabled and by_id[a].condition is None
                   for a in before if a in by_id):
            issues.append(issue(LOCKED, f"ขั้น {GATE_STAGE_ID} ต้องมาหลังขั้นเก็บ requirement (agent ba ที่เปิดอยู่และไม่มี condition) — ย้ายไม่ได้",
                                track=FULL, stage=GATE_STAGE_ID, field="after"))
    for stage in full.stages:
        if stage.id == GATE_STAGE_ID:
            continue
        if not stage.enabled or pre_gate_allowed(stage):
            continue
        if GATE_STAGE_ID not in ancestors(full.stages, stage.id):
            issues.append(issue(LOCKED, f"ขั้น {stage.id} (agent {stage.agent}) ต้องมาหลังขั้นอนุมัติ "
                                        f"{GATE_STAGE_ID} — ข้าม gate ของคนไม่ได้", track=FULL, stage=stage.id))
    issues.extend(_check_reserved(FULL, full, gate_id=GATE_STAGE_ID))
    return issues


def pre_gate_allowed(stage: Stage) -> bool:
    """ขั้นนี้อยู่ก่อน gate ได้ไหม (allowlist)."""
    return stage.agent in PRE_GATE_AGENTS and (stage.skill is None or stage.skill not in GATED_SKILLS)


def _check_reserved(track_name: str, track: Track, gate_id: str | None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for stage in track.stages:
        if stage.id == gate_id:
            continue
        if GATE_FLAG in stage.produces:
            issues.append(issue(LOCKED, f"ขั้น {stage.id}: มีเฉพาะขั้น {GATE_STAGE_ID} ที่ produces requirementsReady",
                                track=track_name, stage=stage.id, field="produces"))
        if stage.agent == HUMAN_AGENT and stage.gate != "human":
            issues.append(issue(LOCKED, f"ขั้น {stage.id}: agent human ใช้ได้เฉพาะ gate human",
                                track=track_name, stage=stage.id, field="agent"))
    return issues


def lock_reason(track: str, stage_id: str) -> str | None:
    """เหตุผลที่ขั้นนี้ถูกล็อก (สำหรับ ``locked``/``lockReason`` ของ API/resolver)."""
    if track == FULL and stage_id == GATE_STAGE_ID:
        return LOCK_REASON_GATE
    return None
