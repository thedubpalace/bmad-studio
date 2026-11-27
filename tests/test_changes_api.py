"""Story 2.2 — preview → confirm token → atomic commit ผ่าน HTTP (REQ-043, AD-5) รวม hostile probes.

ทุก test ใช้ tmp veda จาก conftest (ไม่แตะ veda จริง).
"""
from __future__ import annotations

import codecs
import hashlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api.app import create_app
from bmad_studio.core.settings import Settings

from .conftest import PASSWORD, USERNAME

AGENT = ".claude/agents/developer.md"
CUSTOM = "_bmad/custom/config.toml"
AGENT_TEXT = "---\nname: developer\n---\n" + "".join(f"line {i}\n" for i in range(1, 301))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def wsettings(settings: Settings) -> Settings:
    return settings.model_copy(update={"enable_text_kind": True})


@pytest.fixture
def agent_file(veda_root: Path) -> Path:
    path = veda_root / AGENT
    path.write_bytes(AGENT_TEXT.encode("utf-8"))
    return path


class Studio:
    def __init__(self, settings: Settings) -> None:
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.settings = settings
        response = self.client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
        assert response.status_code == 200
        self.csrf = response.json()["data"]["csrf"]

    def post(self, path: str, body: dict, **headers: str):
        return self.client.post(path, json=body, headers={"X-CSRF-Token": self.csrf, **headers})

    def preview(self, target: str, text: str, base: str | None, kind: str = "text"):
        return self.post("/api/changes/preview", {"kind": kind, "target": target, "payload": {"text": text},
                                                  "base_sha256": base})

    def commit(self, preview: dict, typed: str | None = None, token: str | None = None):
        return self.post("/api/changes/commit", {"preview_id": preview["preview_id"],
                                                 "confirm_token": token or preview["confirm_token"],
                                                 "typed_confirmation": typed})

    def changes(self) -> list[dict]:
        path = self.settings.runtime_dir / "changes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def generic_studio(settings: Settings) -> Studio:
    """Studio สำหรับทดสอบกลไกของ pipeline ด้วยไฟล์ ``_bmad/custom/*`` เป็นเป้าธรรมดา: ปิดกฎตามเนื้อหาของ BMAD
    (impact จากข้อความ + resolve script, story 6-1) ที่มี test ของตัวเองใน ``test_config_editor.py``."""
    studio = Studio(settings)
    studio.app.state.writes.impact_rules.clear()
    studio.app.state.writes.path_validators.clear()
    return studio


@pytest.fixture
def studio(wsettings: Settings) -> Studio:
    return generic_studio(wsettings)


def _edit_line_150(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("line 150\n", "line 150 edited\n")


# -- preview ------------------------------------------------------------------------------------

def test_preview_shape_and_single_hunk(studio: Studio, agent_file: Path) -> None:
    response = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes()))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) >= {"preview_id", "diff", "impact", "warnings", "errors", "confirm_token", "expires_at"}
    assert data["impact"] == "high" and data["confirm_phrase"] == "developer"
    assert data["errors"] == [] and data["op"] == "modify"
    assert len(data["diff"]["hunks"]) == 1 and data["diff"]["added"] == 1 and data["diff"]["removed"] == 1
    assert len(data["diff"]["hunks"][0]["lines"]) == 8  # 3 context + del + add + 3 context
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT  # preview ไม่เขียน


def test_normal_impact_for_bmad_custom(studio: Studio, veda_root: Path) -> None:
    data = studio.preview(CUSTOM, "[core]\nx = 1\n", None).json()["data"]
    assert data["impact"] == "normal" and data["op"] == "create" and data["confirm_phrase"] is None


@pytest.mark.parametrize(("raw", "new_text", "code"), [
    (b"a\nb\nc\n", "a\nb\nc\n", "no_change"),
    (b"a\nb\nc\n", "a\r\nb\r\nc\r\n", "no_change"),  # text เข้ามาแปลงเป็น EOL เดิม = ไม่เปลี่ยน
    (b"a: 1\n  b: 2\n  c: 3\n  d: 4\n", "a: 1\n    b: 2\n    c: 3\n    d: 4\n", "whitespace_only_change"),
])
def test_whitespace_only_changes_are_errors(studio: Studio, veda_root: Path, raw: bytes, new_text: str,
                                            code: str) -> None:
    (veda_root / AGENT).write_bytes(raw)
    data = studio.preview(AGENT, new_text, sha(raw)).json()["data"]
    assert [e["code"] for e in data["errors"]] == [code]


def test_preview_with_stale_base_is_409(studio: Studio, agent_file: Path) -> None:
    response = studio.preview(AGENT, "x\n", sha(b"something else"))
    assert response.status_code == 409 and response.json()["code"] == "conflict"
    response = studio.preview(AGENT, "x\n", None)  # คิดว่าเป็นไฟล์ใหม่ แต่มีอยู่แล้ว
    assert response.status_code == 409


@pytest.mark.parametrize("target", [
    ".veda/projects/registry.json", "_bmad/config.toml", ".claude/skills/bmad-build/SKILL.md",
    "_bmad/custom/customize.toml", "../outside.md", ".claude/agents/../../CLAUDE.md",
    "C:/Windows/win.ini", "\\\\server\\share\\x.md", "CLAUDE.md", ".veda/flow.default.yaml",
])
def test_forbidden_targets(studio: Studio, target: str) -> None:
    response = studio.preview(target, "x\n", None)
    assert response.status_code == 403 and response.json()["code"] == "path_forbidden"
    assert "\\" not in response.json()["error"] and "/" not in response.json()["error"]


def test_text_kind_disabled_by_default(settings: Settings) -> None:
    response = Studio(settings).preview(AGENT, "x\n", None)
    assert response.status_code == 422 and response.json()["code"] == "unknown_kind"


def test_unknown_kind_and_bad_body(studio: Studio) -> None:
    assert studio.preview(AGENT, "x\n", None, kind="no-such-kind").json()["code"] == "unknown_kind"
    # kind flow มีจริงตั้งแต่ story 4-1 แต่เขียน agent ไม่ได้ (rule ของ kind)
    assert studio.preview(AGENT, "x\n", None, kind="flow").json()["code"] == "path_forbidden"
    bad = studio.post("/api/changes/preview", {"kind": "text", "target": AGENT, "payload": {"text": 5}})
    assert bad.status_code == 422
    bad_sha = studio.post("/api/changes/preview", {"kind": "text", "target": AGENT, "base_sha256": "../x"})
    assert bad_sha.status_code == 422


def test_preview_requires_csrf(studio: Studio) -> None:
    response = studio.client.post("/api/changes/preview", json={"kind": "text", "target": AGENT})
    assert response.status_code == 403 and response.json()["code"] == "csrf_invalid"


def test_viewer_cannot_preview_or_commit(wsettings: Settings) -> None:
    viewer = Studio(wsettings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert viewer.preview(AGENT, "x\n", None).json()["code"] == "forbidden"


def test_oversized_text_rejected(studio: Studio) -> None:
    response = studio.preview(CUSTOM, "a" * (2 * 1024 * 1024 + 1), None)
    assert response.status_code == 413


# -- commit -------------------------------------------------------------------------------------

def test_commit_writes_backup_changelog_and_is_idempotent(studio: Studio, agent_file: Path,
                                                          wsettings: Settings) -> None:
    before = agent_file.read_bytes()
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(before)).json()["data"]
    response = studio.commit(preview, typed="developer")
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert agent_file.read_bytes() == _edit_line_150_bytes(before)
    assert result["sha256"] == sha(agent_file.read_bytes()) and result["replayed"] is False
    backup = wsettings.runtime_dir / "backups" / result["change_id"] / "original.bin"
    assert backup.read_bytes() == before
    assert result["backup"] == f".runtime/backups/{result['change_id']}"
    entries = studio.changes()
    assert len(entries) == 1 and entries[0]["path"] == AGENT and entries[0]["op"] == "modify"
    assert entries[0]["base_sha256"] == sha(before) and entries[0]["added"] == 1

    # replay: ผลเดิม ไม่เขียนซ้ำ ไม่ log ซ้ำ — แม้ไฟล์จะถูกแก้ต่อแล้ว
    agent_file.write_bytes(b"changed later\n")
    again = studio.commit(preview, typed="developer")
    assert again.status_code == 200 and again.json()["data"]["change_id"] == result["change_id"]
    assert again.json()["data"]["replayed"] is True
    assert agent_file.read_bytes() == b"changed later\n" and len(studio.changes()) == 1
    assert not list(agent_file.parent.glob("*.tmp"))


def _edit_line_150_bytes(before: bytes) -> bytes:
    return before.replace(b"line 150\n", b"line 150 edited\n")


def test_commit_creates_new_file_utf8_lf(studio: Studio, veda_root: Path) -> None:
    preview = studio.preview(CUSTOM, "[core]\r\nx = 'ไทย'\r\n", None).json()["data"]
    assert studio.commit(preview).status_code == 200
    assert (veda_root / CUSTOM).read_bytes() == "[core]\nx = 'ไทย'\n".encode("utf-8")
    meta = json.loads((studio.settings.runtime_dir / "backups" / studio.changes()[0]["change_id"] / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["existed"] is False


def test_crlf_bom_file_keeps_bytes_outside_edit(studio: Studio, veda_root: Path) -> None:
    # BOM: ใช้ไฟล์ agent (BMAD อ่าน TOML ที่มี BOM ไม่ได้ — _bmad/custom ที่มี BOM เป็น error, story 6-1)
    raw = codecs.BOM_UTF8 + b"a = 1\r\nb = 2\r\nc = 3\r\n"
    path = veda_root / AGENT
    path.write_bytes(raw)
    preview = studio.preview(AGENT, "a = 1\nb = 20\nc = 3\n", sha(raw)).json()["data"]
    assert studio.commit(preview, typed="developer").status_code == 200
    assert path.read_bytes() == codecs.BOM_UTF8 + b"a = 1\r\nb = 20\r\nc = 3\r\n"


def test_crlf_toml_keeps_bytes_outside_edit(studio: Studio, veda_root: Path) -> None:
    raw = b"a = 1\r\nb = 2\r\nc = 3\r\n"
    path = veda_root / CUSTOM
    path.write_bytes(raw)
    preview = studio.preview(CUSTOM, "a = 1\nb = 20\nc = 3\n", sha(raw)).json()["data"]
    assert preview["impact"] == "normal" and studio.commit(preview).status_code == 200
    assert path.read_bytes() == b"a = 1\r\nb = 20\r\nc = 3\r\n"


def test_bom_toml_in_bmad_custom_is_an_error(studio: Studio, veda_root: Path) -> None:
    raw = codecs.BOM_UTF8 + b"a = 1\r\n"
    (veda_root / CUSTOM).write_bytes(raw)
    data = studio.preview(CUSTOM, "a = 2\n", sha(raw)).json()["data"]
    assert [e["code"] for e in data["errors"]] == ["toml_bom"]


def test_file_changed_between_preview_and_commit_is_409(studio: Studio, agent_file: Path) -> None:
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes())).json()["data"]
    agent_file.write_bytes(b"edited elsewhere\n")
    response = studio.commit(preview, typed="developer")
    assert response.status_code == 409 and response.json()["code"] == "conflict"
    assert agent_file.read_bytes() == b"edited elsewhere\n" and studio.changes() == []


def test_file_deleted_between_preview_and_commit_is_409(studio: Studio, agent_file: Path) -> None:
    preview = studio.preview(AGENT, "x\n", sha(agent_file.read_bytes())).json()["data"]
    agent_file.unlink()
    assert studio.commit(preview, typed="developer").status_code == 409
    assert not agent_file.exists()


def test_file_created_between_preview_and_commit_is_409(studio: Studio, veda_root: Path) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    (veda_root / CUSTOM).write_bytes(b"y = 2\n")
    assert studio.commit(preview).status_code == 409
    assert (veda_root / CUSTOM).read_bytes() == b"y = 2\n"


@pytest.mark.parametrize("typed", [None, "", "Developer", "developer.md", "other"])
def test_high_impact_requires_exact_typed_confirmation(studio: Studio, agent_file: Path, typed) -> None:
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes())).json()["data"]
    response = studio.commit(preview, typed=typed)
    assert response.status_code == 400 and response.json()["code"] == "confirmation_required"
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT


def test_errors_block_commit_with_422(studio: Studio, agent_file: Path) -> None:
    preview = studio.preview(AGENT, AGENT_TEXT, sha(agent_file.read_bytes())).json()["data"]
    assert preview["errors"]
    response = studio.commit(preview, typed="developer")
    assert response.status_code == 422 and response.json()["code"] == "has_errors"


# -- hostile token probes -----------------------------------------------------------------------

def _two_previews(studio: Studio, veda_root: Path) -> tuple[dict, dict]:
    a = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    b = studio.preview("_bmad/custom/other.toml", "y = 2\n", None).json()["data"]
    return a, b


@pytest.mark.parametrize("mutate", [
    lambda tok: tok[:-1] + ("0" if tok[-1] != "0" else "1"),
    lambda tok: str(int(tok.split(".")[0]) + 3600) + "." + tok.split(".")[1],  # ยืด expiry
    lambda tok: tok.split(".")[1],
    lambda tok: "garbage",
    lambda tok: tok.split(".")[0] + ".ไทยไทย",
    lambda tok: tok + "." + tok,
])
def test_tampered_token_is_403(studio: Studio, veda_root: Path, mutate) -> None:
    a, _ = _two_previews(studio, veda_root)
    response = studio.commit(a, token=mutate(a["confirm_token"]))
    assert response.status_code == 403 and response.json()["code"] == "confirm_invalid"
    assert not (veda_root / CUSTOM).exists()


def test_token_from_a_different_diff_is_403(studio: Studio, veda_root: Path) -> None:
    a, b = _two_previews(studio, veda_root)
    response = studio.commit(a, token=b["confirm_token"])
    assert response.status_code == 403
    assert not (veda_root / CUSTOM).exists()


def test_token_from_another_session_is_403(wsettings: Settings, veda_root: Path) -> None:
    first, second = Studio(wsettings), Studio(wsettings)
    preview = first.preview(CUSTOM, "x = 1\n", None).json()["data"]
    response = second.commit(preview)
    assert response.status_code == 403 and response.json()["code"] == "confirm_invalid"
    assert not (veda_root / CUSTOM).exists()


def test_unknown_or_malformed_preview_id_is_403(studio: Studio) -> None:
    for pid in ("0" * 32, "../../etc/passwd", "A" * 32, "ไทย"):
        response = studio.post("/api/changes/commit", {"preview_id": pid, "confirm_token": "1.abc"})
        assert response.status_code == 403, pid


def test_expired_token_is_403(studio: Studio, veda_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    pipeline = studio.app.state.writes
    real = pipeline.clock
    monkeypatch.setattr(pipeline, "clock", lambda: real() + 601)
    response = studio.commit(preview)
    assert response.status_code == 403 and response.json()["code"] == "confirm_expired"
    assert not (veda_root / CUSTOM).exists()


def test_tampered_preview_bytes_on_disk_are_not_written(studio: Studio, veda_root: Path,
                                                         wsettings: Settings) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    (wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.bin").write_bytes(b"evil = true\n")
    assert studio.commit(preview).status_code == 403
    assert not (veda_root / CUSTOM).exists()


def test_tampered_preview_target_on_disk_breaks_token(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    meta_path = wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["rel"] = "_bmad/custom/evil.toml"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert studio.commit(preview).status_code == 403
    assert not (veda_root / "_bmad/custom/evil.toml").exists()


def test_concurrent_commits_of_same_preview_write_once(studio: Studio, veda_root: Path) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    results: list[int] = []

    def run() -> None:
        results.append(studio.commit(preview).status_code)

    threads = [threading.Thread(target=run) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * 5
    assert len(studio.changes()) == 1


def test_two_previews_same_base_second_commit_conflicts(studio: Studio, veda_root: Path) -> None:
    a = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    b = studio.preview(CUSTOM, "x = 2\n", None).json()["data"]
    assert studio.commit(a).status_code == 200
    assert studio.commit(b).status_code == 409
    assert (veda_root / CUSTOM).read_bytes() == b"x = 1\n"


def test_junction_swapped_in_after_preview_is_refused(studio: Studio, veda_root: Path, tmp_path: Path) -> None:
    import sys

    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    custom = veda_root / "_bmad" / "custom"
    outside = tmp_path / "outside"
    outside.mkdir()
    custom.rmdir()
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(custom))
    else:
        custom.symlink_to(outside, target_is_directory=True)
    response = studio.commit(preview)
    assert response.status_code == 403 and response.json()["code"] == "path_forbidden"
    assert list(outside.iterdir()) == []


# -- failures leave nothing changed -------------------------------------------------------------

def test_audit_failure_is_503_and_nothing_changes(studio: Studio, agent_file: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes())).json()["data"]
    audit = studio.app.state.audit
    original = audit.audit

    def failing(record: dict) -> None:
        if record.get("event") == "write":
            raise OSError("disk full")
        original(record)

    monkeypatch.setattr(audit, "audit", failing)
    response = studio.commit(preview, typed="developer")
    assert response.status_code == 503 and response.json()["code"] == "audit_unavailable"
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT and studio.changes() == []


def _fail_replace_for(target: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """ทำให้ ``os.replace`` ล้มเฉพาะเมื่อปลายทางคือไฟล์เป้าหมาย (backup/preview ยังเขียนได้)."""
    import os as real_os

    calls: list[str] = []
    original = real_os.replace

    def replace(src, dst):
        if real_os.path.normcase(str(dst)) == real_os.path.normcase(str(target)):
            calls.append(str(dst))
            raise PermissionError("locked by editor")
        return original(src, dst)

    monkeypatch.setattr(real_os, "replace", replace)
    return calls


def test_replace_failure_is_503_and_nothing_changes(studio: Studio, agent_file: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes())).json()["data"]
    calls = _fail_replace_for(agent_file, monkeypatch)
    response = studio.commit(preview, typed="developer")
    assert calls, "write_bytes_atomic ต้องถูกเรียกจริง"
    body = response.json()
    assert response.status_code == 503 and body["code"] == "write_failed" and body["details"]["step"] == "write"
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT
    assert not [p for p in agent_file.parent.iterdir() if p.name.endswith(".tmp")]
    assert studio.changes() == []


def test_backup_failure_is_503_and_leaves_no_partial_backup(studio: Studio, agent_file: Path,
                                                            wsettings: Settings,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    preview = studio.preview(AGENT, _edit_line_150(agent_file), sha(agent_file.read_bytes())).json()["data"]
    import os as real_os

    original = real_os.replace

    def replace(src, dst):
        if "backups" in str(dst):
            raise OSError("disk full")
        return original(src, dst)

    monkeypatch.setattr(real_os, "replace", replace)
    response = studio.commit(preview, typed="developer")
    assert response.status_code == 503 and response.json()["details"]["step"] == "backup"
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT
    backups = wsettings.runtime_dir / "backups"
    assert not backups.exists() or list(backups.iterdir()) == []


@pytest.mark.parametrize("create", [False, True])
def test_changelog_failure_restores_original(studio: Studio, veda_root: Path, create: bool,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    path = veda_root / CUSTOM
    if not create:
        path.write_bytes(b"x = 0\n")
    base = None if create else sha(path.read_bytes())
    preview = studio.preview(CUSTOM, "x = 1\n", base).json()["data"]

    def boom(record):
        raise OSError("disk full")

    monkeypatch.setattr(studio.app.state.writes.state, "append_change", boom)
    response = studio.commit(preview)
    assert response.status_code == 503 and response.json()["details"]["step"] == "changelog"
    if create:
        assert not path.exists()
    else:
        assert path.read_bytes() == b"x = 0\n"


def test_failed_restore_is_reported_honestly(studio: Studio, veda_root: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    pipeline = studio.app.state.writes
    monkeypatch.setattr(pipeline.state, "append_change", lambda record: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(pipeline, "_restore", lambda guarded, original: False)
    response = studio.commit(preview)
    assert response.status_code == 500 and response.json()["code"] == "restore_failed"
    assert response.json()["details"]["change_id"]


def test_mutation_audit_has_requested_and_outcome(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    preview = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    studio.commit(preview)
    raw = (wsettings.runtime_dir / "audit.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(x) for x in raw.splitlines()]
    writes = [x for x in lines if x.get("event") == "write"]
    assert [x["phase"] for x in writes] == ["requested", "committed"]
    assert writes[0]["path"] == CUSTOM and writes[1]["change_id"] == writes[0]["change_id"]
    assert preview["confirm_token"] not in raw and preview["confirm_token"].split(".")[1] not in raw


def test_preview_cap_rejects_new_and_keeps_pending(studio: Studio, veda_root: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_studio.core import writes

    monkeypatch.setattr(writes, "MAX_PREVIEWS", 5)
    previews = [studio.preview(f"_bmad/custom/c{i}.toml", f"x = {i}\n", None).json()["data"] for i in range(5)]
    blocked = studio.preview("_bmad/custom/c9.toml", "x = 9\n", None)
    assert blocked.status_code == 429 and blocked.json()["code"] == "too_many_previews"
    # preview ที่ค้างอยู่ทั้งหมดยัง commit ได้ (ไม่ถูกไล่ทิ้ง)
    for preview in previews:
        assert studio.commit(preview).status_code == 200


def test_expired_previews_are_swept(studio: Studio, veda_root: Path, wsettings: Settings,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import os as real_os

    old = studio.preview(CUSTOM, "x = 1\n", None).json()["data"]
    folder = wsettings.runtime_dir / "previews"
    (folder / "orphan.bin").write_bytes(b"x")
    for path in folder.iterdir():
        stamp = path.stat().st_mtime - 3600
        real_os.utime(path, (stamp, stamp))
    studio.preview("_bmad/custom/other.toml", "y = 1\n", None)
    names = {p.name for p in folder.iterdir()}
    assert f"{old['preview_id']}.json" not in names and "orphan.bin" not in names


@pytest.mark.parametrize("field, value", [
    ("errors", []), ("impact", "normal"), ("confirm_phrase", "x"), ("op", "create"), ("kind", "other"),
])
def test_tampering_preview_meta_breaks_token(studio: Studio, agent_file: Path, wsettings: Settings,
                                             field: str, value: object) -> None:
    preview = studio.preview(AGENT, AGENT_TEXT, sha(agent_file.read_bytes())).json()["data"]  # มี errors (no_change)
    meta_path = wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta[field] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    response = studio.commit(preview, typed="x")
    assert response.status_code == 403 and response.json()["code"] == "confirm_invalid"
    assert agent_file.read_text(encoding="utf-8") == AGENT_TEXT


def test_oversized_body_rejected_before_parse(studio: Studio, monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_studio.api import app as app_module

    monkeypatch.setattr(app_module, "MAX_API_BODY", 1000)
    response = studio.preview(CUSTOM, "a" * 5000, None)
    assert response.status_code == 413 and response.json()["code"] == "request_too_large"


def test_multibyte_text_over_byte_limit_is_413(studio: Studio) -> None:
    response = studio.preview(CUSTOM, "ก" * 800_000, None)  # 800k ตัวอักษร = 2.4 MB UTF-8
    assert response.status_code == 413 and response.json()["code"] == "file_too_large"


def test_adversarial_diff_preview_is_fast(studio: Studio, veda_root: Path) -> None:
    import time

    old = "".join(f"{i % 2}\n" for i in range(4000)).encode()
    (veda_root / CUSTOM).write_bytes(old)
    new = "".join(f"{(i + (i % 7 == 0)) % 2}\n" for i in range(4000))
    start = time.perf_counter()
    response = studio.preview(CUSTOM, new, sha(old))
    assert response.status_code == 200
    assert time.perf_counter() - start < 5.0


def test_huge_preview_is_a_small_response_that_cannot_be_committed(studio: Studio, veda_root: Path) -> None:
    raw = "".join(f"line {i} of the agent body text here\n" for i in range(200)).encode()
    (veda_root / CUSTOM).write_bytes(raw)
    response = studio.preview(CUSTOM, "x\n" * (1024 * 1024 - 10), sha(raw))
    assert response.status_code == 200
    assert len(response.content) < 20_000  # เดิม 69 MB
    data = response.json()["data"]
    assert data["diff"]["truncated"] is True and "diff_too_large" in [e["code"] for e in data["errors"]]
    assert studio.commit(data).status_code == 422
    assert (veda_root / CUSTOM).read_bytes() == raw


def test_preview_cap_is_checked_before_any_diff_work(studio: Studio, veda_root: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_studio.core import writes

    monkeypatch.setattr(writes, "MAX_PREVIEWS", 0)

    def boom(*args, **kwargs):
        raise AssertionError("diff ต้องไม่ถูกเรียกเมื่อเกินจำนวน preview")

    monkeypatch.setattr(writes, "unified", boom)
    monkeypatch.setattr(writes.TextKind, "propose", boom)
    response = studio.preview(CUSTOM, "x = 1\n", None)
    assert response.status_code == 429
