"""Story 3.1 — validator (REQ-046 c) และ human gate ที่ล็อก (REQ-047 b/c, AD-8).

แต่ละกรณีแก้ default ทีละจุด แล้วตรวจว่าได้ error ที่ถูกต้องพร้อมขั้น — ห้ามมีทางให้ flow ใดผ่านโดยข้าม gate.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from bmad_studio.core.flow.validate import validate_data, validate_flow_bytes

from .flowkit import CATALOG, codes, default_data, dump, stage

Mutator = Callable[[dict[str, Any]], None]


def check(mutate: Mutator) -> list[dict[str, str]]:
    data = default_data()
    mutate(data)
    flow, issues = validate_data(data, CATALOG)
    assert flow is None and issues, "flow ที่ผิดกฎต้องไม่ผ่าน"
    # ผลเดียวกันเมื่อผ่าน YAML จริง (ทางที่ revert/resolver ใช้)
    assert validate_flow_bytes(dump(data), CATALOG)[1] == issues
    return issues


def locked(issues: list[dict[str, str]], stage_id: str | None = None) -> bool:
    return any(i["code"] == "locked_gate_violation" and (stage_id is None or i.get("stage") == stage_id) for i in issues)


def test_default_is_valid() -> None:
    flow, issues = validate_data(default_data(), CATALOG)
    assert issues == [] and flow is not None


# -- REQ-046 c: validation ทั่วไป ---------------------------------------------

def test_cycle_names_the_loop() -> None:
    issues = check(lambda d: stage(d, "bootstrap").update(after=["devops"]))
    cyc = [i for i in issues if i["code"] == "cycle"]
    assert cyc and "→" in cyc[0]["error"] and cyc[0]["track"] == "full"


def test_self_reference_is_a_cycle() -> None:
    issues = check(lambda d: stage(d, "develop").update(after=["architecture", "develop"]))
    assert any(i["code"] == "cycle" and i["stage"] == "develop" for i in issues)


def test_unknown_agent_and_skill() -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "devops").update(agent="no-such-agent")
        stage(d, "review").update(skill="no-such-skill")

    issues = check(mutate)
    assert {"stage": "devops", "code": "unknown_agent"}.items() <= next(i for i in issues if i["code"] == "unknown_agent").items()
    assert any(i["code"] == "unknown_skill" and i["stage"] == "review" for i in issues)
    assert all("ขั้น" in i["error"] for i in issues)  # ข้อความไทยระบุขั้น


def test_unknown_after_and_second_root_and_unreachable() -> None:
    assert "unknown_after" in codes(check(lambda d: stage(d, "devops").update(after=["nope"])))
    issues = check(lambda d: stage(d, "devops").update(after=[]))
    assert any(i["code"] == "no_predecessor" and i["stage"] == "devops" for i in issues)


def test_duplicate_stage_id() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"]["full"]["stages"].append(dict(stage(d, "devops")))

    assert "duplicate_stage" in codes(check(mutate))


def test_developer_before_plan_gate() -> None:
    issues = check(lambda d: stage(d, "develop").update(after=["ux-design"]))
    assert any(i["code"] == "plan_gate_missing" and i["stage"] == "develop" for i in issues)
    issues = check(lambda d: stage(d, "architecture").update(enabled=False))
    assert any(i["code"] == "plan_gate_missing" for i in issues)


def test_disabling_ux_is_allowed() -> None:
    """REQ-046 a: ปิด ux (ข้าม) ไม่ผิด validator — architect ยังสืบจาก gate ผ่านขั้นที่ปิด."""
    data = default_data()
    stage(data, "ux-design")["enabled"] = False
    assert validate_data(data, CATALOG)[1] == []


# -- REQ-047 b / AD-8: gate ที่ล็อก -------------------------------------------

GATE_MUTATIONS: dict[str, Mutator] = {
    "disable_gate": lambda d: stage(d, "requirements-review").update(enabled=False),
    "gate_none": lambda d: stage(d, "requirements-review").update(gate="none"),
    "gate_agent_confirm": lambda d: stage(d, "requirements-review").update(gate="agent-confirm"),
    "gate_optional": lambda d: stage(d, "requirements-review").update(optional=True),
    "gate_condition": lambda d: stage(d, "requirements-review").update(condition="hasFrontend"),
    "gate_agent_ba": lambda d: stage(d, "requirements-review").update(agent="ba"),
    "gate_skill": lambda d: stage(d, "requirements-review").update(skill="bmad-build"),
    "gate_produces_nothing": lambda d: stage(d, "requirements-review").update(produces=[]),
    "gate_produces_extra": lambda d: stage(d, "requirements-review").update(produces=["requirementsReady", "planReady"]),
    "gate_moved_first": lambda d: (stage(d, "requirements-review").update(after=["bootstrap"]),
                                   stage(d, "requirements").update(after=["requirements-review"])),
}


@pytest.mark.parametrize("name", sorted(GATE_MUTATIONS))
def test_gate_fields_are_locked(name: str) -> None:
    assert locked(check(GATE_MUTATIONS[name]), "requirements-review"), name


def test_removing_the_gate_is_locked() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"]["full"]["stages"] = [s for s in d["tracks"]["full"]["stages"] if s["id"] != "requirements-review"]
        stage(d, "ux-design")["after"] = ["requirements"]

    issues = check(mutate)
    assert locked(issues, "requirements-review")
    assert {"ux-design", "architecture", "develop", "review", "retrospective"} <= {
        i.get("stage") for i in issues if i["code"] == "locked_gate_violation"}


def test_renamed_gate_id_is_locked() -> None:
    """เปลี่ยน id ของ gate (เช่นเป็น human-review) = ไม่มีขั้นล็อก + ขั้นถัดไปไม่สืบจาก gate."""
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "requirements-review")["id"] = "human-review"
        stage(d, "ux-design")["after"] = ["human-review"]

    assert locked(check(mutate), "requirements-review")


@pytest.mark.parametrize("stage_id", ["ux-design", "architecture", "develop", "review", "retrospective"])
def test_gated_stage_moved_before_gate(stage_id: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, stage_id)["after"] = ["requirements"]

    assert locked(check(mutate), stage_id)


def test_moving_gate_after_developer_is_locked() -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "requirements-review")["after"] = ["develop"]
        stage(d, "ux-design")["after"] = ["requirements"]

    issues = check(mutate)
    assert locked(issues, "develop") and locked(issues, "ux-design")


@pytest.mark.parametrize("agent", ["ux-designer", "architect", "developer", "reviewer"])
def test_new_stage_with_gated_agent_cannot_bypass(agent: str) -> None:
    """ตรวจตาม agent ไม่ใช่ id: ขั้นใหม่ชื่ออื่นที่ใช้ agent ต้องห้ามแต่มาก่อน gate = ล็อก."""
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"]["full"]["stages"].append({"id": "sneaky", "agent": agent, "after": ["bootstrap"]})

    assert locked(check(mutate), "sneaky")


@pytest.mark.parametrize("new_stage", [
    {"id": "scout", "agent": "skill-scout", "after": ["bootstrap"]},          # agent อื่นที่ไม่อยู่ในรายการ AD-8
    {"id": "early-ops", "agent": "devops", "after": ["requirements"]},
    {"id": "sneaky", "agent": "ba", "skill": "bmad-build", "after": ["bootstrap"]},  # agent ที่อนุญาต + skill ลงมือ
    {"id": "sneaky-arch", "agent": "project-bootstrapper", "skill": "bmad-architecture", "after": ["bootstrap"]},
])
def test_pre_gate_is_an_allowlist(new_stage: dict) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"]["full"]["stages"].append(dict(new_stage))

    assert locked(check(mutate), new_stage["id"])


def test_ba_before_gate_with_non_build_skill_is_allowed() -> None:
    data = default_data()
    stage(data, "requirements")["skill"] = "bmad-sprint-planning"  # อยู่ใน catalog แต่เป็น gated skill
    assert locked(validate_data(data, CATALOG)[1], "requirements")
    data = default_data()
    data["tracks"]["full"]["stages"].append({"id": "notes", "agent": "ba", "after": ["requirements"]})
    assert validate_data(data, CATALOG)[1] == []


@pytest.mark.parametrize("change", [{"enabled": False}, {"condition": "hasFrontend"}])
def test_gate_needs_an_active_ba_before_it(change: dict) -> None:
    assert locked(check(lambda d: stage(d, "requirements").update(change)), "requirements-review")


def test_disabled_gated_stage_before_gate_is_allowed() -> None:
    """ขั้นที่ปิดไม่ถูก dispatch — ไม่ใช่ทางข้าม gate."""
    data = default_data()
    data["tracks"]["full"]["stages"].append({"id": "parked", "agent": "developer", "enabled": False, "after": ["bootstrap"]})
    assert "locked_gate_violation" not in codes(validate_data(data, CATALOG)[1])


def test_other_stage_cannot_produce_requirements_ready() -> None:
    assert locked(check(lambda d: stage(d, "requirements").update(produces=["requirementsReady"])), "requirements")


def test_human_agent_only_for_human_gate() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"]["full"]["stages"].append({"id": "fake", "agent": "human", "after": ["devops"]})

    assert locked(check(mutate), "fake")


def test_second_human_gate_does_not_replace_the_locked_one() -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "requirements-review")["enabled"] = False
        d["tracks"]["full"]["stages"].append({"id": "other-review", "agent": "human", "gate": "human",
                                              "produces": ["requirementsReady"], "after": ["requirements"]})
        stage(d, "ux-design")["after"] = ["other-review"]

    assert locked(check(mutate))


QUICK_MUTATIONS: dict[str, Mutator] = {
    "remove": lambda d: d["tracks"]["quick"].pop("entry_condition"),
    "false": lambda d: d["tracks"]["quick"]["entry_condition"].update(requirementsReady=False),
    "status_widened": lambda d: d["tracks"]["quick"]["entry_condition"]["status"].append("planned"),
    "status_narrowed": lambda d: d["tracks"]["quick"]["entry_condition"].update(status=["running"]),
    "status_reordered": lambda d: d["tracks"]["quick"]["entry_condition"]["status"].reverse(),
    "track_removed": lambda d: d["tracks"].pop("quick"),
    "new_track_without_entry": lambda d: d["tracks"].update(
        hotfix={"stages": [{"id": "develop", "agent": "developer"}]}),
    "full_gets_entry_condition": lambda d: d["tracks"]["full"].update(
        entry_condition={"requirementsReady": True, "status": ["developing", "developed", "running", "deployed"]}),
    "full_removed": lambda d: d["tracks"].pop("full"),
}


@pytest.mark.parametrize("name", sorted(QUICK_MUTATIONS))
def test_quick_entry_condition_and_tracks_are_locked(name: str) -> None:
    assert locked(check(QUICK_MUTATIONS[name])), name


def test_new_track_with_locked_entry_is_allowed() -> None:
    data = default_data()
    data["tracks"]["hotfix"] = {"entry_condition": default_data()["tracks"]["quick"]["entry_condition"],
                                "stages": [{"id": "develop", "agent": "developer"}]}
    assert validate_data(data, CATALOG)[1] == []


def test_lookalike_and_case_variant_ids_are_schema_errors() -> None:
    for mutate in (
        lambda d: stage(d, "requirements-review").update(id="requirements-reviеw"),  # Cyrillic е
        lambda d: stage(d, "develop").update(agent="Developer"),
        lambda d: stage(d, "develop").update(agent="devеloper"),
        lambda d: stage(d, "develop").update(agent="developer\n"),
        lambda d: stage(d, "develop").update(agent="ｄeveloper"),  # fullwidth
        lambda d: stage(d, "develop").update(enabled="yes"),
        lambda d: d.update(include="other.yaml"),
        lambda d: stage(d, "develop").update(extends="x"),
        lambda d: d.update(version=2),
    ):
        issues = check(mutate)
        assert codes(issues) == {"schema_invalid"}, issues


def test_issue_for_every_problem_not_only_the_first() -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "requirements-review")["enabled"] = False
        stage(d, "devops")["agent"] = "ghost"

    assert {"locked_gate_violation", "unknown_agent"} <= codes(check(mutate))


# -- rework round 1 (review 3-1 B1): plan gate นับเฉพาะขั้นวางแผนที่เปิดและไม่มี condition, ตรวจตาม skill ด้วย --

def test_plan_producer_with_condition_does_not_count() -> None:
    issues = check(lambda d: stage(d, "architecture").update(condition="hasFrontend"))
    assert any(i["code"] == "plan_gate_missing" and i["stage"] == "develop" for i in issues)


@pytest.mark.parametrize("skill", ["bmad-build", "bmad-build-auto", "bmad-agent-dev", "bmad-qa-generate-e2e-tests"])
def test_build_skill_on_any_agent_needs_plan(skill: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        stage(d, "architecture")["enabled"] = False
        stage(d, "develop")["enabled"] = False
        d["tracks"]["full"]["stages"].append({"id": "sneaky-build", "agent": "reviewer", "skill": skill,
                                              "after": ["ux-design"]})

    issues = check(mutate)
    assert any(i["code"] == "plan_gate_missing" and i["stage"] == "sneaky-build" for i in issues)


def test_plan_and_build_with_the_same_condition_is_allowed() -> None:
    data = default_data()
    stage(data, "architecture")["condition"] = "designReady"
    stage(data, "develop")["condition"] = "designReady"
    assert "plan_gate_missing" not in codes(validate_data(data, CATALOG)[1])
    stage(data, "develop")["condition"] = "hasFrontend"  # condition ต่างกัน = ข้ามแผนได้โดยยังสร้าง
    assert "plan_gate_missing" in codes(validate_data(data, CATALOG)[1])


def test_build_skill_after_plan_is_allowed() -> None:
    data = default_data()
    data["tracks"]["full"]["stages"].append({"id": "extra-build", "agent": "reviewer", "skill": "bmad-build",
                                             "after": ["architecture"]})
    assert validate_data(data, CATALOG)[1] == []
