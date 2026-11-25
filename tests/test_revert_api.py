"""Story 2.4 — changelog, read-only diff และ revert byte-for-byte (REQ-043 b, AD-5, AD-6)."""
from __future__ import annotations

import codecs
import hashlib
import json
from pathlib import Path

import pytest

from bmad_studio.core.settings import Settings

from .test_changes_api import AGENT, CUSTOM, Studio, sha

RAW_CRLF_BOM = codecs.BOM_UTF8 + "a = 1\r\nb = 'ไทย'\r\nc = 3\r\n".encode("utf-8")


@pytest.fixture
def wsettings(settings: Settings) -> Settings:
    return settings.model_copy(update={"enable_text_kind": True})


@pytest.fixture
def studio(wsettings: Settings) -> Studio:
    return Studio(wsettings)


def write(studio: Studio, target: str, text: str, base: str | None, typed: str | None = None) -> dict:
    preview = studio.preview(target, text, base).json()["data"]
    response = studio.commit(preview, typed=typed)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def revert_preview(studio: Studio, target: str, change_id: str, base: str | None):
    return studio.post("/api/changes/preview", {"kind": "revert", "target": target, "payload": {"change_id": change_id},
                                                "base_sha256": base})


def test_history_newest_first_with_types(studio: Studio, veda_root: Path) -> None:
    first = write(studio, CUSTOM, "x = 1\n", None)
    second = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    response = studio.client.get("/api/changes")
    assert response.status_code == 200
    items = response.json()["data"]
    assert [i["change_id"] for i in items] == [second["change_id"], first["change_id"]]
    assert [i["op"] for i in items] == ["modify", "create"]
    assert items[0]["added"] == 1 and items[0]["removed"] == 1
    assert items[0]["changed_since"] is False and items[1]["changed_since"] is True
    assert items[0]["current_sha256"] == sha(b"x = 2\n")


def test_history_requires_session_and_read_permission(wsettings: Settings) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    anonymous = TestClient(create_app(wsettings))
    assert anonymous.get("/api/changes").status_code == 401
    viewer = Studio(wsettings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert viewer.client.get("/api/changes").json()["code"] == "forbidden"


def test_read_only_diff_of_an_entry(studio: Studio, veda_root: Path) -> None:
    write(studio, CUSTOM, "x = 1\ny = 2\n", None)
    entry = write(studio, CUSTOM, "x = 1\ny = 3\n", sha(b"x = 1\ny = 2\n"))
    data = studio.client.get(f"/api/changes/{entry['change_id']}").json()["data"]
    assert data["path"] == CUSTOM and data["diff"]["added"] == 1 and data["diff"]["removed"] == 1
    assert studio.client.get("/api/changes/" + "0" * 32).status_code == 404
    assert studio.client.get("/api/changes/..%2F..%2Fetc").status_code in {403, 404}


def test_revert_is_byte_for_byte_for_crlf_bom(studio: Studio, veda_root: Path) -> None:
    path = veda_root / CUSTOM
    path.write_bytes(RAW_CRLF_BOM)
    entry = write(studio, CUSTOM, "a = 1\nb = 'ไทย'\nc = 30\n", sha(RAW_CRLF_BOM))
    assert path.read_bytes() != RAW_CRLF_BOM
    preview = revert_preview(studio, CUSTOM, entry["change_id"], sha(path.read_bytes())).json()["data"]
    assert preview["errors"] == [] and preview["warnings"] == [] and preview["kind"] == "revert"
    assert preview["impact"] == "normal" and preview["diff"]["added"] == 1
    result = studio.commit(preview)
    assert result.status_code == 200
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha(RAW_CRLF_BOM)
    entries = studio.changes()
    assert entries[-1]["kind"] == "revert" and entries[-1]["reverts"] == entry["change_id"]


def test_revert_high_impact_agent_needs_typed_confirmation(studio: Studio, veda_root: Path) -> None:
    path = veda_root / AGENT
    path.write_bytes(b"---\nname: developer\n---\nold body\r\n")
    entry = write(studio, AGENT, "---\nname: developer\n---\nnew body\n", sha(path.read_bytes()), typed="developer")
    preview = revert_preview(studio, AGENT, entry["change_id"], sha(path.read_bytes())).json()["data"]
    assert preview["impact"] == "high"
    assert studio.commit(preview).status_code == 400
    assert studio.commit(preview, typed="developer").status_code == 200
    assert path.read_bytes() == b"---\nname: developer\n---\nold body\r\n"


def test_revert_warns_when_file_changed_after_entry(studio: Studio, veda_root: Path) -> None:
    first = write(studio, CUSTOM, "x = 1\n", None)
    write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (veda_root / CUSTOM).write_bytes(b"x = 3\n")  # แก้ภายนอกด้วย
    # คืนค่ารายการแรก (สร้างไฟล์) = ย้ายไฟล์ไป backup
    preview = revert_preview(studio, CUSTOM, first["change_id"], sha(b"x = 3\n")).json()["data"]
    assert [w["code"] for w in preview["warnings"]] == ["changed_since"]
    assert preview["op"] == "delete" and preview["diff"]["removed"] == 1
    result = studio.commit(preview).json()["data"]
    assert not (veda_root / CUSTOM).exists()
    backup = studio.settings.runtime_dir / "backups" / result["change_id"] / "original.bin"
    assert backup.read_bytes() == b"x = 3\n"
    # และคืนค่าการย้ายนั้นได้อีก (ไฟล์กลับมา byte-for-byte)
    back = revert_preview(studio, CUSTOM, result["change_id"], None).json()["data"]
    assert back["op"] == "create"
    assert studio.commit(back).status_code == 200
    assert (veda_root / CUSTOM).read_bytes() == b"x = 3\n"


def test_revert_already_matching_is_an_error(studio: Studio, veda_root: Path) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (veda_root / CUSTOM).write_bytes(b"x = 1\n")
    preview = revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 1\n")).json()["data"]
    assert [e["code"] for e in preview["errors"]] == ["no_change"]
    assert studio.commit(preview).status_code == 422


@pytest.mark.parametrize("payload, status", [
    ({}, 422),
    ({"change_id": "../../x"}, 422),
    ({"change_id": "0" * 32}, 404),
])
def test_revert_bad_change_id(studio: Studio, payload: dict, status: int) -> None:
    response = studio.post("/api/changes/preview", {"kind": "revert", "target": CUSTOM, "payload": payload})
    assert response.status_code == status


def test_revert_cannot_retarget_another_file(studio: Studio, veda_root: Path) -> None:
    entry = write(studio, CUSTOM, "x = 1\n", None)
    other = "_bmad/custom/other.toml"
    response = revert_preview(studio, other, entry["change_id"], None)
    assert response.status_code == 422 and response.json()["code"] == "revert_target_mismatch"
    response = revert_preview(studio, ".veda/projects/registry.json", entry["change_id"], None)
    assert response.status_code == 403


def test_revert_with_tampered_backup_is_refused(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (wsettings.runtime_dir / "backups" / entry["change_id"] / "original.bin").write_bytes(b"evil = 1\n")
    response = revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 2\n"))
    assert response.status_code == 422 and response.json()["code"] == "backup_corrupt"
    assert (veda_root / CUSTOM).read_bytes() == b"x = 2\n"


def test_revert_with_missing_backup_is_410(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (wsettings.runtime_dir / "backups" / entry["change_id"] / "original.bin").unlink()
    assert revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 2\n")).status_code == 410


def test_revert_kind_available_without_text_kind(settings: Settings, veda_root: Path) -> None:
    response = Studio(settings).post("/api/changes/preview", {"kind": "revert", "target": CUSTOM,
                                                              "payload": {"change_id": "0" * 32}})
    assert response.json()["code"] == "change_not_found"


def test_delete_preview_token_cannot_be_reused_for_bytes(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    first = write(studio, CUSTOM, "x = 1\n", None)
    preview = revert_preview(studio, CUSTOM, first["change_id"], sha(b"x = 1\n")).json()["data"]
    # เขียน bytes ลงไฟล์ preview ของการลบ — ต้องไม่ถูกเขียนลงไฟล์จริง
    (wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.bin").write_bytes(b"evil = 1\n")
    assert studio.commit(preview).status_code == 403
    assert (veda_root / CUSTOM).read_bytes() == b"x = 1\n"
    meta = json.loads((wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.json").read_text(encoding="utf-8"))
    assert meta["op"] == "delete"


def test_detail_endpoint_requires_session_and_read_permission(studio: Studio, wsettings: Settings, veda_root: Path) -> None:
    from fastapi.testclient import TestClient

    from bmad_studio.api.app import create_app

    entry = write(studio, CUSTOM, "x = 1\n", None)
    url = f"/api/changes/{entry['change_id']}"
    assert TestClient(create_app(wsettings)).get(url).status_code == 401
    viewer = Studio(wsettings.model_copy(update={"admin_allowlist": "someone-else"}))
    assert viewer.client.get(url).json()["code"] == "forbidden"


def test_history_reports_high_impact_and_deleted_files(studio: Studio, veda_root: Path) -> None:
    path = veda_root / AGENT
    path.write_bytes(b"a\n")
    write(studio, AGENT, "b\n", sha(b"a\n"), typed="developer")
    created = write(studio, CUSTOM, "x = 1\n", None)
    moved = studio.commit(revert_preview(studio, CUSTOM, created["change_id"], sha(b"x = 1\n")).json()["data"])
    assert moved.status_code == 200
    items = studio.client.get("/api/changes").json()["data"]
    assert items[0]["op"] == "delete" and items[0]["current_sha256"] is None and items[0]["changed_since"] is False
    agent = next(i for i in items if i["path"] == AGENT)
    assert agent["impact"] == "high"
    assert studio.client.get("/api/changes?limit=1").json()["data"][0]["change_id"] == items[0]["change_id"]
    assert studio.client.get("/api/changes?limit=0").status_code == 422


def test_history_survives_corrupt_changelog_rows(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    with open(wsettings.runtime_dir / "changes.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"change_id": "' + "e" * 32 + '", "path": "_bmad/custom/config.toml", "added": "many", "op": "rm -rf"}\n')
        handle.write("not json\n")
    response = studio.client.get("/api/changes")
    assert response.status_code == 200
    first = response.json()["data"][0]
    assert first["added"] == 0 and first["op"] == "modify"


def test_detail_of_legacy_entry_without_new_bin_is_unavailable(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (wsettings.runtime_dir / "backups" / entry["change_id"] / "new.bin").unlink()
    response = studio.client.get(f"/api/changes/{entry['change_id']}")
    assert response.status_code == 410 and response.json()["code"] == "diff_unavailable"


def test_detail_with_tampered_backup_is_unavailable(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    (wsettings.runtime_dir / "backups" / entry["change_id"] / "new.bin").write_bytes(b"lies = 1\n")
    assert studio.client.get(f"/api/changes/{entry['change_id']}").status_code == 410


def test_swapped_backup_folder_is_refused(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    """backup ที่ตรงกันเอง (meta.sha256 = original.bin) แต่ไม่ใช่ของรายการนี้ ต้องถูกปฏิเสธ."""
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    folder = wsettings.runtime_dir / "backups" / entry["change_id"]
    evil = b"evil = 1\n"
    (folder / "original.bin").write_bytes(evil)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    meta["sha256"] = hashlib.sha256(evil).hexdigest()
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    response = revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 2\n"))
    assert response.status_code == 422 and response.json()["code"] == "backup_corrupt"
    # ปลอมว่าเดิมไม่มีไฟล์ (จะกลายเป็นการลบ) ก็ต้องถูกปฏิเสธ
    meta["existed"] = False
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "original.bin").unlink()
    response = revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 2\n"))
    assert response.status_code == 422
    assert (veda_root / CUSTOM).read_bytes() == b"x = 2\n"


def test_tampering_revert_extra_breaks_token(studio: Studio, veda_root: Path, wsettings: Settings) -> None:
    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    preview = revert_preview(studio, CUSTOM, entry["change_id"], sha(b"x = 2\n")).json()["data"]
    meta_path = wsettings.runtime_dir / "previews" / f"{preview['preview_id']}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["extra"] = {"reverts": "0" * 32}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert studio.commit(preview).status_code == 403
    assert (veda_root / CUSTOM).read_bytes() == b"x = 2\n"


def test_revert_into_missing_folder_is_blocked_at_preview(studio: Studio, veda_root: Path) -> None:
    import shutil

    write(studio, CUSTOM, "x = 1\n", None)
    entry = write(studio, CUSTOM, "x = 2\n", sha(b"x = 1\n"))
    shutil.rmtree(veda_root / "_bmad" / "custom")
    preview = revert_preview(studio, CUSTOM, entry["change_id"], None).json()["data"]
    assert "parent_missing" in [e["code"] for e in preview["errors"]]


def test_revert_of_a_huge_change_is_allowed_with_a_summary(studio: Studio, veda_root: Path) -> None:
    small = b"x = 1\n"
    (veda_root / CUSTOM).write_bytes(small)
    big = "".join(f"k{i} = {i}\n" for i in range(12_000))
    # เขียนไฟล์ใหญ่ผ่าน text ไม่ได้ (diff ใหญ่เกินตรวจ) — สร้างสถานการณ์ด้วยการเขียนตรงใน tmp veda แล้ว revert
    entry = write(studio, CUSTOM, "x = 2\n", sha(small))
    (veda_root / CUSTOM).write_bytes(big.encode())
    preview = revert_preview(studio, CUSTOM, entry["change_id"], sha(big.encode())).json()["data"]
    assert preview["diff"]["truncated"] is True and preview["errors"] == []
    assert "diff_summary_only" in [w["code"] for w in preview["warnings"]]
    assert studio.commit(preview).status_code == 200
    assert (veda_root / CUSTOM).read_bytes() == small
