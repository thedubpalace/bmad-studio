"""Story 6-2 — skill catalog + ที่ใช้ใน flow (REQ-051, AD-2/AD-10) บน tmp veda เท่านั้น."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad_studio.core.settings import Settings

from .bmadkit import real_veda_hashes
from .flowkit import default_data, dump, seed_catalog, stage
from .test_changes_api import Studio


@pytest.fixture(scope="module", autouse=True)
def _real_veda_untouched():
    before = real_veda_hashes()
    yield
    assert real_veda_hashes() == before


@pytest.fixture
def root(veda_root: Path) -> Path:
    seed_catalog(veda_root)
    skills = veda_root / ".claude" / "skills"
    (skills / "veda").mkdir()
    (skills / "veda" / "SKILL.md").write_text("---\nname: veda\ndescription: orchestrator\n---\n# Veda\nbody\n",
                                             encoding="utf-8")
    (skills / "bmad-build" / "customize.toml").write_text("[workflow]\non_complete = \"\"\n", encoding="utf-8")
    (veda_root / "_bmad" / "custom" / "bmad-build.toml").write_text("[workflow]\n", encoding="utf-8")
    (veda_root / "_bmad" / "custom" / "bmad-code-review.user.toml").write_text("[workflow]\n", encoding="utf-8")
    projects = [{"name": "alpha", "status": "developing", "requirementsReady": True, "hasFrontend": False},
                {"name": "beta", "status": "developing", "requirementsReady": True, "hasFrontend": False},
                {"name": "broken", "status": "developing", "requirementsReady": True, "hasFrontend": False}]
    (veda_root / ".veda" / "projects" / "registry.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    for p in projects:
        (veda_root / ".veda" / "projects" / p["name"]).mkdir()
    # alpha: ขั้นวางแผนใช้ skill ที่ไม่มีโฟลเดอร์ (เขียนมือ — validator รายงาน unknown_skill)
    (veda_root / ".veda" / "projects" / "alpha" / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      architecture: {skill: bmad-ghost}\n", encoding="utf-8")
    (veda_root / ".veda" / "projects" / "broken" / "flow.yaml").write_text("extends: [\n", encoding="utf-8")
    return veda_root


@pytest.fixture
def studio(settings: Settings, root: Path) -> Studio:
    return Studio(settings)


def skills(studio: Studio) -> dict[str, dict]:
    response = studio.client.get("/api/skills")
    assert response.status_code == 200
    return {s["id"]: s for s in response.json()["data"]}


def test_catalog_groups_overrides_and_usage(studio: Studio) -> None:
    data = skills(studio)
    assert data["veda"]["group"] == "veda" and data["bmad-build"]["group"] == "bmad"
    assert data["veda"]["description"] == "orchestrator"  # field เดิมของ 4-1 คงรูป
    assert (data["bmad-build"]["overrideTeam"], data["bmad-build"]["overrideUser"]) == (True, False)
    assert (data["bmad-code-review"]["overrideTeam"], data["bmad-code-review"]["overrideUser"]) == (False, True)
    assert data["bmad-build"]["customizable"] is True and data["veda"]["customizable"] is False
    # flow เริ่มต้นใช้ bmad-build ใน full และ quick; alpha (overlay) ก็สืบทอด — นับ 2 flow (default + alpha)
    assert data["bmad-build"]["flowCount"] == 2
    assert {(u["flow"], u["track"]) for u in data["bmad-build"]["usedIn"]} >= {("default", "full"), ("default", "quick")}
    # alpha แทน bmad-architecture ด้วย bmad-ghost
    assert {u["flow"] for u in data["bmad-architecture"]["usedIn"]} == {"default"}
    assert data["veda"]["flowCount"] == 0 and data["veda"]["usedIn"] == []
    assert "bmad-ghost" not in data  # ไม่เสนอ skill ที่ไม่มีโฟลเดอร์ให้ StageInspector เลือก


def test_missing_skill_is_reported_with_where_it_is_used(studio: Studio) -> None:
    data = studio.client.get("/api/skill-references").json()["data"]
    assert data["missing"] == [{"id": "bmad-ghost", "flowCount": 1,
                                "usedIn": [{"flow": "alpha", "kind": "project", "track": "full",
                                            "stage": "architecture", "enabled": True}]}]
    assert data["unreadable"] == [{"flow": "broken", "kind": "project"}]
    # validator ของ flow ยังเป็น error (REQ-051 a)
    view = studio.client.get("/api/flows/alpha").json()
    issues = json.dumps(view, ensure_ascii=False)
    assert "unknown_skill" in issues


def test_skill_detail_is_read_only(settings: Settings, root: Path) -> None:
    studio = Studio(settings.model_copy(update={"enable_text_kind": True}))
    data = studio.client.get("/api/skills/veda").json()["data"]
    assert data["skillMd"].startswith("---\nname: veda") and data["skillMdPath"] == ".claude/skills/veda/SKILL.md"
    assert data["skillMdTruncated"] is False
    for method in ("put", "post", "delete", "patch"):
        response = getattr(studio.client, method)("/api/skills/veda", headers={"X-CSRF-Token": studio.csrf})
        assert response.status_code in {403, 404, 405}, method
    for kind in ("text", "revert", "config", "flow"):
        response = studio.post("/api/changes/preview", {"kind": kind, "target": ".claude/skills/veda/SKILL.md",
                                                        "payload": {"text": "x"}, "base_sha256": None})
        assert response.status_code == 403, kind
    assert (root / ".claude/skills/veda/SKILL.md").read_text(encoding="utf-8").startswith("---\nname: veda")


@pytest.mark.parametrize("skill", ["bmad-ghost", "..", "..%2F..%2FCLAUDE.md", "VEDA", "veda.", "veda%20", "%2e%2e",
                                   "bmad-build%2FSKILL.md", "ｖｅｄａ", "vеda"])
def test_skill_detail_rejects_unknown_and_lookalike_ids(studio: Studio, skill: str) -> None:
    response = studio.client.get(f"/api/skills/{skill}")
    # id ที่มี / หลัง decode ไม่ match route = 403 deny-by-default (AD-3); ชื่อที่ไม่อยู่ใน catalog = 404
    assert response.status_code in {403, 404}, (skill, response.text)
    assert "skillMd" not in response.text


def test_large_skill_md_is_truncated_on_a_line(studio: Studio, root: Path) -> None:
    body = "บรรทัดไทย\n" * 40000  # > 256 KiB
    (root / ".claude/skills/veda/SKILL.md").write_text("---\ndescription: x\n---\n" + body, encoding="utf-8")
    data = studio.client.get("/api/skills/veda").json()["data"]
    assert data["skillMdTruncated"] is True and data["skillMd"].endswith("บรรทัดไทย")


def test_binding_a_gated_skill_before_the_gate_is_refused(studio: Studio) -> None:
    """ผูก skill กับขั้นผ่าน flow editor ยังต้องผ่าน AD-8 (skill ใน GATED_SKILLS ห้ามก่อน gate)."""
    data = default_data()
    stage(data, "requirements")["skill"] = "bmad-build"
    response = studio.post("/api/flows/alpha/validate", {"flow": data})
    issues = response.json()["data"]["errors"]
    assert any(i["code"] == "locked_gate_violation" for i in issues), issues
    ok_data = default_data()
    stage(ok_data, "architecture")["skill"] = "bmad-architecture"
    ok_issues = studio.post("/api/flows/beta/validate", {"flow": ok_data}).json()["data"]["errors"]
    assert not [i for i in ok_issues if i["code"] == "locked_gate_violation"]


def test_skills_endpoint_requires_login(settings: Settings, root: Path) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    client = TestClient(create_app(settings))
    for path in ("/api/skills", "/api/skills/veda", "/api/skill-references"):
        assert client.get(path).status_code == 401


def test_dump_helper_is_used() -> None:
    assert "version" in dump(default_data())


# -- review 6-2 -------------------------------------------------------------------------------------

def _junction(link: Path, target: Path) -> bool:
    import subprocess

    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return result.returncode == 0


def test_junction_and_hard_link_skill_md_are_not_read(studio: Studio, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\ndescription: SECRET-OUTSIDE\n---\nTOP SECRET body\n", encoding="utf-8")
    skills_dir = root / ".claude" / "skills"
    if not _junction(skills_dir / "junc", outside):
        pytest.skip("mklink /J not available")
    hard = skills_dir / "hard"
    hard.mkdir()
    import os

    os.link(outside / "SKILL.md", hard / "SKILL.md")
    for name in ("junc", "hard"):
        response = studio.client.get(f"/api/skills/{name}")
        assert response.status_code in {200, 404}
        assert "SECRET" not in response.text and "TOP SECRET" not in response.text, name
        if response.status_code == 200:
            assert response.json()["data"]["skillMdError"] == "not_contained"
    assert "SECRET" not in studio.client.get("/api/skills").text


@pytest.mark.parametrize(("raw", "expect_tail"), [
    ("ก".encode("utf-8") * 100_000, "ก"),  # บรรทัดเดียว ยาว > 256 KiB ตัดกลางตัวอักษร
    (("line\n" * 70_000).encode("utf-16"), "line"),  # UTF-16 \u0e21\u0e35 BOM \u2014 \u0e15\u0e31\u0e14\u0e17\u0e35\u0e48\u0e02\u0e2d\u0e1a 2 byte
])
def test_truncated_skill_md_still_shows_text(studio: Studio, root: Path, raw: bytes, expect_tail: str) -> None:
    (root / ".claude/skills/veda/SKILL.md").write_bytes(raw)
    data = studio.client.get("/api/skills/veda").json()["data"]
    assert data["skillMdTruncated"] is True and data["skillMd"] and data["skillMdError"] is None
    assert data["skillMd"].rstrip().endswith(expect_tail)


def test_oversized_skill_md_reads_only_the_head(studio: Studio, root: Path) -> None:
    (root / ".claude/skills/veda/SKILL.md").write_bytes(b"x\n" * (9 * 1024 * 1024))  # 18 MiB
    data = studio.client.get("/api/skills/veda").json()["data"]
    assert data["skillMdTruncated"] is True and data["skillMd"].startswith("x\nx") and data["skillMdLimit"] == 256 * 1024


def test_disabled_stage_is_listed_but_not_counted(studio: Studio, root: Path) -> None:
    (root / ".veda/projects/beta/flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      retrospective: {enabled: false}\n", encoding="utf-8")
    data = skills(studio)["bmad-retrospective"]
    beta = [u for u in data["usedIn"] if u["flow"] == "beta"]
    assert beta == [{"flow": "beta", "kind": "project", "track": "full", "stage": "retrospective", "enabled": False}]
    assert data["flowCount"] == 2  # default + alpha (beta ปิดขั้นนี้)


def test_project_named_default_does_not_merge_with_the_default_flow(studio: Studio, root: Path) -> None:
    registry = root / ".veda/projects/registry.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["projects"].append({"name": "default", "status": "developing", "requirementsReady": True})
    registry.write_text(json.dumps(data), encoding="utf-8")
    (root / ".veda/projects/default").mkdir()
    (root / ".veda/projects/default/flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n", encoding="utf-8")
    uses = skills(studio)["bmad-build"]["usedIn"]
    assert {(u["flow"], u["kind"]) for u in uses} >= {("default", "default"), ("default", "project")}
    assert skills(studio)["bmad-build"]["flowCount"] == 3


def test_unreadable_default_flow_marks_usage_unknown(studio: Studio, root: Path) -> None:
    (root / ".veda/flow.default.yaml").write_text("version: [\n", encoding="utf-8")
    data = skills(studio)
    assert all(s["usageKnown"] is False for s in data.values())
    refs = studio.client.get("/api/skill-references").json()["data"]
    assert refs["known"] is False and {"flow": "default", "kind": "default"} in refs["unreadable"]


def test_unreadable_registry_is_reported_not_fatal(studio: Studio, root: Path) -> None:
    (root / ".veda/projects/registry.json").write_text("{not json", encoding="utf-8")
    response = studio.client.get("/api/skill-references")
    assert response.status_code == 200
    assert {"flow": "registry.json", "kind": "projects-list"} in response.json()["data"]["unreadable"]
    assert skills(studio)["bmad-build"]["usageKnown"] is True


def test_overlay_path_that_is_a_folder_is_unreadable_not_default(studio: Studio, root: Path) -> None:
    (root / ".veda/projects/beta/flow.yaml").mkdir()
    refs = studio.client.get("/api/skill-references").json()["data"]
    assert {"flow": "beta", "kind": "project"} in refs["unreadable"]


def test_light_list_for_the_flow_editor_skips_usage(studio: Studio) -> None:
    data = {s["id"]: s for s in studio.client.get("/api/skills?usage=0").json()["data"]}
    assert data["bmad-build"]["usedIn"] == [] and data["bmad-build"]["usageKnown"] is False
    assert data["bmad-build"]["description"] == "" or isinstance(data["bmad-build"]["description"], str)
