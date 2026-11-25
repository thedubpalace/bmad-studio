"""Story 4.3 — แก้ flow เริ่มต้นพร้อมตรวจข้ามโปรเจกต์ (REQ-045, REQ-047 b, AD-5/7/8, retro epic-3 A2).

ทุกการเปลี่ยนที่ทำให้ default เปลี่ยน (แก้/สร้าง/revert/ลบ) ตรวจ overlay ของทุกโปรเจกต์ ทั้งตอน preview และ commit.
"""
from __future__ import annotations

import copy
import hashlib
import time
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.core.flow.validate import DEFAULT_TEMPLATE
from bmad_studio.core.settings import Settings

from .flowkit import default_data, dump, seed_catalog
from .test_changes_api import Studio
from .test_flow_resolve import project_dir, registry

TARGET = ".veda/flow.default.yaml"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def studio(settings: Settings) -> Studio:
    root = settings.veda_root
    seed_catalog(root)
    registry(root,
             {"name": "plain", "status": "developing", "requirementsReady": True, "hasFrontend": True},
             {"name": "custom", "status": "developing", "requirementsReady": True, "hasFrontend": False},
             {"name": "uses-qa", "status": "developing", "requirementsReady": True, "hasFrontend": False})
    (project_dir(root, "custom") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n", encoding="utf-8")
    return Studio(settings)


def default_file(studio: Studio) -> Path:
    return studio.settings.veda_root / TARGET


def with_qa() -> dict[str, Any]:
    """default ที่มีขั้น qa เพิ่ม (หลัง develop) — มีเฉพาะใน default ปัจจุบัน ไม่มีใน packaged."""
    data = default_data()
    stages = data["tracks"]["full"]["stages"]
    stages.append({"id": "qa", "agent": "reviewer", "enabled": True, "optional": False, "after": ["develop"],
                   "gate": "none", "produces": []})
    next(s for s in stages if s["id"] == "review")["after"] = ["qa"]
    return data


def install_qa_default(studio: Studio) -> bytes:
    """default ปัจจุบันมี qa + โปรเจกต์ uses-qa อ้าง qa ใน overlay."""
    raw = dump(with_qa()).encode("utf-8")
    default_file(studio).write_bytes(raw)
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      qa: {enabled: false}\n", encoding="utf-8")
    return raw


def view(studio: Studio) -> dict[str, Any]:
    response = studio.client.get("/api/flow-default")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def draft_of(data: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "agent", "skill", "enabled", "optional", "after", "gate", "produces", "condition")
    return {"version": 1, "tracks": {n: {"entry_condition": t["entry_condition"],
                                         "stages": [{k: s[k] for k in keep} for s in t["stages"]]}
                                     for n, t in data["tracks"].items()}}


def stage(draft: dict[str, Any], sid: str, track: str = "full") -> dict[str, Any]:
    return next(s for s in draft["tracks"][track]["stages"] if s["id"] == sid)


def preview(studio: Studio, draft: dict[str, Any], base: str | None):
    return studio.post("/api/changes/preview", {"kind": "flow-default", "target": TARGET, "payload": {"flow": draft},
                                                "base_sha256": base})


def codes(items: list[dict[str, Any]]) -> set[str]:
    return {i["code"] for i in items}


def resolve(studio: Studio, project: str) -> dict[str, Any]:
    return studio.client.get(f"/api/projects/{project}/flow-status").json()["data"]


# -- มุมมอง -----------------------------------------------------------------------------------------

def test_view_without_file_shows_packaged_default(studio: Studio) -> None:
    data = view(studio)
    assert data["exists"] is False and data["base_sha256"] is None and data["flow"]["default"] == "packaged"
    assert data["target"] == TARGET and data["overrides"] == 0 and data["issues"] == []
    rows = {s["id"]: s for s in data["tracks"]["full"]["stages"]}
    assert rows["requirements-review"]["locked"] is True and rows["ux-design"]["mustFollowLocked"] is True
    assert data["options"]["gates"] == ["none", "human", "agent-confirm"]


def test_view_with_file_counts_changes_against_packaged(studio: Studio) -> None:
    raw = install_qa_default(studio)
    data = view(studio)
    assert data["exists"] is True and data["base_sha256"] == sha(raw) and data["flow"]["default"] == TARGET
    rows = {s["id"]: s for s in data["tracks"]["full"]["stages"]}
    assert rows["qa"]["default"] is None and rows["qa"]["overridden"] is True
    assert rows["review"]["overriddenFields"] == ["after"] and data["overrides"] == 2


def test_view_of_bypassing_default_offers_policy_fix(studio: Studio) -> None:
    data = default_data()
    stage(data, "requirements-review")["enabled"] = False
    stage(data, "devops")["optional"] = False
    default_file(studio).write_text(dump(data), encoding="utf-8")
    result = view(studio)
    assert "locked_gate_violation" in codes(result["issues"])
    fix = result["policyFix"]
    assert stage(fix, "requirements-review")["enabled"] is True and stage(fix, "devops")["optional"] is False


def test_default_routes_need_login_and_csrf(studio: Studio, settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    anon = TestClient(create_app(settings))
    assert anon.get("/api/flow-default").status_code == 401
    assert anon.post("/api/flow-default/validate", json={"flow": {}}).status_code == 401
    assert studio.client.post("/api/flow-default/validate", json={"flow": {}}).status_code == 403


# -- validate + preview: ตรวจข้ามโปรเจกต์ ------------------------------------------------------------

def test_edit_that_breaks_a_project_is_an_error_with_its_name(studio: Studio) -> None:
    raw = install_qa_default(studio)
    draft = draft_of(view(studio))
    # เอา qa ออกจาก default (กลับเป็นสายเดิม) — overlay ของ uses-qa อ้าง qa
    draft["tracks"]["full"]["stages"] = [s for s in draft["tracks"]["full"]["stages"] if s["id"] != "qa"]
    stage(draft, "review")["after"] = ["develop"]
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    broken = [e for e in checked["errors"] if e["code"] == "project_flow_broken"]
    assert broken and broken[0]["project"] == "uses-qa" and "uses-qa" in broken[0]["error"]
    assert broken[0]["reason"] == "unknown_stage"
    data = preview(studio, draft, sha(raw)).json()["data"]
    assert any(e.get("project") == "uses-qa" for e in data["errors"])
    assert data["op"] == "delete"  # draft = packaged → กลับไปใช้ default ของแอป
    assert studio.commit(data, typed="flow.default.yaml").status_code == 422
    assert default_file(studio).read_bytes() == raw


def test_lock_policy_applies_to_the_default(studio: Studio) -> None:
    draft = draft_of(view(studio))
    stage(draft, "requirements-review")["enabled"] = False
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    assert "locked_gate_violation" in codes(checked["errors"])
    data = preview(studio, draft, None).json()["data"]
    assert "locked_gate_violation" in codes(data["errors"])
    assert studio.commit(data, typed="flow.default.yaml").status_code == 422 and not default_file(studio).exists()


def test_first_save_creates_the_file_and_projects_follow(studio: Studio) -> None:
    registry_file = studio.settings.veda_root / ".veda" / "projects" / "registry.json"
    before = (registry_file.read_bytes(), registry_file.stat().st_mtime_ns)
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    stage(draft, "retrospective")["enabled"] = False
    data = preview(studio, draft, None).json()["data"]
    assert data["op"] == "create" and data["impact"] == "high" and data["errors"] == [], data["errors"]
    assert studio.commit(data, typed="flow.default.yaml").status_code == 200
    text = default_file(studio).read_text(encoding="utf-8")
    assert text.startswith("#") and "\r" not in text
    # โปรเจกต์ที่ไม่มี overlay ตามค่าใหม่; โปรเจกต์ที่ override field เดียวกันคงค่าของตัวเอง (AC ระดับโปรเจกต์ 2)
    plain = {s["id"]: s for s in resolve(studio, "plain")["stages"]}
    custom = {s["id"]: s for s in resolve(studio, "custom")["stages"]}
    assert plain["retrospective"]["status"] == "skipped" and custom["retrospective"]["status"] == "skipped"
    assert plain["devops"]["optional"] is False and plain["devops"]["enabled"] is True
    assert custom["devops"]["enabled"] is False  # overlay ของ custom ยังชนะ
    assert (registry_file.read_bytes(), registry_file.stat().st_mtime_ns) == before


def test_edit_keeps_comments_of_existing_default(studio: Studio) -> None:
    template = DEFAULT_TEMPLATE.read_bytes()
    default_file(studio).write_bytes(template)
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    data = preview(studio, draft, sha(template)).json()["data"]
    assert data["errors"] == [] and data["diff"]["added"] == 1 and data["diff"]["removed"] == 1
    assert studio.commit(data, typed="flow.default.yaml").status_code == 200
    text = default_file(studio).read_text(encoding="utf-8")
    assert text.splitlines()[:3] == template.decode("utf-8").splitlines()[:3]  # comment หัวไฟล์คงอยู่


def test_already_broken_project_warns_but_does_not_block(studio: Studio) -> None:
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      nope: {enabled: false}\n", encoding="utf-8")
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    data = preview(studio, draft, None).json()["data"]
    assert data["errors"] == [] and "project_flow_already_broken" in codes(data["warnings"])


def test_commit_rechecks_projects_under_lock(studio: Studio) -> None:
    raw = install_qa_default(studio)
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").unlink()  # ตอน preview ไม่มีใครอ้าง qa
    draft = draft_of(view(studio))
    draft["tracks"]["full"]["stages"] = [s for s in draft["tracks"]["full"]["stages"] if s["id"] != "qa"]
    stage(draft, "review")["after"] = ["develop"]
    stage(draft, "devops")["optional"] = False  # ไม่ให้เท่า packaged (ทาง modify ไม่ใช่ delete)
    data = preview(studio, draft, sha(raw)).json()["data"]
    assert data["errors"] == [] and data["op"] == "modify"
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      qa: {enabled: false}\n", encoding="utf-8")
    response = studio.commit(data, typed="flow.default.yaml")
    assert response.status_code == 422 and default_file(studio).read_bytes() == raw


# -- revert และลบ (retro epic-3 A2) ---------------------------------------------------------------

def _create_qa_default_via_app(studio: Studio) -> dict[str, Any]:
    draft = copy.deepcopy(with_qa())
    for s in draft["tracks"]["full"]["stages"]:
        s.setdefault("skill", None)
        s.setdefault("condition", None)
    data = preview(studio, draft, None).json()["data"]
    assert data["errors"] == [] and data["op"] == "create", data["errors"]
    return studio.commit(data, typed="flow.default.yaml").json()["data"]


def test_revert_of_the_create_entry_deletes_and_is_checked(studio: Studio) -> None:
    created = _create_qa_default_via_app(studio)
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      qa: {enabled: false}\n", encoding="utf-8")
    current = default_file(studio).read_bytes()
    data = studio.post("/api/changes/preview", {"kind": "revert", "target": TARGET, "payload": {"change_id": created["change_id"]},
                                                "base_sha256": sha(current)}).json()["data"]
    assert data["op"] == "delete"
    broken = [e for e in data["errors"] if e["code"] == "project_flow_broken"]
    assert broken and broken[0]["project"] == "uses-qa" and broken[0]["reason"] == "unknown_stage"
    assert studio.commit(data, typed="flow.default.yaml").status_code == 422
    assert default_file(studio).read_bytes() == current  # ไม่ใช่ errors=[] แล้วโปรเจกต์พังทีหลัง


def test_revert_to_an_older_default_is_checked(studio: Studio) -> None:
    template = DEFAULT_TEMPLATE.read_bytes()
    default_file(studio).write_bytes(template)
    draft = copy.deepcopy(with_qa())
    for s in draft["tracks"]["full"]["stages"]:
        s.setdefault("skill", None)
        s.setdefault("condition", None)
    data = preview(studio, draft, sha(template)).json()["data"]
    assert data["errors"] == []
    entry = studio.commit(data, typed="flow.default.yaml").json()["data"]
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      qa: {enabled: false}\n", encoding="utf-8")
    current = default_file(studio).read_bytes()
    revert = studio.post("/api/changes/preview", {"kind": "revert", "target": TARGET, "payload": {"change_id": entry["change_id"]},
                                                  "base_sha256": sha(current)}).json()["data"]
    assert revert["op"] == "modify" and any(e.get("project") == "uses-qa" for e in revert["errors"])
    assert studio.commit(revert, typed="flow.default.yaml").status_code == 422


def test_reset_to_packaged_deletes_when_safe(studio: Studio) -> None:
    template = DEFAULT_TEMPLATE.read_bytes()
    data = default_data()
    stage(data, "devops")["optional"] = False
    raw = dump(data).encode()
    default_file(studio).write_bytes(raw)
    packaged = view(studio)["defaultFlow"]
    result = preview(studio, packaged, sha(raw)).json()["data"]
    assert result["op"] == "delete" and result["errors"] == []
    assert studio.commit(result, typed="flow.default.yaml").status_code == 200 and not default_file(studio).exists()
    assert view(studio)["exists"] is False
    assert template


@pytest.mark.parametrize("target,status", [(".veda/projects/plain/flow.yaml", 403), (".veda/projects/registry.json", 403)])
def test_flow_default_kind_target_rules(studio: Studio, target: str, status: int) -> None:
    draft = draft_of(view(studio))
    response = studio.post("/api/changes/preview", {"kind": "flow-default", "target": target, "payload": {"flow": draft},
                                                    "base_sha256": None})
    assert response.status_code == status


def test_project_named_default_does_not_collide(studio: Studio, settings: Settings) -> None:
    registry(settings.veda_root, {"name": "default", "status": "developing", "requirementsReady": True, "hasFrontend": False})
    assert studio.client.get("/api/flows/default").json()["data"]["target"] == ".veda/projects/default/flow.yaml"
    assert view(studio)["target"] == TARGET


# -- เวลา (epic 2 retro: วัดก่อน triage) ------------------------------------------------------------

def test_cross_project_check_with_many_projects_is_fast(studio: Studio, settings: Settings) -> None:
    root = settings.veda_root
    projects = [{"name": f"p{i:02d}", "status": "developing", "requirementsReady": True, "hasFrontend": False}
                for i in range(60)]
    registry(root, *projects)
    for p in projects:
        (project_dir(root, p["name"]) / "flow.yaml").write_text(
            "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n", encoding="utf-8")
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    start = time.perf_counter()
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    validate_s = time.perf_counter() - start
    start = time.perf_counter()
    data = preview(studio, draft, None).json()["data"]
    preview_s = time.perf_counter() - start
    print(f"60 projects: validate={validate_s:.2f}s preview={preview_s:.2f}s")
    assert checked["errors"] == [] and data["errors"] == []
    assert validate_s < 5 and preview_s < 8, (validate_s, preview_s)


def test_reset_without_file_is_no_change(studio: Studio) -> None:
    result = preview(studio, view(studio)["defaultFlow"], None).json()["data"]
    assert [e["code"] for e in result["errors"]] == ["no_change"]


def test_delete_of_default_rechecks_projects_under_lock(studio: Studio) -> None:
    raw = install_qa_default(studio)
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").unlink()
    data = preview(studio, view(studio)["defaultFlow"], sha(raw)).json()["data"]
    assert data["op"] == "delete" and data["errors"] == []
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      qa: {enabled: false}\n", encoding="utf-8")
    response = studio.commit(data, typed="flow.default.yaml")
    assert response.status_code == 422 and default_file(studio).read_bytes() == raw


@pytest.mark.parametrize("overlay", [b"extends: default\ntracks: [\n", b"- just a list\n", b"\xff\xfe\x00\xd8"])
def test_unreadable_project_overlay_warns_but_does_not_block(studio: Studio, overlay: bytes) -> None:
    (project_dir(studio.settings.veda_root, "uses-qa") / "flow.yaml").write_bytes(overlay)
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    data = preview(studio, draft, None).json()["data"]
    assert data["errors"] == [] and any(w.get("project") == "uses-qa" for w in data["warnings"])
    assert any(w.get("project") == "uses-qa" for w in view(studio)["warnings"])  # บอกตั้งแต่เปิดหน้า


def test_broken_current_default_can_be_fixed(studio: Studio) -> None:
    default_file(studio).write_text("version: 1\ntracks: [\n", encoding="utf-8")
    data = view(studio)
    assert data["loadError"] is True and data["policyFix"] == data["defaultFlow"] and data["editable"] is True
    draft = copy.deepcopy(data["defaultFlow"])
    stage(draft, "devops")["optional"] = False
    result = preview(studio, draft, data["base_sha256"]).json()["data"]
    assert result["errors"] == [] and "format_rewritten" in codes(result["warnings"])
    assert studio.commit(result, typed="flow.default.yaml").status_code == 200


def test_non_text_default_is_not_editable(studio: Studio) -> None:
    default_file(studio).write_bytes(b"\xff\xfe\x00\xd8")
    data = view(studio)
    assert data["editable"] is False and data["policyFix"] is None and data["notEditableReason"]


def test_delete_blocked_when_packaged_default_does_not_fit_the_catalog(studio: Studio) -> None:
    data = default_data()
    stage(data, "devops")["agent"] = "skill-scout"
    raw = dump(data).encode()
    default_file(studio).write_bytes(raw)
    (studio.settings.veda_root / ".claude" / "agents" / "devops.md").unlink()  # packaged อ้าง devops ที่ไม่มีแล้ว
    result = preview(studio, view(studio)["defaultFlow"], sha(raw)).json()["data"]
    assert result["op"] == "delete" and "default_invalid" in codes(result["errors"])


def test_unreadable_registry_blocks_with_a_reason(studio: Studio) -> None:
    draft = draft_of(view(studio))
    stage(draft, "devops")["optional"] = False
    (studio.settings.veda_root / ".veda" / "projects" / "registry.json").write_text("{not json", encoding="utf-8")
    checked = studio.post("/api/flow-default/validate", {"flow": draft}).json()["data"]
    assert "registry_unreadable" in codes(checked["errors"])


@pytest.mark.parametrize("text", ["version: 1\ntracks: [a, b]\n", "version: 1\ntracks:\n  full:\n    stages:\n"])
def test_malformed_existing_default_falls_back_to_rewrite(studio: Studio, text: str) -> None:
    default_file(studio).write_text(text, encoding="utf-8")
    data = view(studio)
    draft = copy.deepcopy(data["defaultFlow"])
    stage(draft, "devops")["optional"] = False
    response = preview(studio, draft, data["base_sha256"])
    assert response.status_code == 200, response.text
