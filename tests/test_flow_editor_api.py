"""Story 4.1 — flow editing API: lock metadata, live validation, kind ``flow`` (REQ-046, REQ-047, AD-1/5/7/8).

ทุก test ใช้ tmp veda (conftest) — ไม่แตะ veda จริง.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.core.flow import policy
from bmad_studio.core.flow.editor import META_KEYS
from bmad_studio.core.settings import Settings

from .flowkit import seed_catalog
from .test_changes_api import Studio
from .test_flow_resolve import project_dir, registry


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def studio(settings: Settings) -> Studio:
    root = settings.veda_root
    seed_catalog(root)
    (root / ".claude" / "agents" / "ba.md").write_text(
        "---\nname: ba\ndescription: เก็บ requirement\n---\nbody\n", encoding="utf-8")
    registry(root,
             {"name": "ui-app", "status": "developing", "requirementsReady": True, "hasFrontend": True},
             {"name": "cli-app", "status": "developing", "requirementsReady": True, "hasFrontend": False},
             {"name": "unknown-fe", "status": "registered"},
             {"name": "broken", "requirementsReady": True, "hasFrontend": False},
             {"name": "garbled", "hasFrontend": False})
    (project_dir(root, "broken") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: false}\n"
        "      devops: {enabled: false}\n", encoding="utf-8")
    (project_dir(root, "garbled") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      nope: {enabled: false}\n", encoding="utf-8")
    return Studio(settings)


def view(studio: Studio, project: str) -> dict[str, Any]:
    response = studio.client.get(f"/api/flows/{project}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def draft_of(data: dict[str, Any]) -> dict[str, Any]:
    """draft ที่ UI ส่ง = tracks ของ view โดยตัด metadata ของ editor ออก."""
    tracks = {}
    for name, track in data["tracks"].items():
        stages = [{k: v for k, v in s.items() if k not in META_KEYS} for s in track["stages"]]
        tracks[name] = {"entry_condition": track["entry_condition"], "stages": stages}
    return {"version": 1, "tracks": tracks}


def stage(draft: dict[str, Any], sid: str, track: str = "full") -> dict[str, Any]:
    return next(s for s in draft["tracks"][track]["stages"] if s["id"] == sid)


def validate(studio: Studio, project: str, draft: dict[str, Any]) -> dict[str, Any]:
    response = studio.post(f"/api/flows/{project}/validate", {"flow": draft})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def codes(items: list[dict[str, Any]]) -> set[str]:
    return {i["code"] for i in items}


def preview(studio: Studio, project: str, draft: dict[str, Any], base: str | None, ack: list[str] | None = None,
            target: str | None = None):
    payload: dict[str, Any] = {"flow": draft}
    if ack is not None:
        payload["acknowledged"] = ack
    return studio.post("/api/changes/preview", {"kind": "flow", "target": target or f".veda/projects/{project}/flow.yaml",
                                                "payload": payload, "base_sha256": base})


def overlay_file(studio: Studio, project: str) -> Path:
    return project_dir(studio.settings.veda_root, project) / "flow.yaml"


# -- GET --------------------------------------------------------------------------------------------

def test_get_without_overlay_has_lock_metadata(studio: Studio) -> None:
    data = view(studio, "ui-app")
    assert data["base_sha256"] is None and data["exists"] is False and data["overrides"] == 0
    assert data["hasFrontend"] is True and data["issues"] == [] and data["policyFix"] is None
    rows = {s["id"]: s for s in data["tracks"]["full"]["stages"]}
    gate = rows["requirements-review"]
    assert gate["locked"] is True and gate["lockReason"] == policy.LOCK_REASON_GATE and gate["mustFollowLocked"] is False
    for sid in ("ux-design", "architecture", "develop", "review", "devops"):
        assert rows[sid]["mustFollowLocked"] is True and rows[sid]["locked"] is False, sid
    for sid in ("bootstrap", "requirements"):
        assert rows[sid]["mustFollowLocked"] is False, sid
    assert rows["devops"]["default"]["optional"] is True and rows["devops"]["overridden"] is False
    assert [s["id"] for s in data["tracks"]["full"]["stages"]][:3] == ["bootstrap", "requirements", "requirements-review"]
    quick = data["tracks"]["quick"]
    assert quick["lockedFields"] == [{"field": "entry_condition", "reason": policy.LOCK_REASON_ENTRY}]
    assert all(s["mustFollowLocked"] is False for s in quick["stages"])


def test_get_with_overlay_reports_base_sha_and_overrides(studio: Studio) -> None:
    raw = b"extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n"
    overlay_file(studio, "cli-app").write_bytes(raw)
    data = view(studio, "cli-app")
    assert data["base_sha256"] == sha(raw) and data["overrides"] == 1
    devops = next(s for s in data["tracks"]["full"]["stages"] if s["id"] == "devops")
    assert devops["overridden"] is True and devops["overriddenFields"] == ["enabled"] and devops["default"]["enabled"] is True


def test_overlay_that_changes_nothing_is_not_overridden(studio: Studio) -> None:
    overlay_file(studio, "cli-app").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: true}\n", encoding="utf-8")
    data = view(studio, "cli-app")
    assert data["overrides"] == 0
    assert not any(s["overridden"] for s in data["tracks"]["full"]["stages"])


def test_get_hand_edited_bypass_has_issues_and_policy_fix(studio: Studio) -> None:
    data = view(studio, "broken")
    assert data["loadError"] is False
    assert any(i["code"] == "locked_gate_violation" and i.get("stage") == "requirements-review" for i in data["issues"])
    fix = data["policyFix"]
    assert fix is not None
    assert validate(studio, "broken", fix)["errors"] == []
    assert stage(fix, "requirements-review")["enabled"] is True
    assert stage(fix, "devops")["enabled"] is False  # การปรับเองที่ไม่ผิด lock คงไว้


def _entry_widened(path: Path) -> None:
    path.write_bytes(b"extends: default\ntracks:\n  quick:\n    entry_condition: {requirementsReady: false, status: [registered]}\n"
                     b"  full:\n    stages:\n      devops: {enabled: false}\n")


def _added_pre_gate(path: Path) -> None:
    path.write_bytes(b"extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n    add:\n"
                     b"      - {id: early, agent: skill-scout, after: [requirements]}\n")


@pytest.mark.parametrize("write", [_entry_widened, _added_pre_gate])
def test_policy_fix_repairs_only_the_locked_part(studio: Studio, write) -> None:
    write(overlay_file(studio, "cli-app"))
    data = view(studio, "cli-app")
    assert "locked_gate_violation" in codes(data["issues"]) and data["loadError"] is False
    fix = data["policyFix"]
    assert fix is not None and fix != data["defaultFlow"]
    assert validate(studio, "cli-app", fix)["errors"] == []
    assert stage(fix, "devops")["enabled"] is False  # การปรับเองที่ไม่ผิด lock คงไว้


def test_overlay_too_large_for_loader_can_still_be_replaced(studio: Studio) -> None:
    # > 256 KiB ของ loader แต่บรรทัดน้อยพอให้ diff แสดงได้ (ไฟล์หลายหมื่นบรรทัด = diff_too_large ดู test_huge_*)
    raw = ("extends: default\n" + ("# " + "p" * 2000 + "\n") * 140).encode("utf-8")
    overlay_file(studio, "cli-app").write_bytes(raw)
    data = view(studio, "cli-app")
    assert data["loadError"] is True and data["editable"] is True and data["base_sha256"] == sha(raw)
    draft = data["policyFix"]
    stage(draft, "devops")["enabled"] = False
    result = preview(studio, "cli-app", draft, data["base_sha256"]).json()["data"]
    assert result["errors"] == [] and "format_rewritten" in codes(result["warnings"])
    assert studio.commit(result, typed="flow.yaml").status_code == 200


def test_non_text_overlay_is_not_editable(studio: Studio) -> None:
    overlay_file(studio, "cli-app").write_bytes(b"\xff\xfe\x00\xd8bad")
    data = view(studio, "cli-app")
    assert data["editable"] is False and data["notEditableReason"] and data["policyFix"] is None


def test_get_unloadable_overlay_falls_back_to_default(studio: Studio) -> None:
    data = view(studio, "garbled")
    assert data["loadError"] is True and "unknown_stage" in codes(data["issues"])
    assert data["policyFix"] == data["defaultFlow"] and data["exists"] is True
    assert data["base_sha256"] == sha(overlay_file(studio, "garbled").read_bytes())


@pytest.mark.parametrize("text", ["extends: default\ntracks: [\n", "extends: other\n", "- a\n", "extends: default\n&a x: 1\n"])
def test_get_bad_yaml_or_schema_is_load_error(studio: Studio, text: str) -> None:
    overlay_file(studio, "cli-app").write_text(text, encoding="utf-8")
    data = view(studio, "cli-app")
    assert data["loadError"] is True and data["issues"] and data["policyFix"] == data["defaultFlow"]


@pytest.mark.parametrize("name", ["nope", "UI-APP", "ui-app.", "default", "con"])
def test_unknown_project_is_404_on_get_and_validate(studio: Studio, name: str) -> None:
    response = studio.client.get(f"/api/flows/{name}")
    assert response.status_code == 404 and response.json()["ok"] is False
    response = studio.post(f"/api/flows/{name}/validate", {"flow": draft_of(view(studio, "ui-app"))})
    assert response.status_code == 404 and response.json()["ok"] is False


def test_dotdot_project_never_reaches_the_editor(studio: Studio) -> None:
    assert studio.client.get("/api/flows/..").status_code == 403  # client normalize เป็น /api/ = ไม่มี rule


def test_flow_routes_need_login_and_csrf(studio: Studio, settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    anon = TestClient(create_app(settings))
    for path in ("/api/flows/ui-app", "/api/agents/names", "/api/skills"):
        assert anon.get(path).status_code == 401
    assert anon.post("/api/flows/ui-app/validate", json={"flow": {}}).status_code == 401
    no_csrf = studio.client.post("/api/flows/ui-app/validate", json={"flow": {}})
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "csrf_invalid"


# -- validate ----------------------------------------------------------------------------------------

def test_validate_default_draft_is_clean(studio: Studio) -> None:
    result = validate(studio, "ui-app", draft_of(view(studio, "ui-app")))
    assert result["errors"] == [] and result["warnings"] == [] and result["resetsToDefault"] is True
    assert result["overlay"] == {"extends": "default"} and result["overrides"] == 0


def _ux_off(draft: dict[str, Any], how: str) -> dict[str, Any]:
    ux = stage(draft, "ux-design")
    if how == "disable":
        ux["enabled"] = False
    elif how == "agent":
        ux["agent"] = "ba"
        ux["produces"] = []
    elif how == "produces":
        ux["produces"] = []
    elif how == "condition":
        ux["condition"] = "planReady"
    elif how == "after-develop":
        # ux ไปอยู่หลัง develop: architecture ต่อจาก gate ตรง ๆ
        stage(draft, "architecture")["after"] = ["requirements-review"]
        ux["after"] = ["develop"]
        stage(draft, "review")["after"] = ["ux-design"]
    return draft


@pytest.mark.parametrize("how", ["disable", "agent", "produces", "condition", "after-develop"])
def test_disabling_ux_on_frontend_project_needs_ack(studio: Studio, how: str) -> None:
    draft = _ux_off(draft_of(view(studio, "ui-app")), how)
    result = validate(studio, "ui-app", draft)
    assert result["ackRequired"] == ["ux_disabled_frontend"], (how, result)
    assert "ux_disabled_frontend" in codes(result["warnings"])


def test_disabling_ux_without_frontend_is_fine(studio: Studio) -> None:
    draft = _ux_off(draft_of(view(studio, "cli-app")), "disable")
    result = validate(studio, "cli-app", draft)
    assert result["errors"] == [] and result["warnings"] == [] and result["ackRequired"] == []


def test_unknown_has_frontend_is_treated_as_frontend(studio: Studio) -> None:
    draft = _ux_off(draft_of(view(studio, "unknown-fe")), "disable")
    assert validate(studio, "unknown-fe", draft)["ackRequired"] == ["ux_disabled_frontend"]


def _cycle(d):
    stage(d, "bootstrap")["after"] = ["devops"]


def _unknown_agent(d):
    stage(d, "devops")["agent"] = "ghost"


def _dev_before_plan(d):
    stage(d, "develop")["after"] = ["ux-design"]


def _ux_before_gate(d):
    stage(d, "ux-design")["after"] = ["requirements"]


def _gate_disabled(d):
    stage(d, "requirements-review")["enabled"] = False


def _removed_stage(d):
    d["tracks"]["full"]["stages"] = [s for s in d["tracks"]["full"]["stages"] if s["id"] != "devops"]


def _new_track(d):
    d["tracks"]["hotfix"] = copy.deepcopy(d["tracks"]["quick"])


def _entry_changed(d):
    d["tracks"]["quick"]["entry_condition"]["status"] = ["registered"]


def _new_agent_before_gate(d):
    d["tracks"]["full"]["stages"].append({"id": "early", "agent": "skill-scout", "skill": None, "enabled": True,
                                          "optional": False, "after": ["requirements"], "gate": "none",
                                          "produces": [], "condition": None})


@pytest.mark.parametrize("mutate,code", [
    (_cycle, "cycle"), (_unknown_agent, "unknown_agent"), (_dev_before_plan, "plan_gate_missing"),
    (_ux_before_gate, "locked_gate_violation"), (_gate_disabled, "locked_gate_violation"),
    (_removed_stage, "cannot_remove_stage"), (_new_track, "unknown_track"),
    (_entry_changed, "locked_gate_violation"), (_new_agent_before_gate, "locked_gate_violation"),
])
def test_validate_reports_rule_errors(studio: Studio, mutate, code: str) -> None:
    draft = draft_of(view(studio, "cli-app"))
    mutate(draft)
    result = validate(studio, "cli-app", draft)
    assert code in codes(result["errors"]), result["errors"]


@pytest.mark.parametrize("bad", [{}, {"version": 2, "tracks": {}}, {"version": 1, "tracks": {"full": {"stages": []}}},
                                 {"version": 1, "tracks": {"full": {"stages": [{"id": "X", "agent": "ba"}]}}}])
def test_validate_schema_errors(studio: Studio, bad: dict) -> None:
    result = validate(studio, "cli-app", bad)
    assert "schema_invalid" in codes(result["errors"]) and result["overlay"] is None and result["tracks"] is None


def test_validate_returns_lock_metadata_for_draft(studio: Studio) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "requirements")["skill"] = "bmad-build"  # ba ที่ใช้ skill สร้าง = ต้องอยู่หลัง gate
    result = validate(studio, "cli-app", draft)
    rows = {s["id"]: s for s in result["tracks"]["full"]["stages"]}
    assert rows["requirements"]["mustFollowLocked"] is True and rows["requirements"]["overridden"] is True
    assert "locked_gate_violation" in codes(result["errors"])


# -- preview / commit ---------------------------------------------------------------------------------

def test_preview_writes_only_the_overlay_diff_and_commits(studio: Studio) -> None:
    root = studio.settings.veda_root
    registry_file = root / ".veda" / "projects" / "registry.json"
    before = (registry_file.read_bytes(), registry_file.stat().st_mtime_ns)
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    response = preview(studio, "cli-app", draft, None)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["impact"] == "high" and data["op"] == "create" and data["errors"] == []
    commit = studio.commit(data, typed="flow.yaml")
    assert commit.status_code == 200, commit.text
    text = overlay_file(studio, "cli-app").read_text(encoding="utf-8")
    assert "devops:" in text and "enabled: false" in text and "bootstrap" not in text
    assert not text.startswith("﻿") and "\r" not in text
    after = view(studio, "cli-app")
    assert after["overrides"] == 1 and after["base_sha256"] == sha(overlay_file(studio, "cli-app").read_bytes())
    resolved = studio.client.get("/api/projects/cli-app/flow-status").json()["data"]
    assert next(s for s in resolved["stages"] if s["id"] == "devops")["status"] == "skipped"
    assert (registry_file.read_bytes(), registry_file.stat().st_mtime_ns) == before  # REQ-047 a


def test_edit_keeps_comments_of_existing_overlay(studio: Studio) -> None:
    raw = ("# ปรับเองสำหรับ CLI\nextends: default\ntracks:\n  full:\n    stages:\n"
           "      devops: {enabled: false}  # ไม่ deploy\n").encode("utf-8")
    overlay_file(studio, "cli-app").write_bytes(raw)
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "retrospective")["enabled"] = False
    data = preview(studio, "cli-app", draft, sha(raw)).json()["data"]
    assert data["errors"] == [] and data["diff"]["removed"] == 0
    studio.commit(data, typed="flow.yaml")
    text = overlay_file(studio, "cli-app").read_text(encoding="utf-8")
    assert text.startswith("# ปรับเองสำหรับ CLI\n") and "# ไม่ deploy" in text and "retrospective" in text


def test_draft_equal_to_default_deletes_overlay(studio: Studio) -> None:
    raw = b"extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n"
    overlay_file(studio, "cli-app").write_bytes(raw)
    data = view(studio, "cli-app")
    draft = draft_of(data)
    stage(draft, "devops")["enabled"] = True
    response = preview(studio, "cli-app", draft, sha(raw)).json()["data"]
    assert response["op"] == "delete" and response["errors"] == []
    assert studio.commit(response, typed="flow.yaml").status_code == 200
    assert not overlay_file(studio, "cli-app").exists()
    assert view(studio, "cli-app")["base_sha256"] is None


def test_default_draft_without_overlay_is_no_change(studio: Studio) -> None:
    response = preview(studio, "cli-app", draft_of(view(studio, "cli-app")), None).json()["data"]
    assert "no_change" in codes(response["errors"])


def test_locked_violation_preview_has_errors_and_cannot_commit(studio: Studio) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "requirements-review")["enabled"] = False
    data = preview(studio, "cli-app", draft, None).json()["data"]
    assert "locked_gate_violation" in codes(data["errors"])
    assert len(data["errors"]) == len({json.dumps(e, sort_keys=True) for e in data["errors"]})  # ไม่มีรายการซ้ำ
    response = studio.commit(data, typed="flow.yaml")
    assert response.status_code == 422 and not overlay_file(studio, "cli-app").exists()


def test_ux_disable_needs_server_side_ack(studio: Studio) -> None:
    draft = _ux_off(draft_of(view(studio, "ui-app")), "disable")
    data = preview(studio, "ui-app", draft, None).json()["data"]
    assert "ux_disable_unconfirmed" in codes(data["errors"]) and "ux_disabled_frontend" in codes(data["warnings"])
    assert studio.commit(data, typed="flow.yaml").status_code == 422
    data = preview(studio, "ui-app", draft, None, ack=["ux_disabled_frontend"]).json()["data"]
    assert data["errors"] == [] and "ux_disabled_frontend" in codes(data["warnings"])
    assert studio.commit(data, typed="flow.yaml").status_code == 200


@pytest.mark.parametrize("ack,status", [(None, 200), ([], 200), (["made-up"], 422), (["ux_disabled_frontend"], 200)])
def test_acknowledged_values(studio: Studio, ack, status: int) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    response = studio.post("/api/changes/preview", {"kind": "flow", "target": ".veda/projects/cli-app/flow.yaml",
                                                    "payload": {"flow": draft, "acknowledged": ack}, "base_sha256": None})
    assert response.status_code == status, response.text


def test_removing_override_keeps_other_comments(studio: Studio) -> None:
    raw = ("# หัวไฟล์\nextends: default\ntracks:\n  full:\n    stages:\n      retrospective: {enabled: false}  # ไม่ทำ retro\n"
           "      devops: {enabled: false}\n      # หมายเหตุของ review\n      review: {gate: none}\n").encode("utf-8")
    overlay_file(studio, "cli-app").write_bytes(raw)
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "retrospective")["enabled"] = True  # เอา override แรกออก
    data = preview(studio, "cli-app", draft, sha(raw)).json()["data"]
    assert data["errors"] == [] and data["diff"]["added"] == 0 and data["diff"]["removed"] == 1
    studio.commit(data, typed="flow.yaml")
    text = overlay_file(studio, "cli-app").read_text(encoding="utf-8")
    assert text.startswith("# หัวไฟล์\n") and "retrospective" not in text and "devops" in text and "review" in text
    assert "# หมายเหตุของ review" in text


def test_deleting_key_that_owns_a_following_comment_warns(studio: Studio) -> None:
    # comment ท้ายบรรทัดของ devops + comment บรรทัดเต็มถัดไป = ruamel เก็บเป็น token เดียวของ devops
    raw = ("extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}  # ไม่ deploy\n"
           "      # ปิด retro ไว้\n      retrospective: {enabled: false}\n").encode("utf-8")
    overlay_file(studio, "cli-app").write_bytes(raw)
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = True
    data = preview(studio, "cli-app", draft, sha(raw)).json()["data"]
    kept = [line["text"] for hunk in data["diff"]["hunks"] for line in hunk["lines"] if line["type"] != "del"]
    lost = not any("# ปิด retro ไว้" in t for t in kept)
    assert lost  # พฤติกรรมของ ruamel ที่รู้แล้ว — สิ่งที่ต้องรับรองคือไม่หายเงียบ
    assert "comments_dropped" in codes(data["warnings"])


def test_ux_disable_on_non_frontend_needs_no_ack(studio: Studio) -> None:
    draft = _ux_off(draft_of(view(studio, "cli-app")), "disable")
    data = preview(studio, "cli-app", draft, None).json()["data"]
    assert data["errors"] == [] and data["warnings"] == []


@pytest.mark.parametrize("target,status", [
    (".veda/projects/nope/flow.yaml", 404),
    (".veda/projects/CLI-APP/flow.yaml", 404),
    (".veda/flow.default.yaml", 403),
    (".veda/projects/registry.json", 403),
    (".claude/agents/developer.md", 403),
])
def test_flow_kind_target_rules(studio: Studio, target: str, status: int) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    response = preview(studio, "cli-app", draft, None, target=target)
    assert response.status_code == status, response.text


@pytest.mark.parametrize("payload", [{}, {"flow": "x"}, {"flow": {}, "acknowledged": "ux"},
                                     {"flow": {}, "acknowledged": ["x"] * 20}])
def test_flow_kind_rejects_bad_payload(studio: Studio, payload: dict) -> None:
    response = studio.post("/api/changes/preview", {"kind": "flow", "target": ".veda/projects/cli-app/flow.yaml",
                                                    "payload": payload, "base_sha256": None})
    assert response.status_code == 422


def test_flow_kind_does_not_accept_an_overlay(studio: Studio) -> None:
    response = studio.post("/api/changes/preview", {
        "kind": "flow", "target": ".veda/projects/cli-app/flow.yaml", "base_sha256": None,
        "payload": {"flow": {"extends": "default", "tracks": {"full": {"stages": {"devops": {"enabled": False}}}}}}})
    assert response.status_code == 422 and response.json()["code"] == "flow_invalid"


def test_stale_base_is_conflict(studio: Studio) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    overlay_file(studio, "cli-app").write_text("extends: default\n", encoding="utf-8")
    assert preview(studio, "cli-app", draft, None).status_code == 409


def test_commit_revalidates_against_changed_default(studio: Studio) -> None:
    """default เปลี่ยนระหว่าง preview กับ commit (แก้มือ) → ตรวจซ้ำใต้ lock แล้วไม่เขียน."""
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    data = preview(studio, "cli-app", draft, None).json()["data"]
    root = studio.settings.veda_root
    (root / ".claude" / "agents" / "devops.md").unlink()  # devops หายจาก catalog → overlay ไม่ผ่านแล้ว
    response = studio.commit(data, typed="flow.yaml")
    assert response.status_code == 422 and not overlay_file(studio, "cli-app").exists()


# -- revert ใช้ validator ชุดเดียวกัน ------------------------------------------------------------------

def test_revert_to_hand_edited_bypass_is_blocked(studio: Studio) -> None:
    bypass = (b"extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: false}\n")
    overlay_file(studio, "cli-app").write_bytes(bypass)  # แก้มือนอกแอป
    data = view(studio, "cli-app")
    assert data["policyFix"] is not None
    fixed = preview(studio, "cli-app", data["policyFix"], sha(bypass)).json()["data"]
    assert fixed["errors"] == [], fixed["errors"]
    assert fixed["op"] == "delete"  # คืน gate = เท่ากับ default ทั้งก้อน → ลบ overlay
    committed = studio.commit(fixed, typed="flow.yaml").json()["data"]
    path = overlay_file(studio, "cli-app")
    current = path.read_bytes() if path.exists() else None
    response = studio.post("/api/changes/preview", {"kind": "revert", "target": ".veda/projects/cli-app/flow.yaml",
                                                    "payload": {"change_id": committed["change_id"]},
                                                    "base_sha256": sha(current) if current is not None else None})
    revert = response.json()["data"]
    assert any(e["code"] == "locked_gate_violation" and e.get("stage") == "requirements-review" for e in revert["errors"])
    assert studio.commit(revert, typed="flow.yaml").status_code == 422
    assert (path.read_bytes() if path.exists() else None) == current


def test_revert_of_valid_flow_edit_works(studio: Studio) -> None:
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    data = preview(studio, "cli-app", draft, None).json()["data"]
    committed = studio.commit(data, typed="flow.yaml").json()["data"]
    current = overlay_file(studio, "cli-app").read_bytes()
    revert = studio.post("/api/changes/preview", {"kind": "revert", "target": ".veda/projects/cli-app/flow.yaml",
                                                  "payload": {"change_id": committed["change_id"]},
                                                  "base_sha256": sha(current)}).json()["data"]
    assert revert["errors"] == [] and revert["op"] == "delete"
    assert studio.commit(revert, typed="flow.yaml").status_code == 200 and not overlay_file(studio, "cli-app").exists()


# -- agents / skills --------------------------------------------------------------------------------

def test_agent_names_and_skills(studio: Studio) -> None:
    root = studio.settings.veda_root
    (root / ".claude" / "agents" / "Weird Name.md").write_text("x", encoding="utf-8")
    agents = studio.client.get("/api/agents/names").json()["data"]
    names = [a["name"] for a in agents]
    assert "ba" in names and "developer" in names and "Weird Name" not in names and names == sorted(names)
    assert next(a for a in agents if a["name"] == "ba")["description"] == "เก็บ requirement"
    skills = studio.client.get("/api/skills").json()["data"]
    assert {s["id"] for s in skills} >= {"bmad-build", "bmad-architecture"}


def test_skill_description_is_bounded(studio: Studio) -> None:
    folder = studio.settings.veda_root / ".claude" / "skills" / "bmad-long"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\ndescription: " + "ก" * 5000 + "\n---\n", encoding="utf-8")
    big = studio.settings.veda_root / ".claude" / "skills" / "bmad-huge"
    big.mkdir()
    (big / "SKILL.md").write_bytes(b"---\ndescription: x\n---\n" + b"y" * (300 * 1024))
    skills = {s["id"]: s for s in studio.client.get("/api/skills").json()["data"]}
    assert len(skills["bmad-long"]["description"]) <= 400 and skills["bmad-huge"]["description"] == ""


# -- DoS (epic 2 retro item 5): วัดเวลา ---------------------------------------------------------------

def _max_draft(base: dict[str, Any]) -> dict[str, Any]:
    """draft ใหญ่สุดที่ schema รับ: full 64 ขั้น (ต่อจาก devops) แต่ละขั้น after ทุกขั้นก่อนหน้า."""
    draft = copy.deepcopy(base)
    stages = draft["tracks"]["full"]["stages"]
    prev = "devops"
    ids = [s["id"] for s in stages]
    while len(stages) < 64:
        sid = f"extra-{len(stages):02d}-" + "x" * 40
        stages.append({"id": sid, "agent": "devops", "skill": None, "enabled": True, "optional": True,
                       # after ยาวสุดที่ schema รับ (64) — ทุกขั้นก่อนหน้า = กราฟหนาแน่นสุดสำหรับ ancestors/topo
                       "after": [prev] + [i for i in ids if i != prev][-63:], "gate": "none", "produces": [],
                       "condition": None})
        ids.append(sid)
        prev = sid
    return draft


def test_validate_and_preview_of_largest_draft_are_fast(studio: Studio) -> None:
    draft = _max_draft(draft_of(view(studio, "cli-app")))
    start = time.perf_counter()
    result = validate(studio, "cli-app", draft)
    validate_s = time.perf_counter() - start
    assert result["errors"] == [], result["errors"][:3]
    start = time.perf_counter()
    data = preview(studio, "cli-app", draft, None).json()["data"]
    preview_s = time.perf_counter() - start
    print(f"validate={validate_s:.2f}s preview={preview_s:.2f}s")
    assert data["errors"] == [] and validate_s < 5 and preview_s < 5, (validate_s, preview_s)


def test_huge_hand_edited_overlay_diff_is_capped(studio: Studio) -> None:
    """overlay เดิมแก้มือ 1.9 MiB หลายหมื่นบรรทัด → diff เกิน cap = diff_too_large (บล็อก) และตอบเร็ว."""
    lines = "".join(f"# comment line {i}\n" for i in range(90_000))
    raw = ("extends: default\n" + lines).encode("utf-8")
    assert len(raw) < 2 * 1024 * 1024
    overlay_file(studio, "cli-app").write_bytes(raw)
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    start = time.perf_counter()
    response = preview(studio, "cli-app", draft, sha(raw))
    elapsed = time.perf_counter() - start
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["diff"]["truncated"] is True and "diff_too_large" in codes(data["errors"])
    assert studio.commit(data, typed="flow.yaml").status_code == 422
    assert elapsed < 10, elapsed


def test_preserving_rewrite_at_size_limit_is_bounded(studio: Studio) -> None:
    """ไฟล์ใหญ่สุดที่ยังรักษารูปแบบ (64 KiB ของ comment) — round-trip หลายชุดตั้งค่ายังต้องเร็ว."""
    from bmad_studio.core.flow.editor import PRESERVE_MAX_BYTES

    body = "extends: default\n"
    i = 0
    while len(body.encode()) < PRESERVE_MAX_BYTES - 40:
        body += f"# comment line {i}\n"
        i += 1
    overlay_file(studio, "cli-app").write_text(body, encoding="utf-8")
    raw = overlay_file(studio, "cli-app").read_bytes()
    draft = draft_of(view(studio, "cli-app"))
    stage(draft, "devops")["enabled"] = False
    start = time.perf_counter()
    response = preview(studio, "cli-app", draft, sha(raw))
    elapsed = time.perf_counter() - start
    assert response.status_code == 200, response.text
    assert elapsed < 5, elapsed
