"""Story 3.1 — schema, เอกสาร และ default template (REQ-045 a/b, AD-7)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bmad_studio.cli.main import main as cli_main
from bmad_studio.core.flow.schema import Flow, Overlay, Stage, json_schema, json_schema_text
from bmad_studio.core.flow.validate import DEFAULT_TEMPLATE, Catalog, load_packaged_default, validate_flow_bytes
from bmad_studio.core.paths import VedaPaths

from .flowkit import CATALOG, seed_catalog

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "flow.md"
REAL_CLAUDE_MD = Path("C:/Users/thedu/code/veda/CLAUDE.md")


def _doc_table() -> list[dict[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Default flow", 1)[1]
    rows = [line for line in section.splitlines() if line.startswith("| full |") or line.startswith("| quick |")]
    keys = ["track", "id", "agent", "skill", "after", "gate", "produces", "condition", "enabled", "optional"]
    return [dict(zip(keys, [cell.strip() for cell in row.strip("|").split("|")])) for row in rows]


def _cell(value: object) -> str:
    if value is None or value == []:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def test_default_template_matches_doc_table_row_by_row() -> None:
    flow = load_packaged_default()
    table = _doc_table()
    actual = [
        {"track": name, "id": s.id, "agent": s.agent, "skill": _cell(s.skill), "after": _cell(s.after), "gate": s.gate,
         "produces": _cell(s.produces), "condition": _cell(s.condition), "enabled": _cell(s.enabled),
         "optional": _cell(s.optional)}
        for name, track in flow.tracks.items() for s in track.stages
    ]
    assert len(table) == 11
    assert actual == table


def test_default_full_order_follows_claude_md_pipeline() -> None:
    flow = load_packaged_default()
    agents = [s.agent for s in flow.tracks["full"].stages]
    assert agents == ["project-bootstrapper", "ba", "human", "ux-designer", "architect", "developer", "reviewer",
                      "reviewer", "devops"]
    by_id = {s.id: s for s in flow.tracks["full"].stages}
    assert by_id["ux-design"].condition == "hasFrontend"
    assert by_id["devops"].enabled and by_id["devops"].optional
    assert flow.tracks["quick"].entry_condition.model_dump() == {
        "requirementsReady": True, "status": ["developing", "developed", "running", "deployed"]}


@pytest.mark.skipif(not REAL_CLAUDE_MD.is_file(), reason="ไม่มี veda จริงบนเครื่องนี้")
def test_default_agents_appear_in_real_claude_md_in_the_same_order() -> None:
    """อ่าน CLAUDE.md ของ veda จริงแบบอ่านอย่างเดียว: บรรทัด 'ลำดับวงจรพัฒนา (full track)'."""
    text = REAL_CLAUDE_MD.read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "ลำดับวงจรพัฒนา" in line)
    order = ["project-bootstrapper", "ba", "/review", "ux-designer", "architect", "developer", "reviewer", "retro", "devops"]
    positions = [line.find(token) for token in order]
    assert all(p >= 0 for p in positions), dict(zip(order, positions))
    assert positions == sorted(positions)
    assert "developing/developed/running/deployed" in text  # เงื่อนไขเข้า quick track


def test_default_passes_validator_with_catalog(veda_root: Path) -> None:
    seed_catalog(veda_root)
    flow, issues = validate_flow_bytes(DEFAULT_TEMPLATE.read_bytes(), Catalog.scan(VedaPaths(veda_root)))
    assert issues == [] and flow is not None


def test_schema_export_is_up_to_date() -> None:
    assert (REPO / "docs" / "flow-schema.json").read_text(encoding="utf-8") == json_schema_text()


def test_cli_flow_schema_writes_same_schema(tmp_path: Path) -> None:
    out = tmp_path / "s.json"
    assert cli_main(["flow", "schema", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == json_schema()


def test_doc_describes_every_field() -> None:
    text = DOC.read_text(encoding="utf-8")
    for name in Stage.model_fields:
        assert f"`stages[].{name}`" in text, name
    for name in Flow.model_fields:
        assert f"`{name}`" in text, name
    assert "`tracks.<t>.entry_condition`" in text
    for name in Overlay.model_fields:
        assert f"`{name}`" in text
    for code in ("locked_gate_violation", "plan_gate_missing", "cycle", "unknown_agent", "unknown_skill",
                 "no_predecessor", "yaml_invalid", "schema_invalid"):
        assert f"`{code}`" in text, code


def test_json_schema_rejects_unknown_keys() -> None:
    schema = json_schema()
    assert schema["$defs"]["Stage"]["additionalProperties"] is False
    assert schema["$defs"]["Flow"]["additionalProperties"] is False
    assert re.fullmatch(schema["$defs"]["Stage"]["properties"]["id"]["pattern"], "requirements-review")

    def maps(node: object) -> list[dict]:
        found = []
        if isinstance(node, dict):
            if "patternProperties" in node:
                found.append(node)
            for value in node.values():
                found.extend(maps(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(maps(value))
        return found

    keyed = maps(schema)
    assert len(keyed) >= 3  # Flow.tracks, Overlay.tracks, TrackOverlay.stages
    assert all(node.get("additionalProperties") is False for node in keyed)


def test_schema_issues_report_how_many_were_omitted() -> None:
    from bmad_studio.core.flow.validate import validate_data

    data = {"version": 1, "tracks": {"full": {"stages": [{"id": f"s{i}", "agent": "X", "bogus": 1}
                                                         for i in range(40)]}}}
    _flow, issues = validate_data(data)
    assert len(issues) == 51 and "อีก" in issues[-1]["error"]


def test_cli_schema_stdout_is_utf8_even_on_cp1252_console() -> None:
    import io
    import sys

    real = sys.stdout
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252")
    sys.stdout = wrapper
    try:
        assert cli_main(["flow", "schema"]) == 0
        wrapper.flush()
    finally:
        sys.stdout = real
        wrapper.detach()
    assert json.loads(raw.getvalue().decode("utf-8")) == json_schema()


def test_catalog_scan_is_exact_case(veda_root: Path) -> None:
    (veda_root / ".claude" / "agents" / "Developer.md").write_text("x", encoding="utf-8")
    (veda_root / ".claude" / "skills" / "no-skill-md").mkdir()
    catalog = Catalog.scan(VedaPaths(veda_root))
    assert "developer" not in catalog.agents and "Developer" in catalog.agents
    assert "no-skill-md" not in catalog.skills
    assert CATALOG.agents >= {"developer", "reviewer"}
