"""Story 3.2 — default + overlay merge และ to_overlay (REQ-045 c, AD-7) + overlay ต้องไม่ข้าม gate (AD-8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bmad_studio.core.flow.merge import (
    FlowInvalid,
    effective_for,
    load_default,
    merge,
    parse_overlay,
    to_overlay,
)
from bmad_studio.core.flow.schema import Flow
from bmad_studio.core.flow.validate import load_packaged_default
from bmad_studio.core.paths import VedaPaths

from .flowkit import CATALOG, codes, default_data, dump, seed_catalog, stage

DEFAULT = load_packaged_default()


def overlay_of(text: str):
    overlay, issues = parse_overlay(text)
    assert overlay is not None, issues
    return overlay


def merged(text: str) -> Flow:
    flow, issues, _touched = merge(DEFAULT, overlay_of(text), CATALOG)
    assert flow is not None, issues
    return flow


def merge_issues(text: str) -> list[dict[str, str]]:
    overlay, issues = parse_overlay(text)
    if overlay is None:
        return issues
    flow, issues, _ = merge(DEFAULT, overlay, CATALOG)
    assert flow is None
    return issues


def test_empty_overlay_is_default() -> None:
    assert merged("extends: default\n") == DEFAULT
    assert to_overlay(DEFAULT, DEFAULT) == {"extends": "default"}


def test_overlay_disables_ux_only() -> None:
    flow = merged("extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n")
    by_id = {s.id: s for s in flow.tracks["full"].stages}
    assert by_id["ux-design"].enabled is False
    assert [s.model_copy(update={"enabled": True}) if s.id == "ux-design" else s for s in flow.tracks["full"].stages] \
        == DEFAULT.tracks["full"].stages


def test_explicit_null_clears_optional_field() -> None:
    flow = merged("extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {condition: null}\n")
    assert {s.id: s for s in flow.tracks["full"].stages}["ux-design"].condition is None


@pytest.mark.parametrize("mutate", [
    lambda d: stage(d, "ux-design").update(enabled=False),
    lambda d: stage(d, "devops").update(enabled=False, optional=False),
    lambda d: stage(d, "review").update(skill="bmad-build", gate="none"),
    lambda d: stage(d, "ux-design").update(condition=None),
    lambda d: d["tracks"]["full"]["stages"].append(
        {"id": "qa", "agent": "reviewer", "skill": "bmad-code-review", "enabled": True, "optional": True,
         "after": ["review"], "gate": "none", "produces": [], "condition": None}),
    lambda d: (stage(d, "retrospective").update(after=["qa"]), d["tracks"]["full"]["stages"].append(
        {"id": "qa", "agent": "reviewer", "after": ["review"]})),
])
def test_to_overlay_round_trip(mutate) -> None:
    data = default_data()
    mutate(data)
    effective = Flow.model_validate(data)
    overlay = to_overlay(effective, DEFAULT)
    text = dump(overlay)
    flow = merged(text)
    assert {s.id: s for s in flow.tracks["full"].stages} == {s.id: s for s in effective.tracks["full"].stages}
    assert flow.tracks["quick"] == effective.tracks["quick"]
    # เก็บเฉพาะส่วนต่าง: ไม่มีขั้น/field ที่เท่ากับ default
    for stage_id, patch in overlay.get("tracks", {}).get("full", {}).get("stages", {}).items():
        ref = {s.id: s for s in DEFAULT.tracks["full"].stages}[stage_id]
        assert all(getattr(ref, k) != v for k, v in patch.items()), (stage_id, patch)
    assert to_overlay(flow, DEFAULT) == overlay


def test_to_overlay_cannot_remove_default_stage() -> None:
    data = default_data()
    data["tracks"]["full"]["stages"] = [s for s in data["tracks"]["full"]["stages"] if s["id"] != "devops"]
    with pytest.raises(FlowInvalid) as info:
        to_overlay(Flow.model_validate(data), DEFAULT)
    assert codes(info.value.issues) == {"cannot_remove_stage"}


def test_to_overlay_entry_condition_and_track_removal() -> None:
    data = default_data()
    data["tracks"]["quick"]["entry_condition"]["status"] = ["running"]
    overlay = to_overlay(Flow.model_validate(data), DEFAULT)
    assert overlay["tracks"]["quick"] == {"entry_condition": {"requirementsReady": True, "status": ["running"]}}
    assert "locked_gate_violation" in codes(merge_issues(dump(overlay)))  # round-trip ได้ แต่ policy ปฏิเสธ
    data = default_data()
    del data["tracks"]["quick"]
    with pytest.raises(FlowInvalid) as info:
        to_overlay(Flow.model_validate(data), DEFAULT)
    assert codes(info.value.issues) == {"cannot_remove_track"}


def test_unknown_stage_and_track_in_overlay() -> None:
    assert "unknown_stage" in codes(merge_issues("extends: default\ntracks:\n  full:\n    stages:\n      nope: {enabled: false}\n"))
    assert "unknown_track" in codes(merge_issues("extends: default\ntracks:\n  hotfix:\n    stages: {}\n"))


def test_overlay_must_extend_default_and_have_known_keys() -> None:
    assert codes(merge_issues("tracks: {}\n")) == {"schema_invalid"}
    assert codes(merge_issues("extends: other\n")) == {"schema_invalid"}
    assert codes(merge_issues("extends: default\ninclude: x.yaml\n")) == {"schema_invalid"}
    assert codes(merge_issues("extends: default\ntracks:\n  full:\n    stages:\n      develop: {id: x}\n")) == {"schema_invalid"}
    assert codes(merge_issues("extends: default\ntracks:\n  full:\n    stages:\n      develop: {enabled: null}\n")) == {"schema_invalid"}


OVERLAY_BYPASSES = {
    "disable_gate": "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: false}\n",
    "gate_to_none": "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {gate: none}\n",
    "dev_before_gate": "extends: default\ntracks:\n  full:\n    stages:\n      develop: {after: [requirements]}\n",
    "ux_before_gate": "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {after: [bootstrap]}\n",
    "gate_after_dev": "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {after: [develop]}\n"
                      "      ux-design: {after: [requirements]}\n",
    "add_pre_gate_dev": "extends: default\ntracks:\n  full:\n    add:\n      - {id: hotfix, agent: developer, after: [bootstrap]}\n",
    "add_pre_gate_other_agent": "extends: default\ntracks:\n  full:\n    add:\n      - {id: x, agent: devops, after: [bootstrap]}\n",
    "ba_runs_build": "extends: default\ntracks:\n  full:\n    stages:\n      requirements: {skill: bmad-build}\n",
    "quick_entry_open": "extends: default\ntracks:\n  quick:\n    entry_condition: {requirementsReady: false, status: [developing, developed, running, deployed]}\n",
    "quick_entry_removed": "extends: default\ntracks:\n  quick:\n    entry_condition: null\n",
    "other_produces_flag": "extends: default\ntracks:\n  full:\n    stages:\n      requirements: {produces: [requirementsReady]}\n",
    "readd_gate_id": "extends: default\ntracks:\n  full:\n    add:\n      - {id: requirements-review, agent: human, gate: human, produces: [requirementsReady], after: [bootstrap]}\n",
}


@pytest.mark.parametrize("name", sorted(OVERLAY_BYPASSES))
def test_overlay_cannot_bypass_gate(name: str) -> None:
    issues = merge_issues(OVERLAY_BYPASSES[name])
    assert codes(issues) & {"locked_gate_violation", "duplicate_stage"}, (name, issues)


@pytest.mark.parametrize("text", [
    "extends: default\ntracks:\n  full:\n    stages:\n      requirements-review: &g {enabled: false}\n      develop: *g\n",
    "extends: default\nextends: default\n",
    "extends: default\ntracks:\n  full:\n    stages:\n      requirements-reviеw: {enabled: false}\n",  # Cyrillic е
    "extends: default\ntracks:\n  full:\n    <<: {stages: {requirements-review: {enabled: false}}}\n",
    "%YAML 1.1\n---\nextends: default\ntracks:\n  full:\n    stages:\n      requirements-review: {enabled: no}\n",
])
def test_overlay_yaml_tricks_rejected(text: str) -> None:
    assert codes(merge_issues(text)) & {"yaml_invalid", "schema_invalid"}


def test_veda_default_file_overrides_packaged(veda_root: Path) -> None:
    seed_catalog(veda_root)
    paths = VedaPaths(veda_root)
    assert load_default(paths, CATALOG)[1] == "packaged"
    data = default_data()
    stage(data, "devops")["enabled"] = False
    (veda_root / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    flow, source = load_default(paths, CATALOG)
    assert source == ".veda/flow.default.yaml"
    assert {s.id: s for s in flow.tracks["full"].stages}["devops"].enabled is False


def test_invalid_veda_default_is_an_error_not_a_fallback(veda_root: Path) -> None:
    data = default_data()
    stage(data, "requirements-review")["enabled"] = False
    (veda_root / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    with pytest.raises(FlowInvalid) as info:
        effective_for(VedaPaths(veda_root), None, CATALOG)
    assert info.value.source == ".veda/flow.default.yaml"
    assert "locked_gate_violation" in codes(info.value.issues)


def test_effective_marks_overridden_stages(veda_root: Path) -> None:
    folder = veda_root / ".veda" / "projects" / "demo"
    folder.mkdir()
    (folder / "flow.yaml").write_text("extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n",
                                      encoding="utf-8")
    eff = effective_for(VedaPaths(veda_root), "demo", CATALOG)
    assert eff.overlay_source == ".veda/projects/demo/flow.yaml" and eff.overridden == {"full": {"ux-design"}}
