"""Story 5-2 — สถานะข้อเสนอ CLAUDE.md และข้อเสนอแก้ guard ของ agent (REQ-049, AD-9, AD-2).

ข้อเสนอแก้ agent อ่านอย่างเดียว: ไม่มี kind/route ใดเขียน ``.claude/agents`` (การบันทึก agent มาใน Epic 7).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bmad_studio.core import claude_md as cm
from bmad_studio.core.settings import Settings

from .flowkit import seed_catalog
from .test_changes_api import Studio
from .test_claude_md import VARIANTS

URL = "/api/claude-md/proposals"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def agents_dir(studio: Studio) -> Path:
    return studio.settings.veda_root / ".claude" / "agents"


@pytest.fixture
def studio(settings: Settings) -> Studio:
    seed_catalog(settings.veda_root)
    (settings.veda_root / "CLAUDE.md").write_bytes(VARIANTS["crlf"])
    folder = settings.veda_root / ".claude" / "agents"
    (folder / "developer.md").write_text(
        "---\nname: developer\ndescription: dev\n---\n\n## Gate\n"
        "- `requirementsReady != true` → หยุด\n"
        "- `hasFrontend == true` และ `designReady != true` → หยุด (ต้องผ่าน ux-designer)\n"
        "- **Full track**: `planReady != true` → หยุด\n", encoding="utf-8")
    (folder / "architect.md").write_text(
        "---\nname: architect\n---\r\n1. gate:\r\n   - `designReady != true` (ต้องผ่าน ux-designer ก่อน)\r\n", encoding="utf-8")
    (folder / "reviewer.md").write_text("---\nname: reviewer\n---\n- ไม่แตะ `designReady`\n- `planReady != true` → หยุด\n",
                                        encoding="utf-8")
    return Studio(settings)


def get(studio: Studio) -> dict:
    response = studio.client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def states(data: dict) -> dict[str, str]:
    return {b["block"]: b["state"] for b in data["blocks"]}


def test_requires_login(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    assert TestClient(create_app(settings)).get(URL).status_code == 401


def test_block_states_missing_applied_stale(studio: Studio) -> None:
    data = get(studio)
    assert data["exists"] and data["path"] == "CLAUDE.md" and data["issues"] == []
    assert states(data) == {cm.RESOLVER_BLOCK: "missing", cm.DISPATCH_BLOCK: "missing"}
    assert data["command"] == cm.RESOLVE_COMMAND
    for block in cm.BLOCKS:
        current = (studio.settings.veda_root / "CLAUDE.md").read_bytes()
        preview = studio.post("/api/changes/preview", {"kind": "claude-md", "target": "CLAUDE.md",
                                                       "payload": {"block": block}, "base_sha256": sha(current)})
        assert studio.commit(preview.json()["data"], typed="CLAUDE.md").status_code == 200
    data = get(studio)
    assert states(data) == {cm.RESOLVER_BLOCK: "applied", cm.DISPATCH_BLOCK: "applied"}
    assert data["base_sha256"] == sha((studio.settings.veda_root / "CLAUDE.md").read_bytes())
    # ทะเบียน agent เปลี่ยน → dispatch-table ล้าสมัย; resolver ยังใช้แล้ว
    (agents_dir(studio) / "newbie.md").write_text("---\nname: newbie\ndescription: ใหม่\n---\n", encoding="utf-8")
    assert states(get(studio)) == {cm.RESOLVER_BLOCK: "applied", cm.DISPATCH_BLOCK: "stale"}


def test_invalid_markers_and_missing_file(studio: Studio) -> None:
    target = studio.settings.veda_root / "CLAUDE.md"
    target.write_bytes(VARIANTS["lf"] + b"<!-- bmad-studio:dispatch-table:end -->\n")
    data = get(studio)
    assert set(states(data).values()) == {"invalid"} and data["issues"][0]["code"] == "claude_md_marker_unbalanced"
    target.unlink()
    data = get(studio)
    assert not data["exists"] and data["base_sha256"] is None and set(states(data).values()) == {"unavailable"}


def test_agent_guards_only_for_flow_disableable_flags(studio: Studio) -> None:
    before = {p.name: sha(p.read_bytes()) for p in agents_dir(studio).iterdir()}
    guards = {g["agent"]: g for g in get(studio)["agentGuards"]}
    # requirementsReady (human gate) และ planReady (plan gate ในโค้ด) ไม่ถูกเสนอให้ผ่อน; reviewer ไม่มี guard designReady
    assert set(guards) == {"architect", "developer"}
    dev = guards["developer"]
    assert dev["state"] == "suggested" and [g["line"] for g in dev["guards"]] == [8]
    assert dev["guards"][0]["flag"] == "designReady" and dev["guards"][0]["stage"] == "ux-design"
    added = [line["text"] for h in dev["diff"]["hunks"] for line in h["lines"] if line["type"] == "add"]
    assert len(added) == 1 and cm.RESOLVE_PREFIX + " <project> --json" in added[0] and "`ux-design`" in added[0]
    assert added[0].startswith("  - ") and ".exe" not in added[0]
    assert dev["diff"]["removed"] == 0
    arch = guards["architect"]
    arch_added = [line["text"] for h in arch["diff"]["hunks"] for line in h["lines"] if line["type"] == "add"]
    assert arch_added[0].startswith("     - ")  # เยื้องเป็นลูกของรายการเดิม
    after = {p.name: sha(p.read_bytes()) for p in agents_dir(studio).iterdir()}
    assert after == before  # GET ไม่เขียนไฟล์ agent


def test_agent_guard_already_referencing_resolver_is_applied(studio: Studio) -> None:
    path = agents_dir(studio) / "developer.md"
    text = path.read_text(encoding="utf-8")
    line = next(l for l in text.split("\n") if "designReady != true" in l)
    path.write_text(text.replace(line, line + "\n" + cm.guard_note("ux-design", "  ")), encoding="utf-8")
    dev = next(g for g in get(studio)["agentGuards"] if g["agent"] == "developer")
    assert dev["state"] == "applied" and dev["diff"] is None and dev["guards"][0]["applied"] is True


def test_guard_scan_skips_unreadable_and_huge_agents(studio: Studio) -> None:
    (agents_dir(studio) / "binary.md").write_bytes(b"---\nname: binary\n---\n\x80\x81 `designReady != true`\n")
    (agents_dir(studio) / "huge.md").write_bytes(b"---\nname: huge\n---\n" + b"- `designReady != true`\n" * (cm.MAX_AGENT_BYTES // 20))
    names = {g["agent"] for g in get(studio)["agentGuards"]}
    assert "binary" not in names and "huge" not in names


def test_no_write_path_into_agents(studio: Studio) -> None:
    """AD-2 อนุญาต .claude/agents แต่แอปยังไม่มี kind ที่เขียน agent (Epic 7) — มีแค่ revert ของรายการที่แอปเขียนเอง."""
    kinds = studio.app.state.writes.kinds
    writers = {name for name, handler in kinds.items() if "agent" in handler.rules}
    assert writers == {"revert"}  # text ปิดเป็นค่าเริ่มต้น
    response = studio.post("/api/changes/preview", {"kind": "agent-guard", "target": ".claude/agents/developer.md",
                                                    "payload": {}, "base_sha256": None})
    assert response.status_code == 422 and response.json()["code"] == "unknown_kind"
    response = studio.post("/api/changes/preview", {"kind": "claude-md", "target": ".claude/agents/developer.md",
                                                    "payload": {"block": cm.RESOLVER_BLOCK}, "base_sha256": None})
    assert response.status_code == 403


def test_injected_guard_text_is_bounded(studio: Studio) -> None:
    (agents_dir(studio) / "evil.md").write_text("---\nname: evil\n---\n- `designReady != true` " + "x" * 5000 + "\n",
                                               encoding="utf-8")
    evil = next(g for g in get(studio)["agentGuards"] if g["agent"] == "evil")
    assert len(evil["guards"][0]["text"]) <= 300


def test_agent_guards_match_thai_phrasing_and_numbered_items(studio: Studio) -> None:
    (agents_dir(studio) / "thai.md").write_text(
        "---\nname: thai\n---\n10. ถ้า `designReady` ไม่เป็น `true` ให้หยุด\n", encoding="utf-8")
    thai = next(g for g in get(studio)["agentGuards"] if g["agent"] == "thai")
    assert [(m["line"], m["flag"], m["stage"]) for m in thai["guards"]] == [(4, "designReady", "ux-design")]
    added = [line["text"] for h in thai["diff"]["hunks"] for line in h["lines"] if line["type"] == "add"]
    assert len(added) == 1 and added[0].startswith("    - ")  # ลูกของ "10. " เยื้อง 4


def test_guards_in_frontmatter_fence_and_table_are_ignored(studio: Studio) -> None:
    (agents_dir(studio) / "doc.md").write_text(
        "---\nname: doc\ndescription: ปฏิเสธถ้า designReady != true\n---\n"
        "```\n- `designReady != true`\n```\n| guard | `designReady != true` |\n", encoding="utf-8")
    assert "doc" not in {g["agent"] for g in get(studio)["agentGuards"]}


def test_applied_note_found_among_child_lines_even_after_repo_move(studio: Studio) -> None:
    path = agents_dir(studio) / "developer.md"
    text = path.read_text(encoding="utf-8")
    line = next(l for l in text.split("\n") if "designReady != true" in l)
    moved = "  - ต่อบรรทัด\n  - ยกเว้น: รัน `uv run --project D:/elsewhere python -m bmad_studio.cli flow resolve <p> --json`"
    path.write_text(text.replace(line, line + "\n" + moved), encoding="utf-8")
    dev = next(g for g in get(studio)["agentGuards"] if g["agent"] == "developer")
    assert dev["state"] == "applied"


def test_unreadable_claude_md_is_not_reported_as_missing(studio: Studio) -> None:
    target = studio.settings.veda_root / "CLAUDE.md"
    target.unlink()
    target.mkdir()
    data = get(studio)
    assert data["exists"] and set(states(data).values()) == {"invalid"} and data["issues"]
