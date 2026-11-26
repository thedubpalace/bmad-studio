"""Rework 4-3 B1 / 4-1 N1 N2 — ทุกทางเขียนที่ปิดขั้นออกแบบ UI ของโปรเจกต์ที่มี frontend ต้องถามหรือเตือน.

ทาง: kind flow, kind flow-default, revert (overlay และ default), ลบ overlay (draft = default / revert ของรายการที่สร้าง).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.core.settings import Settings

from .flowkit import default_data, dump, seed_catalog
from .test_changes_api import Studio
from .test_flow_resolve import project_dir, registry

UX = "ux_disabled_frontend"
DEFAULT = ".veda/flow.default.yaml"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def studio(settings: Settings) -> Studio:
    root = settings.veda_root
    seed_catalog(root)
    registry(root,
             {"name": "ui", "status": "developing", "requirementsReady": True, "hasFrontend": True},
             {"name": "nofe", "status": "developing", "requirementsReady": True},
             {"name": "cli", "status": "developing", "requirementsReady": True, "hasFrontend": False},
             {"name": "ui-off", "status": "developing", "requirementsReady": True, "hasFrontend": True})
    (project_dir(root, "ui-off") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n", encoding="utf-8")
    return Studio(settings)


def strip(view: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "agent", "skill", "enabled", "optional", "after", "gate", "produces", "condition")
    return {"version": 1, "tracks": {n: {"entry_condition": t["entry_condition"],
                                         "stages": [{k: s[k] for k in keep} for s in t["stages"]]}
                                     for n, t in view["tracks"].items()}}


def stage(draft: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(s for s in draft["tracks"]["full"]["stages"] if s["id"] == sid)


def preview(studio: Studio, kind: str, target: str, payload: dict[str, Any], base: str | None) -> dict[str, Any]:
    response = studio.post("/api/changes/preview", {"kind": kind, "target": target, "payload": payload, "base_sha256": base})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def ux_projects(items: list[dict[str, Any]]) -> set[str]:
    return {i.get("project") for i in items if i["code"] == UX}


def codes(items: list[dict[str, Any]]) -> set[str]:
    return {i["code"] for i in items}


def file_sha(path: Path) -> str | None:
    return sha(path.read_bytes()) if path.exists() else None


# -- kind flow-default (B1) --------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["disable", "optional", "agent"])
def test_default_turning_ux_off_needs_ack_for_frontend_projects(studio: Studio, how: str) -> None:
    draft = strip(studio.client.get("/api/flow-default").json()["data"])
    ux = stage(draft, "ux-design")
    if how == "disable":
        ux["enabled"] = False
    elif how == "optional":
        ux["optional"] = True
    else:
        ux["agent"] = "ba"
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    # ui + nofe (ไม่รู้ = ถือว่ามี) ใหม่; cli ไม่มี frontend; ui-off ปิดอยู่แล้ว — ไม่นับ
    assert ux_projects(checked["warnings"]) == {"ui", "nofe"} and checked["ackRequired"] == [UX]
    data = preview(studio, "flow-default", DEFAULT, {"flow": draft}, None)
    assert ux_projects(data["warnings"]) == {"ui", "nofe"} and "ux_disable_unconfirmed" in codes(data["errors"])
    assert studio.commit(data, typed="flow.default.yaml").status_code == 422
    data = preview(studio, "flow-default", DEFAULT, {"flow": draft, "acknowledged": [UX]}, None)
    assert data["errors"] == [] and ux_projects(data["warnings"]) == {"ui", "nofe"}
    assert studio.commit(data, typed="flow.default.yaml").status_code == 200
    assert UX in codes(studio.client.get("/api/flows/ui").json()["data"]["warnings"])  # editor ของโปรเจกต์บอกต่อ


def test_unrelated_default_edit_needs_no_ack(studio: Studio) -> None:
    draft = strip(studio.client.get("/api/flow-default").json()["data"])
    stage(draft, "devops")["optional"] = False
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    assert checked["ackRequired"] == [] and ux_projects(checked["warnings"]) == set()


def _default_without_ux() -> bytes:
    data = default_data()
    stage(data, "ux-design")["enabled"] = False
    return dump(data).encode()


def test_revert_of_default_that_turns_ux_off_warns(studio: Studio) -> None:
    target = studio.settings.veda_root / DEFAULT
    off = _default_without_ux()
    target.write_bytes(off)  # แก้มือ: ปิด ux ทุกโปรเจกต์
    draft = strip(studio.client.get("/api/flow-default").json()["data"])
    stage(draft, "ux-design")["enabled"] = True
    stage(draft, "devops")["optional"] = False
    data = preview(studio, "flow-default", DEFAULT, {"flow": draft}, sha(off))
    assert data["errors"] == []
    entry = studio.commit(data, typed="flow.default.yaml").json()["data"]
    revert = preview(studio, "revert", DEFAULT, {"change_id": entry["change_id"]}, file_sha(target))
    # revert ใช้ typed confirmation ของตัวเองเป็นการยืนยัน แต่ต้องเห็นคำเตือนใน preview (review 4-1 N1)
    assert ux_projects(revert["warnings"]) == {"ui", "nofe"} and revert["errors"] == []  # ไม่ใช่ cli/ui-off
    assert studio.commit(revert, typed="flow.default.yaml").status_code == 200


def test_delete_of_default_that_turns_ux_off_warns(studio: Studio) -> None:
    """default ปัจจุบันเพิ่มขั้นออกแบบอีกขั้นที่โปรเจกต์ ui-off ใช้แทน ux-design — ลบ default = ux ของ ui-off ปิด."""
    root = studio.settings.veda_root
    data = default_data()
    stages = data["tracks"]["full"]["stages"]
    stages.append({"id": "ui-design", "agent": "ux-designer", "enabled": True, "optional": False,
                   "after": ["requirements-review"], "gate": "none", "produces": ["designReady"]})
    stage(data, "architecture")["after"] = ["ux-design", "ui-design"]
    raw = dump(data).encode()
    (root / DEFAULT).write_bytes(raw)
    result = studio.client.get("/api/flow-default").json()["data"]
    reset = preview(studio, "flow-default", DEFAULT, {"flow": result["defaultFlow"]}, sha(raw))
    assert reset["op"] == "delete" and "ui-off" in ux_projects(reset["warnings"])
    assert "ux_disable_unconfirmed" in codes(reset["errors"])
    reset = preview(studio, "flow-default", DEFAULT, {"flow": result["defaultFlow"], "acknowledged": [UX]}, sha(raw))
    assert reset["errors"] == []


# -- kind flow / revert / ลบ overlay (4-1 N1, N2) ------------------------------------------------------

def test_optional_ux_on_project_needs_ack(studio: Studio) -> None:
    draft = strip(studio.client.get("/api/flows/ui").json()["data"])
    stage(draft, "ux-design")["optional"] = True
    data = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": draft}, None)
    assert "ux_disable_unconfirmed" in codes(data["errors"]) and ux_projects(data["warnings"]) == {"ui"}


def test_revert_of_overlay_that_turns_ux_off_warns(studio: Studio) -> None:
    target = studio.settings.veda_root / ".veda/projects/ui/flow.yaml"
    view = studio.client.get("/api/flows/ui").json()["data"]
    off = strip(view)
    stage(off, "ux-design")["enabled"] = False
    first = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": off, "acknowledged": [UX]}, None)
    assert studio.commit(first, typed="flow.yaml").status_code == 200
    on = strip(studio.client.get("/api/flows/ui").json()["data"])
    stage(on, "ux-design")["enabled"] = True
    second = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": on}, file_sha(target))
    assert second["op"] == "delete" and second["errors"] == []
    entry = studio.commit(second, typed="flow.yaml").json()["data"]
    revert = preview(studio, "revert", ".veda/projects/ui/flow.yaml", {"change_id": entry["change_id"]}, file_sha(target))
    assert revert["op"] == "create" and ux_projects(revert["warnings"]) == {"ui"} and revert["errors"] == []


def test_deleting_overlay_that_kept_ux_on_warns_and_needs_ack(studio: Studio) -> None:
    root = studio.settings.veda_root
    (root / DEFAULT).write_bytes(_default_without_ux())  # default ปิด ux; overlay ของ ui เปิดกลับ
    overlay = root / ".veda/projects/ui/flow.yaml"
    overlay.write_text("extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: true}\n", encoding="utf-8")
    view = studio.client.get("/api/flows/ui").json()["data"]
    assert view["warnings"] == []
    draft = view["defaultFlow"]  # รีเซ็ตเป็น default = ลบ overlay = ux ปิด
    data = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": draft}, file_sha(overlay))
    assert data["op"] == "delete" and ux_projects(data["warnings"]) == {"ui"}
    assert "ux_disable_unconfirmed" in codes(data["errors"])


def test_project_view_shows_existing_ux_off_state(studio: Studio) -> None:
    view = studio.client.get("/api/flows/ui-off").json()["data"]
    assert ux_projects(view["warnings"]) == {"ui-off"}


# -- cache ของ parse (4-3 N1) ---------------------------------------------------------------------

def test_overlay_parse_cache_returns_independent_issue_lists() -> None:
    from bmad_studio.core.flow.merge import parse_overlay

    first = parse_overlay(b"extends: nope\n")
    first[1].append({"code": "mutated", "error": "x"})
    second = parse_overlay(b"extends: nope\n")
    assert first[0] is None and "mutated" not in {i["code"] for i in second[1]}
    assert parse_overlay(b"extends: default\n")[0] is not None



def test_revert_of_create_that_deletes_overlay_and_turns_ux_off_warns(studio: Studio) -> None:
    root = studio.settings.veda_root
    (root / DEFAULT).write_bytes(_default_without_ux())  # default ปิด ux ทุกโปรเจกต์
    overlay = root / ".veda/projects/ui/flow.yaml"
    draft = strip(studio.client.get("/api/flows/ui").json()["data"])
    stage(draft, "ux-design")["enabled"] = True  # สร้าง overlay ที่เปิด ux กลับ
    created = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": draft}, None)
    assert created["op"] == "create" and created["errors"] == []
    entry = studio.commit(created, typed="flow.yaml").json()["data"]
    revert = preview(studio, "revert", ".veda/projects/ui/flow.yaml", {"change_id": entry["change_id"]}, file_sha(overlay))
    assert revert["op"] == "delete" and ux_projects(revert["warnings"]) == {"ui"} and revert["errors"] == []


def test_no_frontend_project_gets_no_ux_warning_on_revert(studio: Studio) -> None:
    root = studio.settings.veda_root
    overlay = root / ".veda/projects/cli/flow.yaml"
    draft = strip(studio.client.get("/api/flows/cli").json()["data"])
    stage(draft, "ux-design")["enabled"] = False
    first = preview(studio, "flow", ".veda/projects/cli/flow.yaml", {"flow": draft}, None)
    assert first["errors"] == [] and first["warnings"] == []
    entry = studio.commit(first, typed="flow.yaml").json()["data"]
    revert = preview(studio, "revert", ".veda/projects/cli/flow.yaml", {"change_id": entry["change_id"]}, file_sha(overlay))
    assert ux_projects(revert["warnings"]) == set()


def test_commit_rechecks_ux_ack_under_lock(studio: Studio) -> None:
    """preview ตอน ux เปิด (ไม่ต้อง ack) → default ถูกแก้มือให้ปิด ux → commit ต้องไม่ผ่านโดยไม่มีใครยืนยัน."""
    root = studio.settings.veda_root
    draft = strip(studio.client.get("/api/flows/ui").json()["data"])
    stage(draft, "devops")["enabled"] = False
    data = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": draft}, None)
    assert data["errors"] == [] and data["warnings"] == []
    (root / DEFAULT).write_bytes(_default_without_ux())
    response = studio.commit(data, typed="flow.yaml")
    # ชุดโปรเจกต์ที่ ux ปิดโตจาก {} เป็น {ui} หลัง preview → ต้องถามใหม่ (retro epic-4 A3) ไม่เขียน
    assert response.status_code == 409 and not (root / ".veda/projects/ui/flow.yaml").exists()
    assert response.json()["code"] == "ux_ack_stale" and response.json()["details"]["projects"] == ["ui"]


def test_default_draft_without_overlay_is_no_change_even_when_ux_is_off(studio: Studio) -> None:
    (studio.settings.veda_root / DEFAULT).write_bytes(_default_without_ux())
    view = studio.client.get("/api/flows/ui").json()["data"]
    data = preview(studio, "flow", ".veda/projects/ui/flow.yaml", {"flow": view["defaultFlow"]}, None)
    assert [e["code"] for e in data["errors"]] == ["no_change"]


def test_overlay_parse_cache_is_bounded_and_copies_the_overlay() -> None:
    from bmad_studio.core.flow import merge

    for i in range(merge._CACHE_MAX + 20):
        merge.parse_overlay(f"extends: default\n# {i}\n".encode())
    assert len(merge._parse_cache) <= merge._CACHE_MAX
    a = merge.parse_overlay(b"extends: default\ntracks: {full: {stages: {devops: {enabled: false}}}}\n")[0]
    a.tracks["full"].stages.clear()  # ผู้เรียกแก้ผลได้ — ต้องไม่กระทบครั้งถัดไป
    b = merge.parse_overlay(b"extends: default\ntracks: {full: {stages: {devops: {enabled: false}}}}\n")[0]
    assert "devops" in b.tracks["full"].stages


# -- ack ผูกกับชุดโปรเจกต์ที่แสดงใน preview (retro epic-4 A3) -------------------------------------------

def _registry_edit(studio: Studio, change: Any) -> None:
    import json

    path = studio.settings.veda_root / ".veda/projects/registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["projects"])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _add_project(studio: Studio, entry: dict[str, Any]) -> None:
    _registry_edit(studio, lambda projects: projects.append(entry))
    project_dir(studio.settings.veda_root, entry["name"]).mkdir(exist_ok=True)


def _set_flag(studio: Studio, name: str, **flags: Any) -> None:
    _registry_edit(studio, lambda projects: [p.update(flags) for p in projects if p["name"] == name])


NEW_UI = {"name": "late-ui", "status": "developing", "requirementsReady": True, "hasFrontend": True}
UX_OFF_OVERLAY = "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n"
UX_ON_OVERLAY = "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: true}\n"


def _stale(response: Any, projects: list[str]) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "ux_ack_stale" and body["details"]["projects"] == projects


def _ux_off_default_draft(studio: Studio) -> dict[str, Any]:
    draft = strip(studio.client.get("/api/flow-default").json()["data"])
    stage(draft, "ux-design")["enabled"] = False
    return draft


def test_flow_default_ack_does_not_cover_project_added_after_preview(studio: Studio) -> None:
    root = studio.settings.veda_root
    draft = _ux_off_default_draft(studio)
    data = preview(studio, "flow-default", DEFAULT, {"flow": draft, "acknowledged": [UX]}, None)
    assert data["errors"] == [] and ux_projects(data["warnings"]) == {"ui", "nofe"}
    _add_project(studio, NEW_UI)
    _stale(studio.commit(data, typed="flow.default.yaml"), ["late-ui"])
    assert not (root / DEFAULT).exists() and studio.changes() == []
    again = preview(studio, "flow-default", DEFAULT, {"flow": draft, "acknowledged": [UX]}, None)
    assert ux_projects(again["warnings"]) == {"ui", "nofe", "late-ui"}  # ถามใหม่พร้อมชื่อที่เพิ่ม
    assert studio.commit(again, typed="flow.default.yaml").status_code == 200


def test_flow_default_commit_allowed_when_shown_set_shrinks(studio: Studio) -> None:
    data = preview(studio, "flow-default", DEFAULT, {"flow": _ux_off_default_draft(studio), "acknowledged": [UX]}, None)
    _set_flag(studio, "nofe", hasFrontend=False)  # ชุดเล็กลง — การยืนยันเดิมยังครอบคลุม
    assert studio.commit(data, typed="flow.default.yaml").status_code == 200


def test_flow_default_unchanged_set_commits(studio: Studio) -> None:
    data = preview(studio, "flow-default", DEFAULT, {"flow": _ux_off_default_draft(studio), "acknowledged": [UX]}, None)
    _add_project(studio, {**NEW_UI, "name": "late-cli", "hasFrontend": False})  # ไม่มี frontend — ชุดไม่เปลี่ยน
    assert studio.commit(data, typed="flow.default.yaml").status_code == 200


def test_flow_overlay_frontend_flag_flip_after_preview_reasks(studio: Studio) -> None:
    root = studio.settings.veda_root
    draft = strip(studio.client.get("/api/flows/cli").json()["data"])
    stage(draft, "ux-design")["enabled"] = False
    data = preview(studio, "flow", ".veda/projects/cli/flow.yaml", {"flow": draft}, None)
    assert data["errors"] == [] and data["warnings"] == []
    _set_flag(studio, "cli", hasFrontend=True)
    _stale(studio.commit(data, typed="flow.yaml"), ["cli"])
    assert not (root / ".veda/projects/cli/flow.yaml").exists()


def test_flow_overlay_delete_frontend_flag_flip_after_preview_reasks(studio: Studio) -> None:
    root = studio.settings.veda_root
    (root / DEFAULT).write_bytes(_default_without_ux())
    overlay = root / ".veda/projects/cli/flow.yaml"
    overlay.write_text(UX_ON_OVERLAY, encoding="utf-8")
    view = studio.client.get("/api/flows/cli").json()["data"]
    data = preview(studio, "flow", ".veda/projects/cli/flow.yaml", {"flow": view["defaultFlow"]}, file_sha(overlay))
    assert data["op"] == "delete" and data["errors"] == [] and data["warnings"] == []
    _set_flag(studio, "cli", hasFrontend=True)
    _stale(studio.commit(data, typed="flow.yaml"), ["cli"])
    assert overlay.exists()


def test_revert_of_default_does_not_cover_project_added_after_preview(studio: Studio) -> None:
    target = studio.settings.veda_root / DEFAULT
    off = _default_without_ux()
    target.write_bytes(off)
    draft = strip(studio.client.get("/api/flow-default").json()["data"])
    stage(draft, "ux-design")["enabled"] = True
    stage(draft, "devops")["optional"] = False
    entry = studio.commit(preview(studio, "flow-default", DEFAULT, {"flow": draft}, sha(off)),
                          typed="flow.default.yaml").json()["data"]
    before = file_sha(target)
    revert = preview(studio, "revert", DEFAULT, {"change_id": entry["change_id"]}, before)
    assert ux_projects(revert["warnings"]) == {"ui", "nofe"}
    _add_project(studio, NEW_UI)
    _stale(studio.commit(revert, typed="flow.default.yaml"), ["late-ui"])
    assert file_sha(target) == before


def test_reset_default_delete_does_not_cover_project_added_after_preview(studio: Studio) -> None:
    """รีเซ็ต default = ลบไฟล์ (กลับไปใช้ packaged); default ปัจจุบันมีขั้นออกแบบสำรอง (ui-design) — โปรเจกต์ที่เพิ่ม
    หลัง preview และปิด ux-design ใน overlay จะเสีย ux หลังลบ."""
    root = studio.settings.veda_root
    target = root / DEFAULT
    data = default_data()
    data["tracks"]["full"]["stages"].append({"id": "ui-design", "agent": "ux-designer", "enabled": True,
                                             "optional": False, "after": ["requirements-review"], "gate": "none",
                                             "produces": ["designReady"]})
    stage(data, "architecture")["after"] = ["ux-design", "ui-design"]
    raw = dump(data).encode()
    target.write_bytes(raw)
    reset_draft = studio.client.get("/api/flow-default").json()["data"]["defaultFlow"]
    reset = preview(studio, "flow-default", DEFAULT, {"flow": reset_draft, "acknowledged": [UX]}, sha(raw))
    assert reset["op"] == "delete" and "ui-off" in ux_projects(reset["warnings"])
    _add_project(studio, {**NEW_UI, "name": "late-off"})
    (project_dir(root, "late-off") / "flow.yaml").write_text(UX_OFF_OVERLAY, encoding="utf-8")
    _stale(studio.commit(reset, typed="flow.default.yaml"), ["late-off"])
    assert target.read_bytes() == raw


def test_revert_of_overlay_frontend_flag_flip_after_preview_reasks(studio: Studio) -> None:
    root = studio.settings.veda_root
    overlay = root / ".veda/projects/cli/flow.yaml"
    off = strip(studio.client.get("/api/flows/cli").json()["data"])
    stage(off, "ux-design")["enabled"] = False
    assert studio.commit(preview(studio, "flow", ".veda/projects/cli/flow.yaml", {"flow": off}, None),
                         typed="flow.yaml").status_code == 200
    on = strip(studio.client.get("/api/flows/cli").json()["data"])
    stage(on, "ux-design")["enabled"] = True
    second = studio.commit(preview(studio, "flow", ".veda/projects/cli/flow.yaml", {"flow": on}, file_sha(overlay)),
                           typed="flow.yaml").json()["data"]
    revert = preview(studio, "revert", ".veda/projects/cli/flow.yaml", {"change_id": second["change_id"]}, None)
    assert revert["op"] == "create" and revert["warnings"] == []
    _set_flag(studio, "cli", hasFrontend=True)
    _stale(studio.commit(revert, typed="flow.yaml"), ["cli"])
    assert not overlay.exists()


def test_ack_scope_is_signed(studio: Studio) -> None:
    """แก้ ack_scope ในไฟล์ preview บนดิสก์ให้ครอบคลุมโปรเจกต์ใหม่ไม่ได้ — token ใช้ไม่ได้."""
    import json

    data = preview(studio, "flow-default", DEFAULT, {"flow": _ux_off_default_draft(studio), "acknowledged": [UX]}, None)
    meta_path = studio.settings.runtime_dir / "previews" / f"{data['preview_id']}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["ack_scope"] == ["nofe", "ui"]
    meta["ack_scope"].append("late-ui")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    _add_project(studio, NEW_UI)
    response = studio.commit(data, typed="flow.default.yaml")
    assert response.status_code == 403 and response.json()["code"] == "confirm_invalid"


def test_ack_scope_unit() -> None:
    from bmad_studio.core.flow.ux_guard import SCOPE_UNCHECKED, ack_scope, scope_grew

    shown = ack_scope([{"code": UX, "project": "b", "error": ""}, {"code": UX, "project": "a", "error": ""},
                       {"code": "other", "project": "z", "error": ""}])
    assert shown == ["a", "b"]
    assert ack_scope([{"code": "ux_check_failed", "error": ""}]) == [SCOPE_UNCHECKED]
    assert scope_grew(shown, ["a"]) == [] and scope_grew(shown, ["a", "c"]) == ["c"]
    assert scope_grew([], [SCOPE_UNCHECKED]) == [SCOPE_UNCHECKED]
