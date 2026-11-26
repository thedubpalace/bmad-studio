"""Story 5-1 — marker-block engine และข้อเสนอแก้ CLAUDE.md (REQ-049, AD-9, AD-5).

ทุก test ใช้ tmp veda (fixture ``settings``) — ไม่มี test ใดอ่าน/เขียน CLAUDE.md จริง.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.core import claude_md as cm
from bmad_studio.core.settings import Settings

from .flowkit import seed_catalog
from .test_changes_api import Studio
from .test_flow_resolve import project_dir, registry

R, D = cm.RESOLVER_BLOCK, cm.DISPATCH_BLOCK
STANDARD = "uv run --project C:/Users/thedu/code/bmad-studio python -m bmad_studio.cli flow resolve <project> [--json]"

SAMPLE = """# Veda — ศูนย์ควบคุม

ข้อความของคน ห้ามแตะ   (มีช่องว่างท้าย)
## Subagent ที่มี (`.claude/agents/`)
| งาน | agent |
|---|---|
| a | **ba** |

## กติกาการ dispatch
1. ส่ง path เสมอ

```text
<!-- bmad-studio:flow-resolver-rule:start -->
เอกสารใน code fence ไม่ใช่ marker
<!-- bmad-studio:flow-resolver-rule:end -->
```

## State
- ท้ายไฟล์
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lines(text: str = "x") -> list[str]:
    return ["", text, ""]


def apply(raw: bytes, block: str = D, content: list[str] | None = None) -> bytes:
    return cm.apply_block(raw, block, content or lines())


def codes(items: list[dict[str, Any]]) -> set[str]:
    return {i["code"] for i in items}


# -- engine: bytes นอก marker เท่าเดิมทุกกรณี (REQ-049 a) ---------------------------------------------

VARIANTS = {
    "lf": SAMPLE.encode(),
    "crlf": SAMPLE.replace("\n", "\r\n").encode(),
    "bom": b"\xef\xbb\xbf" + SAMPLE.encode(),
    "bom-crlf": b"\xef\xbb\xbf" + SAMPLE.replace("\n", "\r\n").encode(),
    "no-final-eol": SAMPLE.rstrip("\n").encode(),
    "crlf-no-final-eol": SAMPLE.rstrip("\n").replace("\n", "\r\n").encode(),
    "mixed-eol": SAMPLE.replace("\n", "\r\n", 3).encode(),
    "no-anchor": b"# only\r\nline\r\n",
    "no-anchor-no-eol": b"# only\nline",
    "empty": b"",
    "bom-only": b"\xef\xbb\xbf",
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("block", [R, D])
def test_first_insert_then_replace_keeps_outside_bytes(name: str, block: str) -> None:
    raw = VARIANTS[name]
    first = apply(raw, block, lines("one"))
    assert cm.outside(cm.parse(first)) == raw and cm.validate_change(raw, first) == []
    assert cm.parse(first).issues == [] and cm.block_state(first, block, lines("one")) == "applied"
    second = apply(first, block, lines("two | <b>"))
    assert cm.outside(cm.parse(second)) == raw and cm.validate_change(first, second) == []
    assert cm.block_state(second, block, lines("one")) == "stale"
    assert apply(second, block, lines("two | <b>")) == second  # ไม่มีอะไรเปลี่ยน
    both = apply(second, R if block == D else D, lines("other"))
    assert cm.outside(cm.parse(both)) == raw and set(cm.parse(both).blocks) == {R, D}


def test_eol_of_block_follows_file() -> None:
    crlf = VARIANTS["crlf"]
    out = apply(crlf, D)
    start = out.index(cm.marker(D, "start"))
    end = out.index(cm.marker(D, "end")) + len(cm.marker(D, "end"))
    assert b"\n" not in out[start:end].replace(b"\r\n", b"")
    assert out[end:end + 2] == b"\r\n"


def test_first_insert_lands_at_end_of_anchor_section() -> None:
    out = apply(VARIANTS["lf"], D).decode()
    assert out.index("<!-- bmad-studio:dispatch-table:end -->") < out.index("## กติกาการ dispatch")
    assert out.index("| a | **ba** |") < out.index("<!-- bmad-studio:dispatch-table:start -->")
    out = apply(VARIANTS["lf"], R).decode()
    rule_end = out.rindex("<!-- bmad-studio:flow-resolver-rule:end -->")  # ตัวแรกอยู่ใน code fence
    assert out.index("```\n") < rule_end < out.index("## State")


def test_marker_inside_code_fence_is_text() -> None:
    parsed = cm.parse(VARIANTS["lf"])
    assert parsed.issues == [] and parsed.blocks == {}
    out = apply(VARIANTS["lf"], R)
    assert out.count(cm.marker(R, "start")) == 2 and cm.outside(cm.parse(out)) == VARIANTS["lf"]
    tilde = b"~~~~\n<!-- bmad-studio:dispatch-table:start -->\n~~~\nstill fenced\n~~~~\n"
    assert cm.parse(tilde).blocks == {} and cm.parse(tilde).issues == []


START, END = "<!-- bmad-studio:{b}:start -->", "<!-- bmad-studio:{b}:end -->"


def m(edge: str, block: str = D) -> str:
    return (START if edge == "start" else END).format(b=block)


MALFORMED = {
    "missing-end": [m("start"), "x"],
    "missing-start": ["x", m("end")],
    "duplicate": [m("start"), m("end"), m("start"), m("end")],
    "nested": [m("start", R), m("start"), m("end"), m("end", R)],
    "reordered": [m("end"), "x", m("start")],
    "crossed": [m("start", R), m("start", D), m("end", R), m("end", D)],
    "unknown-block": ["<!-- bmad-studio:other:start -->", "<!-- bmad-studio:other:end -->"],
    "trailing-space": [m("start") + " ", m("end")],
    "leading-space": [" " + m("start"), m("end")],
    "fullwidth": [m("start").replace("<", "＜"), m("end")],
    "cyrillic-o": [m("start").replace("studio", "studiо"), m("end")],
    "zero-width": [m("start").replace("bmad", "bm\u200bad"), m("end")],
    "case": [m("start").replace("bmad-studio", "BMAD-Studio"), m("end")],
    "double-space": [m("start").replace("<!-- ", "<!--  "), m("end")],
    "nbsp": [m("start").replace(" ", "\u00a0", 1), m("end")],
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_markers_block_editing(name: str) -> None:
    raw = ("# t\n" + "\n".join(MALFORMED[name]) + "\n").encode()
    parsed = cm.parse(raw)
    assert parsed.issues, name
    assert all(i["code"].startswith("claude_md_marker_") for i in parsed.issues)
    with pytest.raises(Exception):
        apply(raw, D)
    assert cm.validate_change(raw, raw)  # ไฟล์ปัจจุบันผิดรูป = แก้ผ่านแอปไม่ได้ (fail closed)
    assert cm.block_state(raw, D, lines()) == "invalid"


def test_comment_about_bmad_studio_is_not_a_lookalike() -> None:
    raw = "# t\n<!-- TODO: bmad-studio integration -->\n<!-- bmad-studio notes -->\n".encode()
    assert cm.parse(raw).issues == []
    assert codes(cm.parse("<!-- bmad studio : dispatch-table : start -->\n".encode()).issues) == {
        "claude_md_marker_lookalike"}


def test_backtick_info_string_with_backtick_is_not_a_fence() -> None:
    raw = ("```py` x\n" + m("start") + "\nx\n" + m("end") + "\n").encode()
    parsed = cm.parse(raw)
    assert parsed.issues == [] and set(parsed.blocks) == {D}


def test_unclosed_fence_is_reported() -> None:
    raw = ("# t\n```\n" + m("start") + "\nx\n" + m("end") + "\n").encode()
    assert codes(cm.parse(raw).issues) == {"claude_md_unclosed_fence"}
    assert codes(cm.validate_change(raw, raw)) == {"claude_md_unclosed_fence"}


def test_eol_owned_by_trailing_block_cannot_flip() -> None:
    raw = b"a\nx"
    first = apply(raw, D)  # block ท้ายไฟล์ไม่มี EOL ปิดท้าย
    assert first.startswith(b"a\nx\n<!--") and cm.validate_change(raw, first) == []
    crlf_first = apply(b"a\r\nx", D)
    flipped = crlf_first.replace(b"x\r\n<!--", b"x\n<!--")
    assert codes(cm.validate_change(crlf_first, flipped)) == {"claude_md_outside_changed"}
    assert cm.validate_change(first, raw) == []  # คืนค่าการแทรกครั้งแรก


def test_many_issues_are_capped_with_a_note() -> None:
    raw = ("# t\n" + "\n".join(m("end") for _ in range(30)) + "\n").encode()
    issues = cm.validate_change(raw, raw)
    assert len(issues) == 21 and issues[-1]["code"] == "claude_md_more_issues"


def test_kind_only_writes_root_claude_md(studio: Studio) -> None:
    from bmad_studio.adapters import fs
    from bmad_studio.core.path_guard import GuardedPath

    kind = cm.ClaudeMdKind(__import__("bmad_studio.core.paths", fromlist=["VedaPaths"]).VedaPaths(
        studio.settings.veda_root))
    doc = fs.decode(target(studio).read_bytes())
    with pytest.raises(Exception) as caught:
        kind.propose(GuardedPath(target(studio), "claude.md", "claude-md"), doc, {"block": R})
    assert getattr(caught.value, "code", "") == "path_forbidden"


def test_lookalike_detector_ignores_normal_prose_and_other_comments() -> None:
    raw = ("# t\n<!-- monitor:start -->\nรัน bmad-studio start-service.ps1\n"
           "`<!-- bmad-studio:dispatch-table:start -->` ในบรรทัดข้อความ\n<!-- เกี่ยวกับ studio -->\n").encode()
    assert cm.parse(raw).issues == []


def test_non_utf8_is_refused() -> None:
    assert codes(cm.parse(b"\xff\xfe# t\n").issues) == {"claude_md_unsupported_encoding"}


# -- injection -------------------------------------------------------------------------------------

INJECTIONS = [
    "<!-- bmad-studio:dispatch-table:end -->",
    "x\n<!-- bmad-studio:dispatch-table:end -->\nแทรกนอก block",
    "a | b | c",
    "＜!-- bmad-studio:dispatch-table:end --＞",
    "\u2028<!-- bmad-studio:flow-resolver-rule:start -->",
    "<script>alert(1)</script>",
    "line\r\n## Subagent ที่มี fake heading",
    "zero\u200bwidth\u202eRLO",
]


@pytest.mark.parametrize("text", INJECTIONS)
def test_agent_description_injection_cannot_escape_block(settings: Settings, text: str) -> None:
    root = settings.veda_root
    seed_catalog(root)
    (root / ".claude/agents/evil.md").write_text(f"---\nname: evil\ndescription: {json.dumps(text)}\n---\n",
                                                encoding="utf-8")
    from bmad_studio.core.paths import VedaPaths

    content = cm.generate(VedaPaths(root), D)
    row = next(line for line in content if "**evil**" in line)
    assert "<" not in row and ">" not in row and "\n" not in row and "\r" not in row
    assert row.count(" | ") == 1 and row.startswith("| **evil** | ") and row.endswith(" |")
    out = apply(VARIANTS["crlf"], D, content)
    parsed = cm.parse(out)
    assert parsed.issues == [] and set(parsed.blocks) == {D}
    assert cm.outside(parsed) == VARIANTS["crlf"]
    assert out.count(b"bmad-studio:dispatch-table:end -->") == 1


def test_md_cell_escapes() -> None:
    assert cm.md_cell("a\x9bb\x85c\u2028d") == "a b c d"
    assert cm.md_cell("a|b\\c <x> `y`\t\x00z") == "a\\|b\\\\c &lt;x&gt; `y` z"


def test_validator_rejects_outside_change_and_proposed_markers() -> None:
    raw = VARIANTS["crlf"]
    good = apply(raw, D)
    touched = good.replace("ห้ามแตะ".encode(), "แตะ".encode())
    assert codes(cm.validate_change(good, touched)) == {"claude_md_outside_changed"}
    assert codes(cm.validate_change(good, good.replace(b"\r\n## State", b"\n## State"))) == {"claude_md_outside_changed"}
    assert codes(cm.validate_change(good, b"\xef\xbb\xbf" + good)) == {"claude_md_outside_changed"}
    assert codes(cm.validate_change(good, good + b" ")) == {"claude_md_outside_changed"}
    broken = good.replace(cm.marker(D, "end"), b"")
    assert codes(cm.validate_change(good, broken)) == {"claude_md_marker_unbalanced"}
    # block ที่ "ปิดก่อนเวลา" แล้วเขียนต่อนอก block → outside เปลี่ยน
    end = cm.marker(D, "end") + b"\r\n"
    early = good.replace(end, end + "แทรกนอก block\r\n".encode(), 1)
    assert codes(cm.validate_change(good, early)) == {"claude_md_outside_changed"}
    # ปิดก่อนเวลาแล้วเปิด block ใหม่ซ้ำเพื่อ "ซ่อน" ข้อความ → duplicate
    reopen = good.replace(end, end + "แทรก\r\n".encode() + cm.marker(D, "start") + b"\r\n" + end, 1)
    assert "claude_md_marker_duplicate" in codes(cm.validate_change(good, reopen))
    assert codes(cm.validate_change(None, good)) == {"claude_md_missing"}


# -- kind claude-md ผ่าน pipeline เท่านั้น (REQ-049 b) ------------------------------------------------

@pytest.fixture
def studio(settings: Settings) -> Studio:
    seed_catalog(settings.veda_root)
    (settings.veda_root / "CLAUDE.md").write_bytes(VARIANTS["crlf"])
    return Studio(settings)


def preview(studio: Studio, block: str, base: str | None, **payload: Any) -> Any:
    return studio.post("/api/changes/preview", {"kind": "claude-md", "target": "CLAUDE.md",
                                                "payload": {"block": block, **payload}, "base_sha256": base})


def target(studio: Studio) -> Path:
    return studio.settings.veda_root / "CLAUDE.md"


def test_preview_is_high_impact_and_commit_needs_typed_name(studio: Studio) -> None:
    before = target(studio).read_bytes()
    response = preview(studio, R, sha(before))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["impact"] == "high" and data["confirm_phrase"] == "CLAUDE.md" and data["errors"] == []
    assert data["op"] == "modify" and data["diff"]["removed"] == 0
    assert studio.commit(data).status_code == 400 and target(studio).read_bytes() == before
    committed = studio.commit(data, typed="CLAUDE.md")
    assert committed.status_code == 200, committed.text
    after = target(studio).read_bytes()
    assert cm.outside(cm.parse(after)) == before
    backup = studio.settings.runtime_dir / "backups" / committed.json()["data"]["change_id"]
    assert (backup / "original.bin").read_bytes() == before
    again = preview(studio, R, sha(after)).json()["data"]
    assert codes(again["errors"]) == {"no_change"}


def test_payload_text_is_refused(studio: Studio) -> None:
    before = target(studio).read_bytes()
    for payload in ({"block": R, "text": "evil"}, {"block": "other"}, {"block": None}, {}):
        response = studio.post("/api/changes/preview", {"kind": "claude-md", "target": "CLAUDE.md",
                                                        "payload": payload, "base_sha256": sha(before)})
        assert response.status_code == 422, payload
    for other in ("AGENTS.md", ".claude/agents/ba.md", "claude.md/../CLAUDE.md", "sub/CLAUDE.md"):
        response = studio.post("/api/changes/preview", {"kind": "claude-md", "target": other,
                                                        "payload": {"block": R}, "base_sha256": None})
        assert response.status_code in (403, 409, 422), other
    text_kind = studio.post("/api/changes/preview", {"kind": "text", "target": "CLAUDE.md",
                                                     "payload": {"text": "x"}, "base_sha256": sha(before)})
    # kind text ไม่มี rule claude-md เลย (ไม่ใช่แค่ไม่ผ่าน validator) — เขียนข้อความเข้า block ตรง ๆ ไม่ได้
    assert text_kind.status_code == 422 and text_kind.json()["code"] == "unknown_kind"  # ปิดเป็นค่าเริ่มต้น
    from bmad_studio.core.writes import RevertKind, TextKind

    assert "claude-md" not in TextKind.rules and "claude-md" in RevertKind.rules  # เปิดแล้วก็ได้ 403 kind_rule


def test_missing_claude_md_is_not_created(studio: Studio) -> None:
    target(studio).unlink()
    response = preview(studio, R, None)
    assert response.status_code == 422 and response.json()["code"] == "claude_md_missing"
    assert not target(studio).exists()


def test_malformed_current_file_gives_errors_and_cannot_commit(studio: Studio) -> None:
    raw = VARIANTS["lf"] + b"<!-- bmad-studio:dispatch-table:start -->\n"
    target(studio).write_bytes(raw)
    data = preview(studio, D, sha(raw)).json()["data"]
    assert "claude_md_marker_unbalanced" in codes(data["errors"])
    assert studio.commit(data, typed="CLAUDE.md").status_code == 422 and target(studio).read_bytes() == raw


def test_commit_revalidates_under_lock_when_file_changes(studio: Studio) -> None:
    before = target(studio).read_bytes()
    data = preview(studio, D, sha(before)).json()["data"]
    target(studio).write_bytes(before + b"hand edit\r\n")
    assert studio.commit(data, typed="CLAUDE.md").status_code == 409


# -- revert ต้องผ่าน validator ของ claude-md (review epic 2: N-revert-validator) ------------------------

def test_revert_that_only_touches_blocks_passes(studio: Studio) -> None:
    before = target(studio).read_bytes()
    first = studio.commit(preview(studio, D, sha(before)).json()["data"], typed="CLAUDE.md").json()["data"]
    after = target(studio).read_bytes()
    revert = studio.post("/api/changes/preview", {"kind": "revert", "target": "CLAUDE.md",
                                                  "payload": {"change_id": first["change_id"]},
                                                  "base_sha256": sha(after)}).json()["data"]
    assert revert["errors"] == [] and revert["impact"] == "high"
    assert studio.commit(revert, typed="CLAUDE.md").status_code == 200
    assert target(studio).read_bytes() == before


def test_revert_that_would_change_outside_text_is_blocked(studio: Studio) -> None:
    before = target(studio).read_bytes()
    first = studio.commit(preview(studio, D, sha(before)).json()["data"], typed="CLAUDE.md").json()["data"]
    edited = target(studio).read_bytes().replace("ห้ามแตะ".encode(), "คนแก้ต่อ".encode())
    target(studio).write_bytes(edited)  # คนแก้ข้อความนอก marker เองหลังรายการนั้น
    revert = studio.post("/api/changes/preview", {"kind": "revert", "target": "CLAUDE.md",
                                                  "payload": {"change_id": first["change_id"]},
                                                  "base_sha256": sha(edited)}).json()["data"]
    assert "claude_md_outside_changed" in codes(revert["errors"])
    assert studio.commit(revert, typed="CLAUDE.md").status_code == 422
    assert target(studio).read_bytes() == edited


def test_revert_of_hand_tampered_backup_is_blocked(studio: Studio) -> None:
    """backup ถูกแก้บนดิสก์ (และ meta ถูกแก้ให้ sha ตรง) — validator ยังจับข้อความนอก marker ที่เปลี่ยน."""
    before = target(studio).read_bytes()
    first = studio.commit(preview(studio, D, sha(before)).json()["data"], typed="CLAUDE.md").json()["data"]
    after = target(studio).read_bytes()
    folder = studio.settings.runtime_dir / "backups" / first["change_id"]
    tampered = before.replace("ห้ามแตะ".encode(), "ถูกแทรก".encode())
    (folder / "original.bin").write_bytes(tampered)
    meta_path = next(p for p in folder.iterdir() if p.suffix == ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = sha(tampered)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    response = studio.post("/api/changes/preview", {"kind": "revert", "target": "CLAUDE.md",
                                                    "payload": {"change_id": first["change_id"]},
                                                    "base_sha256": sha(after)})
    # changelog ไม่ตรงกับ backup → backup_corrupt ก่อนถึง validator (validator ถูกทดสอบใน test ก่อนหน้า)
    assert response.status_code == 422 and response.json()["code"] == "backup_corrupt"
    assert target(studio).read_bytes() == after


def test_delete_of_claude_md_is_refused_on_every_path(studio: Studio) -> None:
    from bmad_studio.core.writes import WritePipeline

    pipeline: WritePipeline = studio.app.state.writes
    assert codes(pipeline._validate_delete("claude-md")) == {"claude_md_delete_forbidden"}
    pipeline.delete_validators.pop("claude-md")
    assert codes(pipeline._validate_delete("claude-md")) == {"delete_forbidden"}  # fail closed แม้ไม่ได้ต่อ validator


def test_pipeline_refuses_claude_md_without_validator(settings: Settings) -> None:
    from bmad_studio.core.writes import VALIDATED_RULES

    assert "claude-md" in VALIDATED_RULES
    studio = Studio(settings)
    assert set(studio.app.state.writes.validators) >= {"claude-md"}


# -- คำสั่งมาตรฐาน + REQ-049 c --------------------------------------------------------------------

COMMAND = re.compile(r"`([^`]*flow resolve[^`]*)`")


def test_resolver_block_uses_only_the_standard_command() -> None:
    # AC: รูปมาตรฐานตัวต่อตัวบนเครื่องนี้; clone ที่อื่นใช้ path ของ repo นั้นในรูปเดียวกัน
    if cm.REPO_ROOT.as_posix() == "C:/Users/thedu/code/bmad-studio":
        assert cm.RESOLVE_COMMAND == STANDARD
    else:
        assert cm.RESOLVE_COMMAND == STANDARD.replace("C:/Users/thedu/code/bmad-studio", cm.REPO_ROOT.as_posix())
    assert (cm.REPO_ROOT / "pyproject.toml").is_file()
    text = "\n".join(cm.resolver_lines())
    commands = COMMAND.findall(text)
    assert commands and all(c.startswith(cm.RESOLVE_PREFIX + " <project>") for c in commands)
    assert ".exe" not in text and "bmad-studio flow" not in text
    assert "flow.yaml" in text and "requirements-review" in text


def _veda_with_ux_off(root: Path) -> None:
    seed_catalog(root)
    registry(root, {"name": "demo", "status": "planned", "requirementsReady": True, "hasFrontend": True})
    (project_dir(root, "demo") / "flow.yaml").write_text(
        "extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n", encoding="utf-8")


def test_req_049_c_resolver_block_and_ux_off_gives_architect(studio: Studio, capsys: pytest.CaptureFixture[str]) -> None:
    """CLAUDE.md มี block resolver แล้ว (ผ่าน pipeline) + โปรเจกต์ปิด ux → next = architecture หลัง requirements-review."""
    from bmad_studio.cli.main import main

    root = studio.settings.veda_root
    _veda_with_ux_off(root)
    before = target(studio).read_bytes()
    assert studio.commit(preview(studio, R, sha(before)).json()["data"], typed="CLAUDE.md").status_code == 200
    assert cm.block_state(target(studio).read_bytes(), R, cm.resolver_lines()) == "applied"
    assert main(["flow", "resolve", "demo", "--json", "--veda-root", str(root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["next"]["stage"] == "architecture" and data["next"]["agent"] == "architect"
    order = [s["id"] for s in data["stages"]]
    assert order.index("requirements-review") < order.index("architecture")
    assert next(s for s in data["stages"] if s["id"] == "ux-design")["status"] == "skipped"


@pytest.mark.skipif(shutil.which("uv") is None, reason="ไม่มี uv")
def test_command_in_block_runs_and_returns_json(settings: Settings) -> None:
    """รันคำสั่งตามข้อความใน block จริง (subprocess) กับ tmp veda ผ่าน VEDA_ROOT — ไม่แตะ veda จริง."""
    root = settings.veda_root
    _veda_with_ux_off(root)
    command = next(c for c in COMMAND.findall("\n".join(cm.resolver_lines())))
    argv = command.replace("<project>", "demo").split()
    assert argv[:3] == ["uv", "run", "--project"] and "--json" in argv
    env = {**os.environ, "VEDA_ROOT": str(root), "BMAD_STUDIO_VEDA_ROOT": str(root), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([shutil.which("uv") or "uv", *argv[1:]], capture_output=True, env=env, timeout=120,
                         cwd=str(root))
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    data = json.loads(out.stdout.decode("utf-8"))
    assert data["ok"] is True and data["project"] == "demo" and data["next"]["stage"] == "architecture"
