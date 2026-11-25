"""Story 3.2 — ``bmad-studio flow resolve`` (REQ-048 a–d, AD-1/AD-8).

ทุก test ใช้ tmp veda (registry + state ของโปรเจกต์ปลอม) — ไม่แตะ veda จริง.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.cli.main import main as cli_main

from .flowkit import default_data, dump, seed_catalog, stage

REPO = Path(__file__).resolve().parents[1]


def registry(veda_root: Path, *projects: dict[str, Any]) -> None:
    (veda_root / ".veda" / "projects" / "registry.json").write_text(
        json.dumps({"projects": list(projects)}, ensure_ascii=False), encoding="utf-8")
    for p in projects:
        (veda_root / ".veda" / "projects" / p["name"]).mkdir(exist_ok=True)


def project_dir(veda_root: Path, name: str) -> Path:
    return veda_root / ".veda" / "projects" / name


def sprint(veda_root: Path, name: str, statuses: dict[str, str]) -> None:
    body = "\n".join(f"  {k}: {v}" for k, v in statuses.items())
    (project_dir(veda_root, name) / "sprint-status.yaml").write_text(
        f"# generated\nproject: {name}\ndevelopment_status:\n{body}\n", encoding="utf-8")


def run(veda_root: Path, capsys: pytest.CaptureFixture[bytes], *args: str) -> tuple[int, Any]:
    code = cli_main(["flow", "resolve", *args, "--json", "--veda-root", str(veda_root)])
    out = capsys.readouterr()
    return code, json.loads(out.out)


@pytest.fixture
def veda(veda_root: Path) -> Path:
    seed_catalog(veda_root)
    return veda_root


def nxt(veda: Path, capsys, name: str, *extra: str) -> str | None:
    code, data = run(veda, capsys, name, *extra)
    assert code == 0, data
    return None if data["next"] is None else data["next"]["stage"]


# -- (a) ไม่มี flow.yaml = flow ปัจจุบันใน CLAUDE.md ----------------------------

def test_default_flow_walks_the_claude_md_pipeline(veda: Path, capsys) -> None:
    p = {"name": "demo", "status": "bootstrapped", "hasFrontend": True}
    registry(veda, p)
    assert nxt(veda, capsys, "demo") == "requirements"  # ba
    (project_dir(veda, "demo") / "requirements.md").write_text("REQ-001\n", encoding="utf-8")
    assert nxt(veda, capsys, "demo") == "requirements-review"  # รอคน
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["agent"] == "human" and data["next"]["gate"] == "human" and "/review" in data["next"]["note"]
    registry(veda, {**p, "requirementsReady": True})
    assert nxt(veda, capsys, "demo") == "ux-design"
    registry(veda, {**p, "requirementsReady": True, "designReady": True})
    assert nxt(veda, capsys, "demo") == "architecture"
    ready = {**p, "requirementsReady": True, "designReady": True, "planReady": True, "status": "planned"}
    registry(veda, ready)
    sprint(veda, "demo", {"epic-1": "backlog", "1-1-a": "ready-for-dev", "1-2-b": "ready-for-dev",
                          "epic-1-retrospective": "optional"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "develop" and data["next"]["skill"] == "bmad-build"
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "review", "1-2-b": "ready-for-dev"})
    assert nxt(veda, capsys, "demo") == "review"  # เคลียร์ review ก่อน
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "done", "1-2-b": "in-progress"})
    (project_dir(veda, "demo") / "reviews").mkdir()
    (project_dir(veda, "demo") / "reviews" / "1-2-b.md").write_text("fix\n", encoding="utf-8")
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "develop" and "1-2-b" in data["next"]["note"]
    sprint(veda, "demo", {"epic-1": "done", "1-1-a": "done", "1-2-b": "done", "epic-1-retrospective": "optional"})
    assert nxt(veda, capsys, "demo") == "retrospective"
    sprint(veda, "demo", {"epic-1": "done", "1-1-a": "done", "1-2-b": "done", "epic-1-retrospective": "done"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "devops" and data["next"]["optional"] is True
    registry(veda, {**ready, "status": "deployed", "dockerImage": "x/y:1"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"] is None and data["complete"] is True


def test_no_frontend_skips_ux_from_gate_to_architect(veda: Path, capsys) -> None:
    registry(veda, {"name": "bot", "status": "bootstrapped", "hasFrontend": False, "requirementsReady": True})
    _code, data = run(veda, capsys, "bot")
    assert data["next"]["stage"] == "architecture"
    ux = next(s for s in data["stages"] if s["id"] == "ux-design")
    assert ux["status"] == "skipped" and "hasFrontend" in ux["skipReason"]


def test_json_shape(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": True})
    code, data = run(veda, capsys, "demo")
    assert code == 0 and data["ok"] is True
    assert set(data["next"]) >= {"stage", "agent", "skill", "gate"}
    assert all({"id", "status", "skipReason", "locked", "lockReason", "overridden"} <= set(s) for s in data["stages"])
    assert {"stage": "requirements-review"}.items() <= data["locked"][0].items()
    assert data["flow"] == {"default": "packaged", "overlay": None}


def test_text_output_is_thai(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False})
    assert cli_main(["flow", "resolve", "demo", "--veda-root", str(veda)]) == 0
    out = capsys.readouterr().out
    assert "ขั้นถัดไป: architecture → agent architect" in out and "ข้าม" in out


# -- (b) overlay ปิด ux → next ข้ามจาก human review ไป architect ---------------

def test_overlay_disabling_ux_goes_to_architect(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": True})
    assert nxt(veda, capsys, "demo") == "ux-design"
    (project_dir(veda, "demo") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n", encoding="utf-8")
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "architecture"
    ux = next(s for s in data["stages"] if s["id"] == "ux-design")
    assert ux["status"] == "skipped" and ux["overridden"] is True and "enabled" in ux["skipReason"]
    assert data["flow"]["overlay"] == ".veda/projects/demo/flow.yaml"


def test_gate_not_approved_blocks_everything_after(veda: Path, capsys) -> None:
    """requirementsReady เป็น false → ไม่ว่ามี plan/sprint อะไร next ไม่เลย gate."""
    registry(veda, {"name": "demo", "requirementsReady": False, "designReady": True, "planReady": True,
                    "hasFrontend": True, "status": "developing"})
    (project_dir(veda, "demo") / "requirements.md").write_text("x\n", encoding="utf-8")
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "review"})
    assert nxt(veda, capsys, "demo") == "requirements-review"
    registry(veda, {"name": "demo", "requirementsReady": "true", "planReady": True})  # string ไม่ใช่ true
    assert nxt(veda, capsys, "demo") == "requirements-review"  # มี requirements.md แล้ว
    (project_dir(veda, "demo") / "requirements.md").unlink()
    assert nxt(veda, capsys, "demo") == "requirements"  # ba ก่อน gate


# -- quick track ---------------------------------------------------------------

def test_quick_track_entry_condition(veda: Path, capsys) -> None:
    registry(veda, {"name": "new", "status": "planned", "requirementsReady": True})
    code, data = run(veda, capsys, "new", "--track", "quick")
    assert code == 0 and data["next"] is None and data["entry"]["ok"] is False and "planned" in data["entry"]["reason"]
    registry(veda, {"name": "old", "status": "running", "requirementsReady": True})
    assert nxt(veda, capsys, "old", "--track", "quick") == "develop"
    (project_dir(veda, "old") / "quick").mkdir()
    (project_dir(veda, "old") / "quick" / "2026-11-25-fix.md").write_text("---\nstatus: 'review'\n---\n", encoding="utf-8")
    assert nxt(veda, capsys, "old", "--track", "quick") == "review"
    registry(veda, {"name": "old", "status": "running", "requirementsReady": False})
    assert run(veda, capsys, "old", "--track", "quick")[1]["entry"]["ok"] is False


# -- (c) อ่านอย่างเดียว ------------------------------------------------------------

def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        else:
            out[str(path.relative_to(root)) + "/"] = ("dir", 0)
    return out


def test_resolver_writes_nothing(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": True, "status": "developing",
                    "planReady": True, "designReady": True},
             {"name": "bad", "requirementsReady": True})
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "review"})
    (project_dir(veda, "demo") / "flow.yaml").write_text("extends: default\n", encoding="utf-8")
    (project_dir(veda, "bad") / "flow.yaml").write_text("extends: default\ntracks: {full: {stages: {nope: {}}}}\n",
                                                        encoding="utf-8")
    before = _snapshot(veda)
    for args in (["demo"], ["demo", "--track", "quick"], ["bad"], ["missing"]):
        cli_main(["flow", "resolve", *args, "--json", "--veda-root", str(veda)])
        cli_main(["flow", "resolve", *args, "--veda-root", str(veda)])
    cli_main(["flow", "validate", "demo", "--veda-root", str(veda)])
    capsys.readouterr()
    assert _snapshot(veda) == before


def test_no_write_code_path_in_resolver_modules() -> None:
    from .test_write_path_scan import raw_write_calls, _references_fs_writer

    flow_dir = REPO / "src" / "bmad_studio" / "core" / "flow"
    for path in sorted(flow_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert raw_write_calls(source) == [] and _references_fs_writer(source) == [], path.name
    assert "registry.json" not in (flow_dir / "resolve.py").read_text(encoding="utf-8")


# -- (d) flow ผิด = exit ≠ 0 พร้อม error ไม่เดา -------------------------------------

@pytest.mark.parametrize("overlay, code", [
    ("extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: false}\n", "locked_gate_violation"),
    ("extends: default\ntracks:\n  full:\n    stages:\n      develop: {after: [requirements]}\n", "locked_gate_violation"),
    ("extends: default\ntracks:\n  full:\n    stages:\n      develop: {agent: ghost}\n", "unknown_agent"),
    ("extends: default\ntracks:\n  full:\n    stages:\n      bootstrap: {after: [devops]}\n", "cycle"),
    ("extends: default\nfoo: 1\n", "schema_invalid"),
    ("a: &x 1\nb: *x\n", "yaml_invalid"),
    ("version: 1\ntracks: {}\n", "schema_invalid"),
])
def test_invalid_flow_exits_nonzero_with_issues(veda: Path, capsys, overlay: str, code: str) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})
    (project_dir(veda, "demo") / "flow.yaml").write_text(overlay, encoding="utf-8")
    exit_code, data = run(veda, capsys, "demo")
    assert exit_code == 2 and data["ok"] is False and data["code"] == "flow_invalid"
    assert data["source"] == ".veda/projects/demo/flow.yaml"
    assert code in {i["code"] for i in data["issues"]}
    assert "next" not in data


def test_invalid_default_file_exits_nonzero(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})
    data = default_data()
    stage(data, "requirements-review")["gate"] = "none"
    (veda / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    exit_code, out = run(veda, capsys, "demo")
    assert exit_code == 2 and out["source"] == ".veda/flow.default.yaml"


@pytest.mark.parametrize("name", ["missing", "../etc", "demo/../demo", "DEMO", "", "a" * 300])
def test_unknown_or_unsafe_project(veda: Path, capsys, name: str) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})
    exit_code, data = run(veda, capsys, name)
    assert exit_code == 2 and data["code"] == "project_not_found"


def test_broken_registry_and_missing_root(veda: Path, capsys, tmp_path: Path) -> None:
    (veda / ".veda" / "projects" / "registry.json").write_text("{not json", encoding="utf-8")
    exit_code, data = run(veda, capsys, "demo")
    assert exit_code == 2 and data["code"] == "registry_unreadable"
    exit_code, data = run(tmp_path / "nope", capsys, "demo")
    assert exit_code == 2 and data["code"] == "veda_root_missing"


def test_unreadable_sprint_status_is_a_warning_not_an_error(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "planReady": True, "designReady": True,
                    "hasFrontend": True, "status": "developing"})
    (project_dir(veda, "demo") / "sprint-status.yaml").write_text("a: &x 1\n", encoding="utf-8")
    code, data = run(veda, capsys, "demo")
    assert code == 0 and data["warnings"][0]["code"] == "sprint_status_unreadable"
    assert data["next"]["stage"] == "develop"


def test_pending_ux_is_not_skipped_by_active_downstream_work(veda: Path, capsys) -> None:
    """บรรพบุรุษที่ pending (ux ยังไม่ออกแบบ) บล็อกงาน review ที่อยู่ปลายทาง."""
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": True, "designReady": False,
                    "planReady": True, "status": "developing"})
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "review"})
    assert nxt(veda, capsys, "demo") == "ux-design"


def test_missing_condition_flag_does_not_skip(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})  # ไม่มี hasFrontend
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "ux-design"
    assert any(w["code"] == "condition_unknown" for w in data["warnings"])


def test_retro_offered_when_an_epic_finishes_mid_sprint(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False, "planReady": True})
    sprint(veda, "demo", {"epic-1": "done", "1-1-a": "done", "epic-1-retrospective": "optional",
                          "epic-2": "backlog", "2-1-a": "ready-for-dev"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "retrospective" and "epic-1" in data["next"]["note"]
    sprint(veda, "demo", {"epic-1": "done", "1-1-a": "done", "epic-1-retrospective": "done",
                          "epic-2": "backlog", "2-1-a": "ready-for-dev"})
    assert nxt(veda, capsys, "demo") == "develop"


def test_unknown_story_status_counts_as_not_done(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False, "planReady": True})
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "drafted", "1-2-b": "done"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "develop"
    assert any(w["code"] == "story_status_unknown" for w in data["warnings"])


def test_sprint_without_epics_skips_retro(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False, "planReady": True})
    sprint(veda, "demo", {"1-1-a": "done"})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "devops"


def test_legacy_developed_project_without_sprint(veda: Path, capsys) -> None:
    registry(veda, {"name": "old", "status": "developed", "requirementsReady": True, "designReady": True,
                    "planReady": True, "hasFrontend": True})
    _code, data = run(veda, capsys, "old")
    rows = {s["id"]: s for s in data["stages"]}
    assert rows["develop"]["status"] == "done" and rows["review"]["status"] == "done"
    assert rows["retrospective"]["status"] == "skipped" and rows["retrospective"]["skipReason"]
    assert data["next"]["stage"] == "devops" and any("quick" in h for h in data["hints"])
    registry(veda, {"name": "old", "status": "developing", "requirementsReady": True, "designReady": True,
                    "planReady": True, "hasFrontend": True})
    _code, data = run(veda, capsys, "old")
    assert {s["id"]: s for s in data["stages"]}["develop"]["status"] == "active"


def test_quick_spec_status_read_from_front_matter_only(veda: Path, capsys) -> None:
    registry(veda, {"name": "old", "status": "running", "requirementsReady": True})
    quick = project_dir(veda, "old") / "quick"
    quick.mkdir()
    (quick / "a.md").write_text("---\nstatus: in_review\n---\n", encoding="utf-8")
    (quick / "b.md").write_text("---\nstatus: done\n---\n```\nstatus: review\n```\n", encoding="utf-8")
    (quick / "c.md").write_text("no status here\n", encoding="utf-8")
    _code, data = run(veda, capsys, "old", "--track", "quick")
    assert data["next"]["stage"] == "review" and "a.md" in data["next"]["note"] and "b.md" not in data["next"]["note"]
    assert any(w["code"] == "quick_status_unknown" for w in data["warnings"])


@pytest.mark.parametrize("overlay, code", [
    ("extends: default\ntracks:\n  full:\n    stages:\n      develop: {after: [requirements]}\n", "locked_gate_violation"),
    ("extends: default\ntracks:\n  full:\n    stages:\n      develop: {agent: ghost}\n", "unknown_agent"),
])
def test_validate_project_with_invalid_overlay(veda: Path, capsys, overlay: str, code: str) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})
    (project_dir(veda, "demo") / "flow.yaml").write_text(overlay, encoding="utf-8")
    assert cli_main(["flow", "validate", "demo", "--json", "--veda-root", str(veda)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "flow_invalid" and code in {i["code"] for i in out["issues"]}
    assert cli_main(["flow", "validate", "ghost", "--json", "--veda-root", str(veda)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "project_not_found"


def test_validate_file_decides_by_structure(veda: Path, capsys, tmp_path: Path) -> None:
    full = tmp_path / "full.yaml"
    full.write_text("# extends: comment only\n" + dump(default_data()), encoding="utf-8")
    assert cli_main(["flow", "validate", "--file", str(full), "--veda-root", str(veda)]) == 0
    broken = tmp_path / "o.yaml"
    broken.write_text("extends: default\ntracks: [\n", encoding="utf-8")
    assert cli_main(["flow", "validate", "--file", str(broken), "--json", "--veda-root", str(veda)]) == 2
    capsys.readouterr()
    assert cli_main(["flow", "validate", "demo", "--file", str(full), "--json", "--veda-root", str(veda)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_arguments"
    assert cli_main(["flow", "validate", "--file", str(tmp_path / "nope.yaml"), "--json", "--veda-root", str(veda)]) == 2


def test_root_without_veda_dir_and_unknown_track(veda: Path, capsys) -> None:
    exit_code, data = run(veda / ".veda", capsys, "demo")
    assert exit_code == 2 and data["code"] == "veda_root_missing"
    registry(veda, {"name": "demo", "requirementsReady": True})
    data = default_data()
    del data["tracks"]["quick"]
    (veda / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    exit_code, out = run(veda, capsys, "demo", "--track", "quick")
    assert exit_code == 2 and out["code"] == "flow_invalid"  # default ไม่มี quick = ผิด policy ไม่ใช่เดา


def test_validate_command(veda: Path, capsys, tmp_path: Path) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True})
    assert cli_main(["flow", "validate", "--veda-root", str(veda)]) == 0
    capsys.readouterr()
    bad = tmp_path / "bad.yaml"
    bad.write_text("extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {gate: none}\n",
                   encoding="utf-8")
    assert cli_main(["flow", "validate", "--file", str(bad), "--json", "--veda-root", str(veda)]) == 2
    assert "locked_gate_violation" in {i["code"] for i in json.loads(capsys.readouterr().out)["issues"]}
    full = tmp_path / "full.yaml"
    data = default_data()
    stage(data, "develop")["after"] = ["requirements"]
    full.write_text(dump(data), encoding="utf-8")
    assert cli_main(["flow", "validate", "--file", str(full), "--veda-root", str(veda)]) == 2


def test_cli_runs_standalone_without_server(veda: Path) -> None:
    """เหมือนที่ orchestrator เรียก: process แยก, cwd อื่น, ไม่ import FastAPI."""
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False})
    env = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_STUDIO_") and k != "VEDA_ROOT"}
    env["PYTHONPATH"] = str(REPO / "src")
    code = ("import sys, runpy; sys.argv=['bmad-studio','flow','resolve','demo','--json','--veda-root',sys.argv[1]];"
            "from bmad_studio.cli.main import main; rc=main(sys.argv[1:]); "
            "assert 'fastapi' not in sys.modules, 'fastapi imported'; sys.exit(rc)")
    proc = subprocess.run([sys.executable, "-c", code, str(veda)], cwd=str(veda.parent), env=env,
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert json.loads(proc.stdout.decode("utf-8"))["next"]["stage"] == "architecture"


# -- rework round 1 (review 3-2 B1, N5, N7) ------------------------------------

@pytest.mark.parametrize("value", ["true", 1, None, "false", 0, 1.0, [True]])
def test_non_boolean_condition_is_not_skipped(veda: Path, capsys, value) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": value})
    _code, data = run(veda, capsys, "demo")
    ux = next(s for s in data["stages"] if s["id"] == "ux-design")
    assert ux["status"] != "skipped" and data["next"]["stage"] == "ux-design"
    warn = [w for w in data["warnings"] if w["code"] == "condition_unknown"]
    assert warn and warn[0]["stage"] == "ux-design" and "true/false" in warn[0]["error"]
    assert not any(t in warn[0]["error"] for t in ("NoneType", "str)", "int)", "float)", "list)"))  # ไม่ใช่ชื่อ type ของ Python


def test_boolean_false_condition_still_skips(veda: Path, capsys) -> None:
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False})
    _code, data = run(veda, capsys, "demo")
    assert data["next"]["stage"] == "architecture" and data["warnings"] == []


@pytest.mark.parametrize("value", ["true", 1, None, "True"])
def test_non_boolean_gate_and_entry_flags_fail_closed(veda: Path, capsys, value) -> None:
    registry(veda, {"name": "demo", "requirementsReady": value, "hasFrontend": False, "status": "running",
                    "planReady": True})
    (project_dir(veda, "demo") / "requirements.md").write_text("x\n", encoding="utf-8")
    assert nxt(veda, capsys, "demo") == "requirements-review"
    assert run(veda, capsys, "demo", "--track", "quick")[1]["entry"]["ok"] is False
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False, "planReady": True, "status": 5})
    assert run(veda, capsys, "demo", "--track", "quick")[1]["entry"]["ok"] is False
    registry(veda, {"name": "demo", "requirementsReady": True, "hasFrontend": False, "planReady": True,
                    "status": "running"})
    assert run(veda, capsys, "demo", "--track", "quick")[1]["entry"]["ok"] is True  # ควบคุม: ค่าถูกชนิดผ่าน


def test_ad8_backstop_redirects_active_loop_work_to_the_gate(veda: Path, capsys) -> None:
    """ด่านสุดท้ายของ AD-8 ทำงานจริงกับ flow ที่ถูกต้อง: gate รอคน + มี story รอ review."""
    from bmad_studio.core.flow.merge import effective_for
    from bmad_studio.core.flow.resolve import _pick_next, resolve_track, topo_order
    from bmad_studio.core.flow.status import find_project, load_state
    from bmad_studio.core.paths import VedaPaths

    registry(veda, {"name": "demo", "requirementsReady": False, "hasFrontend": False, "planReady": True,
                    "status": "developing"})
    (project_dir(veda, "demo") / "requirements.md").write_text("x\n", encoding="utf-8")
    sprint(veda, "demo", {"epic-1": "in-progress", "1-1-a": "review"})
    paths = VedaPaths(veda)
    eff = effective_for(paths, "demo", None)
    state = load_state(paths, find_project(paths, "demo"))
    result = resolve_track(eff, "full", state)
    rows = {s["id"]: s for s in result["stages"]}
    track = eff.flow.tracks["full"]
    by_id = {s.id: s for s in track.stages}
    # ไม่มีด่านนี้ ขั้นที่ถูกเลือกจะเลย gate
    assert _pick_next(topo_order(track), by_id, rows, False) == "review"
    assert result["next"]["stage"] == "requirements-review" and result["complete"] is False


@pytest.mark.parametrize("name", ["demo.", "Demo", "CON", "nul", "com1.x"])
def test_aliasing_registry_names_are_rejected(veda: Path, capsys, name: str) -> None:
    """ชื่อที่ NTFS ชี้ไปโฟลเดอร์ของโปรเจกต์อื่น (จุดท้าย, ต่างตัวพิมพ์) หรือเป็นอุปกรณ์ (CON/NUL/COMn)."""
    registry(veda, {"name": "demo", "requirementsReady": True})
    data = json.loads((veda / ".veda" / "projects" / "registry.json").read_text(encoding="utf-8"))
    data["projects"].append({"name": name, "requirementsReady": True})
    (veda / ".veda" / "projects" / "registry.json").write_text(json.dumps(data), encoding="utf-8")
    exit_code, out = run(veda, capsys, name)
    assert exit_code == 2 and out["code"] == "project_not_found"
    from bmad_studio.core.flow.status import read_registry
    from bmad_studio.core.paths import VedaPaths

    assert [p["name"] for p in read_registry(VedaPaths(veda))] == ["demo"]
