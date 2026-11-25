"""Story 3.3 — ``GET /api/projects`` และ ``/api/projects/<name>/flow-status`` (REQ-050, AD-1, AD-12)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api.app import create_app
from bmad_studio.core.flow.resolve import resolve_project
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.settings import Settings

from .flowkit import seed_catalog
from .test_changes_api import Studio
from .test_flow_resolve import _snapshot, project_dir, registry, sprint


@pytest.fixture
def studio(settings: Settings) -> Studio:
    root = settings.veda_root
    seed_catalog(root)
    registry(root,
             {"name": "alpha", "description": "UI app", "status": "developing", "requirementsReady": True,
              "designReady": True, "planReady": True, "hasFrontend": True},
             {"name": "bot", "status": "bootstrapped", "requirementsReady": True, "hasFrontend": False},
             {"name": "fresh", "status": "registered", "hasFrontend": True},
             {"name": "broken", "requirementsReady": True, "hasFrontend": False},
             {"name": "done-app", "status": "deployed", "requirementsReady": True, "designReady": True,
              "planReady": True, "hasFrontend": True, "dockerImage": "x/y:1"})
    sprint(root, "alpha", {"epic-1": "in-progress", "1-1-a": "review", "1-2-b": "ready-for-dev"})
    (project_dir(root, "bot") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n", encoding="utf-8")
    (project_dir(root, "broken") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: false}\n", encoding="utf-8")
    return Studio(settings)


def test_list_projects_matches_resolver(studio: Studio, settings: Settings) -> None:
    response = studio.client.get("/api/projects")
    assert response.status_code == 200
    items = {i["project"]: i for i in response.json()["data"]}
    assert set(items) == {"alpha", "bot", "fresh", "broken", "done-app"}
    paths = VedaPaths(settings.veda_root)
    for name in ("alpha", "bot", "fresh", "done-app"):
        assert items[name]["next"] == resolve_project(paths, name)["next"], name  # ค่าเดียวกับ flow resolve
    assert items["alpha"]["next"]["stage"] == "review"
    assert items["bot"]["overridden"] is True and items["alpha"]["overridden"] is False
    assert items["fresh"]["next"]["stage"] == "requirements"
    assert items["done-app"]["complete"] is True
    assert items["broken"]["error"]["code"] == "flow_invalid"
    assert any(i["code"] == "locked_gate_violation" for i in items["broken"]["error"]["issues"])
    ux = next(s for s in items["bot"]["stages"] if s["id"] == "ux-design")
    assert ux["status"] == "skipped" and ux["skipReason"]


def test_flow_status_has_lock_and_override_metadata(studio: Studio) -> None:
    data = studio.client.get("/api/projects/bot/flow-status").json()["data"]
    rows = {s["id"]: s for s in data["stages"]}
    assert rows["requirements-review"]["locked"] is True and rows["requirements-review"]["lockReason"]
    assert rows["devops"]["overridden"] is True and rows["devops"]["default"]["enabled"] is True
    assert rows["devops"]["status"] == "skipped" and "enabled" in rows["devops"]["skipReason"]
    assert data["next"]["stage"] == "architecture" and data["reviewUrl"]
    assert data["flow"]["overlay"] == ".veda/projects/bot/flow.yaml"


def test_flow_status_quick_track_and_gate(studio: Studio) -> None:
    data = studio.client.get("/api/projects/fresh/flow-status").json()["data"]
    assert data["next"]["stage"] == "requirements"
    (project_dir(studio.settings.veda_root, "fresh") / "requirements.md").write_text("x\n", encoding="utf-8")
    data = studio.client.get("/api/projects/fresh/flow-status").json()["data"]
    assert data["next"]["gate"] == "human" and data["next"]["agent"] == "human"
    quick = studio.client.get("/api/projects/fresh/flow-status?track=quick").json()["data"]
    assert quick["next"] is None and quick["entry"]["ok"] is False


def test_no_sprint_or_status_md_is_not_an_error(studio: Studio) -> None:
    response = studio.client.get("/api/projects/bot/flow-status")
    assert response.status_code == 200 and response.json()["data"]["hasSprint"] is False


def test_errors(studio: Studio) -> None:
    for path, status, code in (
        ("/api/projects/nope/flow-status", 404, "project_not_found"),
        ("/api/projects/..%2F..%2Fx/flow-status", 403, None),  # ไม่ตรง rule ใดของ access policy
        ("/api/projects/broken/flow-status", 422, "flow_invalid"),
        ("/api/projects/alpha/flow-status?track=hotfix", 422, "invalid_request"),
    ):
        response = studio.client.get(path)
        assert response.status_code == status, path
        body = response.json()
        assert body["ok"] is False and (code is None or body["code"] == code)
    issues = studio.client.get("/api/projects/broken/flow-status").json()["details"]["issues"]
    assert any(i["code"] == "locked_gate_violation" for i in issues)


def test_requires_session_and_read_permission(settings: Settings, studio: Studio) -> None:
    anonymous = TestClient(create_app(settings))
    assert anonymous.get("/api/projects").status_code == 401
    assert anonymous.get("/api/projects/alpha/flow-status").status_code == 401
    viewer = Studio(settings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert viewer.client.get("/api/projects").json()["code"] == "forbidden"


def test_project_routes_are_read_only(studio: Studio) -> None:
    """REQ-050 a: ไม่มี method เขียนบน /api/projects และการเรียกไม่เปลี่ยนไฟล์ใดใน veda."""
    from .test_access_policy import registered_routes

    project_routes = [(m, p) for m, p in registered_routes(studio.app) if p.startswith("/api/projects")]
    assert project_routes and {m for m, _ in project_routes} <= {"GET", "HEAD"}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert studio.client.request(method, "/api/projects/alpha/flow-status",
                                     headers={"X-CSRF-Token": studio.csrf}).status_code in {403, 405}
    before = _snapshot(studio.settings.veda_root)
    studio.client.get("/api/projects")
    for name in ("alpha", "bot", "fresh", "broken", "done-app", "nope"):
        studio.client.get(f"/api/projects/{name}/flow-status")
        studio.client.get(f"/api/projects/{name}/flow-status?track=quick")
    assert _snapshot(studio.settings.veda_root) == before


def test_registry_not_present_gives_empty_list(settings: Settings) -> None:
    (settings.veda_root / ".veda" / "projects" / "registry.json").unlink(missing_ok=True)
    assert Studio(settings).client.get("/api/projects").json()["data"] == []


def test_response_matches_schema(studio: Studio) -> None:
    from bmad_studio.api.schemas import FlowStatusEnvelope, ProjectListEnvelope

    ProjectListEnvelope.model_validate(studio.client.get("/api/projects").json())
    for name in ("alpha", "bot", "done-app"):
        for track in ("full", "quick"):
            FlowStatusEnvelope.model_validate(studio.client.get(f"/api/projects/{name}/flow-status?track={track}").json())
    json.dumps(studio.client.get("/api/projects").json(), ensure_ascii=False)


def test_invalid_default_flow_marks_every_item_without_failing_the_list(studio: Studio) -> None:
    from .flowkit import default_data, dump, stage

    data = default_data()
    stage(data, "requirements-review")["enabled"] = False
    (studio.settings.veda_root / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    response = studio.client.get("/api/projects")
    assert response.status_code == 200
    items = response.json()["data"]
    assert items and all(i["error"]["source"] == ".veda/flow.default.yaml" for i in items)


def test_added_stage_has_no_default_and_list_still_works(studio: Studio) -> None:
    (project_dir(studio.settings.veda_root, "bot") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    add:\n      - {id: qa, agent: reviewer, after: [review], optional: true}\n",
        encoding="utf-8")
    rows = {s["id"]: s for s in studio.client.get("/api/projects/bot/flow-status").json()["data"]["stages"]}
    assert rows["qa"]["default"] is None and rows["qa"]["overridden"] is True
    items = {i["project"]: i for i in studio.client.get("/api/projects").json()["data"]}
    assert items["bot"]["error"] is None and items["bot"]["overridden"] is True


def test_overlay_that_changes_nothing_is_not_overridden(studio: Studio) -> None:
    (project_dir(studio.settings.veda_root, "alpha") / "flow.yaml").write_text("extends: default\n", encoding="utf-8")
    items = {i["project"]: i for i in studio.client.get("/api/projects").json()["data"]}
    assert items["alpha"]["overridden"] is False


def test_has_sprint_true_and_review_url_unset(studio: Studio, settings: Settings) -> None:
    assert studio.client.get("/api/projects/alpha/flow-status").json()["data"]["hasSprint"] is True
    bare = Studio(settings.model_copy(update={"review_url": ""}))
    assert bare.client.get("/api/projects/alpha/flow-status").json()["data"]["reviewUrl"] is None


def test_viewer_cannot_read_flow_status(settings: Settings, studio: Studio) -> None:
    viewer = Studio(settings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert viewer.client.get("/api/projects/alpha/flow-status").json()["code"] == "forbidden"


def test_duplicate_registry_names_listed_once(studio: Studio) -> None:
    root = studio.settings.veda_root
    data = json.loads((root / ".veda" / "projects" / "registry.json").read_text(encoding="utf-8"))
    data["projects"].append({"name": "alpha", "status": "shelved"})
    (root / ".veda" / "projects" / "registry.json").write_text(json.dumps(data), encoding="utf-8")
    names = [i["project"] for i in studio.client.get("/api/projects").json()["data"]]
    assert names.count("alpha") == 1
