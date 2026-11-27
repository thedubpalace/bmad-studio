"""Override ของ skill ใน ``_bmad/custom/<skill>.toml`` / ``.user.toml`` (story 6-3, REQ-041, AD-2/5/6/10).

- พื้นผิวที่ปรับได้ (customize surface) อ่านจาก ``customize.toml`` ของ skill เท่านั้น (อ่านอย่างเดียว — AD-2 deny ทุก
  ทางเขียน) ผ่าน :func:`bmad_studio.core.skill_catalog.contained_file` (ไม่ตาม link ออกนอก ``.claude/skills``)
- กฎ merge แสดงตามที่ ``config_utils.structural_merge`` ของ BMAD ทำ: scalar แทนที่, array ต่อท้าย,
  array ของ table ที่ทุกตัวมี ``code``/``id`` = แทนที่ตาม key, table = merge ทีละ key — เพื่อแสดงเท่านั้น;
  ค่า effective มาจาก ``resolve_customization.py`` เสมอ (AD-10)
- ``activation_steps_prepend/append`` และ ``persistent_facts`` คือคำสั่งที่ agent รันทุกครั้งที่เริ่ม skill — ฟิลด์
  เหล่านี้ติดป้ายผลสูง และการเปลี่ยนใด ๆ (ทุก kind/op) เป็น impact high ผ่าน :func:`config_model.classify` ที่ระดับ rule
- key นอก surface = ``warnings[override_outside_surface]`` ที่ต้องยืนยัน (``payload.acknowledged``) ก่อนบันทึก
  (REQ-041 a); kind อื่น (text/revert) ได้คำเตือนเดียวกันผ่าน advisory ของ rule
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from bmad_studio.adapters import fs
from bmad_studio.adapters.bmad import BmadScripts, BmadUnavailable
from bmad_studio.core import config_model as cfg
from bmad_studio.core.errors import StudioError
from bmad_studio.core.path_guard import GuardedPath
from bmad_studio.core.skill_catalog import _ok, contained_file
from bmad_studio.core.flow.validate import Catalog
from bmad_studio.core.writes import Proposal

ACK_OUTSIDE = "override_outside_surface"
_KEYED = ("code", "id")
_TARGET = re.compile(r"_bmad/custom/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)(\.user)?\.toml", re.ASCII)
MAX_FIELDS = 400

Issue = dict[str, Any]


def target_of(rel: str) -> tuple[str, str] | None:
    """(skill, layer) ของไฟล์ override — ``config``/``config.user`` เป็นไฟล์ config กลาง ไม่ใช่ skill."""
    match = _TARGET.fullmatch(rel)
    if not match or match.group(1) == "config":
        return None
    return match.group(1), "user" if match.group(2) else "team"


def override_rel(skill: str, layer: str) -> str:
    return f"_bmad/custom/{skill}{'.user' if layer == 'user' else ''}.toml"


def load_surface(scripts: BmadScripts, skill: str) -> dict[str, Any] | None:
    """``customize.toml`` ของ skill ที่ parse แล้ว (None = ไม่มี/อ่านไม่ได้/ไม่ใช่ skill ที่ปรับได้)."""
    if not _ok(skill) or skill not in Catalog.scan(scripts.paths).skills:
        return None
    path = contained_file(scripts.paths, skill, "customize.toml")
    if path is None:
        return None
    try:
        raw = fs.read_bytes(path)
    except (StudioError, OSError):
        return None
    if raw is None:
        return None
    data, problem = cfg.parse_bytes(raw)
    return None if problem else data


def merge_rule(value: Any) -> tuple[str, str | None]:
    if isinstance(value, list):
        if value and all(isinstance(v, Mapping) for v in value):
            key = next((k for k in _KEYED if all(k in v for v in value)), None)
            if key:
                return "keyed", key
        return "append", None
    return "replace", None


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "list"  # รายการข้อความเท่านั้น — รายการตัวเลข/boolean แก้ในโหมดข้อความ (ไม่แปลงเป็น string)
    return "complex"


def _shape(value: Any) -> str:
    """ชนิดคร่าว ๆ สำหรับเทียบกับ default: number (int/float), boolean, string, list, table, อื่น ๆ."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "table"
    return type(value).__name__


def surface_fields(surface: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """ฟิลด์ที่ปรับได้ทั้งหมด (leaf ของ customize.toml; table ลงไปทีละ key) พร้อมกฎ merge — ไม่ตัด (view ตัดเอง)."""
    out: list[dict[str, Any]] = []
    for key, value in surface.items():
        path = (*prefix, str(key))
        if isinstance(value, Mapping) and value:
            out.extend(surface_fields(value, path))
            continue
        rule, keyed_by = merge_rule(value)
        out.append({"key": cfg.dotted(path), "id": cfg.path_key(path), "path": list(path), "rule": rule,
                    "keyedBy": keyed_by, "type": "table" if isinstance(value, Mapping) else _kind(value),
                    "default": value, "instruction": any(cfg._norm_key(k) in cfg.ACTIVATION_KEYS for k in path)})
    return out


def outside_surface(surface: Mapping[str, Any], override: Mapping[str, Any]) -> list[str]:
    """key ใน override ที่ไม่ตรง leaf ของ surface — table เปล่าใน surface (``[workflow.extra]``) รับ key ใดก็ได้ข้างใต้;
    scalar ทับ table ของ surface (``workflow = 1``) = นอก surface (review 6-3)."""
    fields = surface_fields(surface)
    allowed = {tuple(f["path"]) for f in fields}
    open_tables = {tuple(f["path"]) for f in fields if f["type"] == "table"}
    tables = {p[:i] for p in allowed for i in range(1, len(p))}
    found = []
    for path, value in cfg.leaves(override).items():
        if path in allowed or any(path[:len(t)] == t for t in open_tables):
            continue
        if path in tables and isinstance(value, Mapping) and not value:  # table เปล่าที่เป็นส่วนของ surface
            continue
        found.append(cfg.dotted(path))
    return sorted(found)


def type_mismatches(surface: Mapping[str, Any], override: Mapping[str, Any]) -> list[str]:
    """ฟิลด์ที่ override ใส่ค่าคนละชนิดกับ default (เช่น ``max_rounds = "abc"``, ``activation_steps_append = 0``)."""
    defaults = {tuple(f["path"]): f["default"] for f in surface_fields(surface)}
    out = []
    for path, value in cfg.leaves(override).items():
        if path in defaults and _shape(value) != _shape(defaults[path]):
            out.append(cfg.dotted(path))
    return sorted(out)


def file_facts(data: Mapping[str, Any]) -> list[str]:
    """รายการ ``file:`` ใน ``persistent_facts`` (prefix ไม่สนตัวพิมพ์หลัง NFKC) — สำหรับคำเตือนว่าเนื้อไฟล์จะเป็น fact;
    ความถูกต้องของ path ตรวจที่ :func:`config_model.path_issues` (validator ของ rule ทุก kind)."""
    return [where + ": file:" + value for where, value in cfg.path_values(data) if where.split(".")[-1] and
            cfg._norm_key(where.split(".")[-1]) == "persistentfacts"]


def _outside_issue(keys: list[str]) -> Issue:
    return cfg.issue(ACK_OUTSIDE, "มี key ที่ไม่อยู่ในพื้นผิวที่ skill นี้เปิดให้ปรับ (customize.toml) — BMAD อาจไม่ใช้ค่าเหล่านี้ "
                                  "หรือ skill อาจตีความต่างไป ยืนยันก่อนบันทึก", keys=keys[:200])


def advisory(scripts: BmadScripts) -> Any:
    """คำเตือนของ rule ``bmad-custom`` สำหรับทุก kind (รวม text/revert): key นอก surface, ชนิดค่าไม่ตรง default,
    และรายการ ``file:`` ใน persistent_facts (เนื้อไฟล์ที่ glob ชี้จะกลายเป็น fact ของ agent — diff ไม่แสดง)."""
    def check(rel: str, proposed: bytes | None) -> list[Issue]:
        target = target_of(rel.lower())
        if target is None or proposed is None:
            return []
        try:
            surface = load_surface(scripts, target[0])
        except (OSError, StudioError):
            return [cfg.issue("override_surface_unreadable", "อ่าน customize.toml ของ skill ไม่ได้ — ตรวจ key นอก surface ไม่ได้")]
        data, problem = cfg.parse_bytes(proposed)
        if surface is None or problem is not None:
            return []
        out: list[Issue] = []
        keys = outside_surface(surface, data)
        if keys:
            out.append(_outside_issue(keys))
        mismatched = type_mismatches(surface, data)
        if mismatched:
            out.append(cfg.issue("override_type_mismatch", "ค่าบางค่าคนละชนิดกับ default ของ skill — BMAD จะใช้ค่านี้แทนทั้งค่า",
                                 keys=mismatched[:cfg.MAX_LISTED]))
        facts = file_facts(data)
        if facts:
            out.append(cfg.issue("persistent_facts_file", "persistent_facts อ้างไฟล์ (file:) — เนื้อไฟล์ที่ตรงกับ glob จะกลายเป็น "
                                 "fact ของ agent ทุกครั้งที่เริ่ม skill และการแก้ไฟล์เหล่านั้นภายหลังจะไม่ผ่านหน้านี้",
                                 keys=facts[:cfg.MAX_LISTED]))
        return out
    return check


def resolve_issues(scripts: BmadScripts, rel: str, proposed: bytes) -> list[Issue]:
    """path validator ของไฟล์ override (ทุก kind, preview และ commit): ``resolve_customization.py`` ต้องรันผ่าน."""
    target = target_of(rel.lower())
    if target is None:
        return []
    if load_surface(scripts, target[0]) is None:
        return []
    try:
        # ชื่อไฟล์ตัวสะกดมาตรฐาน — mirror บน FS ที่สนตัวพิมพ์ต้องแทนไฟล์ที่ BMAD อ่านจริง
        scripts.resolve_customization_with(target[0], override_rel(*target), proposed)
    except BmadUnavailable as exc:
        return [cfg._resolve_issue(exc)]
    return []


def _layer(scripts: BmadScripts, rel: str) -> dict[str, Any]:
    return cfg._layer(scripts.paths.root, rel)


def override_view(scripts: BmadScripts, skill: str) -> dict[str, Any]:
    """``GET /api/skills/<id>/override`` — ฟิลด์จาก surface + ค่า team/user + effective จาก script."""
    surface = load_surface(scripts, skill)
    if surface is None:
        raise StudioError("not_customizable", "skill นี้ไม่มี customize.toml ให้ปรับ", 404)
    layers = {layer: _layer(scripts, override_rel(skill, layer)) for layer in ("team", "user")}
    issues: list[Issue] = []
    try:
        effective: dict[str, Any] | None = scripts.resolve_customization(skill)
    except BmadUnavailable as exc:
        effective, issues = None, [cfg._unavailable(exc)]
    eff = cfg.leaves(effective or {})
    layer_leaves = {layer: cfg.leaves(info["values"]) for layer, info in layers.items()}
    all_fields = surface_fields(surface)
    if len(all_fields) > MAX_FIELDS:
        issues.append(cfg.issue("surface_truncated", f"skill นี้มีฟิลด์ {len(all_fields)} รายการ — แสดง {MAX_FIELDS} แรก "
                                                     "ที่เหลือแก้ในโหมดข้อความ"))
    fields = []
    for field in all_fields[:MAX_FIELDS]:
        path = tuple(field["path"])
        values = {layer: layer_leaves[layer].get(path, cfg._MISSING) for layer in layers}
        fields.append({**field,
                       **{layer: (None if v is cfg._MISSING else v) for layer, v in values.items()},
                       "has": {**{layer: v is not cfg._MISSING for layer, v in values.items()},
                               "effective": path in eff},
                       "effective": eff.get(path)})
    outside = {layer: outside_surface(surface, layers[layer]["values"]) for layer in layers}
    return {
        "skill": skill,
        "targets": {layer: override_rel(skill, layer) for layer in layers},
        "layers": {layer: {k: v for k, v in info.items() if k != "values"} for layer, info in layers.items()},
        "fields": fields,
        "outside": outside,
        "effectiveAvailable": effective is not None,
        "issues": issues,
    }


class OverrideKind:
    """``ChangeRequest{kind: "override", target: _bmad/custom/<skill>(.user).toml, payload: {set,unset}|{text}, acknowledged?}``."""

    name = "override"
    rules = frozenset({cfg.RULE})
    check_whitespace = True

    def __init__(self, scripts: BmadScripts) -> None:
        self.scripts = scripts

    def check_target(self, target: GuardedPath) -> None:
        """ตัวสะกดตรง (ตัวพิมพ์เล็ก) ของ skill ที่มีใน catalog และมี customize.toml — ``BMAD-BUILD.toml`` ไม่ใช่ชื่อที่ BMAD อ่าน."""
        parsed = target_of(target.rel)
        if parsed is None or load_surface(self.scripts, parsed[0]) is None:
            raise StudioError("path_forbidden", "kind override เขียนได้เฉพาะ _bmad/custom/<skill>.toml หรือ .user.toml "
                              "ของ skill ที่มี customize.toml", 403, {"reason": "kind_target"})

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        self.check_target(target)
        skill, _layer_name = target_of(target.rel)  # type: ignore[misc]
        acknowledged = payload.get("acknowledged", [])
        if not isinstance(acknowledged, list) or acknowledged not in ([], [ACK_OUTSIDE]):
            raise StudioError("invalid_request", "payload.acknowledged ต้องเป็น [] หรือ [\"override_outside_surface\"]", 422)
        text, warnings = cfg.proposed_text(current, payload)
        proposed, encode_warnings = cfg.encode_for_bmad(current, text)
        errors: list[Issue] = []
        view: dict[str, Any] | None = None
        # key นอก surface → pipeline ตรวจ/บังคับยืนยันให้ทุก kind (advisory + acknowledged + ack_scope)
        # ตรวจถูก ๆ ก่อน (TOML, อักขระซ่อน, path) — ไม่รัน resolver (subprocess) กับไฟล์ที่ไม่ผ่านอยู่แล้ว (review N4)
        if not cfg.validate_toml(proposed):
            try:
                after = self.scripts.resolve_customization_with(skill, override_rel(skill, _layer_name), proposed)
                try:
                    before: dict[str, Any] | None = self.scripts.resolve_customization(skill)
                except BmadUnavailable:
                    before = None
                view = cfg.effective_view(before, after)
            except BmadUnavailable:
                view = None  # error มาจาก path validator ของ pipeline
        return Proposal(proposed, warnings + encode_warnings, errors, {"acknowledged": acknowledged}, view=view)


def compose_advisories(existing: Mapping[str, Any], override_advisory: Any) -> dict[str, Any]:
    """รวม advisory ของ rule ``bmad-custom`` กับของเดิม (ถ้ามี) — ไม่ทับกัน; advisory ที่ล้มกลายเป็นคำเตือน ไม่ใช่ 500."""
    previous = existing.get(cfg.RULE)

    def combined(rel: str, proposed: bytes | None) -> list[Issue]:
        out: list[Issue] = list(previous(rel, proposed)) if previous is not None else []
        try:
            out += override_advisory(rel, proposed)
        except Exception:  # noqa: BLE001 — คำเตือนต้องไม่ทำให้ preview/commit ล้ม
            out.append(cfg.issue("override_advisory_failed", "ตรวจ override เพิ่มเติมไม่ได้ — ตรวจ diff ให้ละเอียด"))
        return out

    return {**existing, cfg.RULE: combined}


def path_validators(scripts: BmadScripts) -> dict[str, Any]:
    """รวม validator ของไฟล์ config กลาง (6-1) และไฟล์ override (6-3) ของ rule ``bmad-custom``."""
    def check(rel: str, proposed: bytes) -> list[Issue]:
        return cfg.resolve_issues(scripts, rel, proposed) or resolve_issues(scripts, rel, proposed)
    return {cfg.RULE: check}


__all__ = ["ACK_OUTSIDE", "OverrideKind", "advisory", "load_surface", "merge_rule", "outside_surface",
           "override_view", "path_validators", "surface_fields", "target_of"]
