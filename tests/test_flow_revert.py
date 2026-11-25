"""Story 3.1 — revert ที่เป้าหมายเป็น flow ต้องผ่าน validator ของ flow (review epic 2: N-revert-validator).

ฉบับสำรองอาจเป็นฉบับที่ถูกแก้มือให้ข้าม gate ก่อนที่แอปจะแก้ — revert ต้องไม่พามันกลับมา.
ยังไม่มี kind ``flow`` (epic 4) จึงใช้ kind ทดสอบที่เขียน flow ทั้งไฟล์เพื่อสร้างประวัติ.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bmad_studio.adapters import fs
from bmad_studio.adapters.state import RuntimeState
from bmad_studio.api.app import create_app
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow.files import file_validators
from bmad_studio.core.flow.validate import DEFAULT_TEMPLATE
from bmad_studio.core.path_guard import GuardedPath, PathGuard
from bmad_studio.core.paths import VedaPaths
from bmad_studio.core.settings import Settings
from bmad_studio.core.writes import ChangeRequest, Proposal, RevertKind, WritePipeline

from .flowkit import default_data, dump, seed_catalog, stage

SECRET = "k" * 40
FP = "session-fp"
DEFAULT_TARGET = ".veda/flow.default.yaml"
PROJECT_TARGET = ".veda/projects/demo/flow.yaml"


class FlowTextKind:
    """test-only: เขียน flow ทั้งไฟล์ (เลียนแบบการแก้ที่ epic 4 จะทำผ่าน kind flow)."""

    name = "flow-text"
    rules = frozenset({"flow-default", "flow-project"})
    check_whitespace = True

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        return Proposal(current.encode(payload["text"]))


def pipeline(veda_root: Path, runtime: Path, validators: Mapping | None) -> WritePipeline:
    state = RuntimeState(runtime)
    return WritePipeline(PathGuard(veda_root), state, SECRET,
                         {"flow-text": FlowTextKind(), "revert": RevertKind(state)}, validators=validators)


def write(pipe: WritePipeline, target: str, text: str, base: str | None) -> dict:
    """สร้างประวัติด้วย kind ทดสอบ — ใช้ pipeline ไม่มี validator เพื่อให้เขียนฉบับใดก็ได้ แล้วใช้ state เดียวกัน."""
    preview = pipe.preview(ChangeRequest("flow-text", target, {"text": text}, base), FP, "admin")
    assert preview["errors"] == [], preview["errors"]
    return pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", target.rsplit("/", 1)[-1])


def revert(pipe: WritePipeline, target: str, change_id: str, current: bytes) -> dict:
    return pipe.preview(ChangeRequest("revert", target, {"change_id": change_id}, fs.sha256_hex(current)), FP, "admin")


@pytest.fixture
def flow_root(veda_root: Path) -> Path:
    seed_catalog(veda_root)
    return veda_root


def bypass_text() -> str:
    data = default_data()
    stage(data, "requirements-review")["enabled"] = False
    return dump(data)


def test_revert_to_hand_edited_bypass_is_blocked(flow_root: Path, tmp_path: Path) -> None:
    target = flow_root / DEFAULT_TARGET
    hand_edited = bypass_text().encode()
    target.write_bytes(hand_edited)  # แก้มือนอกแอปให้ข้าม gate
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    entry = write(pipe, DEFAULT_TARGET, DEFAULT_TEMPLATE.read_text(encoding="utf-8"), fs.sha256_hex(hand_edited))
    preview = revert(pipe, DEFAULT_TARGET, entry["change_id"], target.read_bytes())
    assert any(e["code"] == "locked_gate_violation" and e.get("stage") == "requirements-review"
               for e in preview["errors"])
    with pytest.raises(StudioError) as info:
        pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.default.yaml")
    assert info.value.code == "has_errors"
    assert target.read_bytes() == DEFAULT_TEMPLATE.read_bytes()  # ไม่ถูกเขียน


def test_revert_to_valid_flow_is_allowed(flow_root: Path, tmp_path: Path) -> None:
    target = flow_root / DEFAULT_TARGET
    original = DEFAULT_TEMPLATE.read_bytes()
    target.write_bytes(original)
    data = default_data()
    stage(data, "devops")["enabled"] = False
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    entry = write(pipe, DEFAULT_TARGET, dump(data), fs.sha256_hex(original))
    preview = revert(pipe, DEFAULT_TARGET, entry["change_id"], target.read_bytes())
    assert preview["errors"] == []
    pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.default.yaml")
    assert target.read_bytes() == original


def test_revert_to_broken_yaml_is_blocked(flow_root: Path, tmp_path: Path) -> None:
    target = flow_root / DEFAULT_TARGET
    broken = b"a: &x [1]\nb: *x\n"
    target.write_bytes(broken)
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    entry = write(pipe, DEFAULT_TARGET, DEFAULT_TEMPLATE.read_text(encoding="utf-8"), fs.sha256_hex(broken))
    preview = revert(pipe, DEFAULT_TARGET, entry["change_id"], target.read_bytes())
    assert [e["code"] for e in preview["errors"]] == ["yaml_invalid"]


def _overlay_revert(flow_root: Path, tmp_path: Path, original: bytes) -> tuple[WritePipeline, dict, Path]:
    (flow_root / ".veda" / "projects" / "demo").mkdir()
    target = flow_root / PROJECT_TARGET
    target.write_bytes(original)
    writer = pipeline(flow_root, tmp_path / "rt", {"flow-project": lambda _raw: []})
    entry = write(writer, PROJECT_TARGET, "extends: default\n", fs.sha256_hex(original))
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    return pipe, revert(pipe, PROJECT_TARGET, entry["change_id"], target.read_bytes()), target


def test_project_overlay_revert_to_bypass_is_blocked(flow_root: Path, tmp_path: Path) -> None:
    """overlay ถูก merge กับ default แล้วตรวจ effective (3-2) — ฉบับสำรองที่ข้าม gate คืนค่าไม่ได้."""
    bypass = b"extends: default\ntracks:\n  full:\n    stages:\n      develop: {after: [requirements]}\n"
    pipe, preview, target = _overlay_revert(flow_root, tmp_path, bypass)
    assert any(e["code"] == "locked_gate_violation" and e.get("stage") == "develop" for e in preview["errors"])
    with pytest.raises(StudioError):
        pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.yaml")
    assert target.read_bytes() == b"extends: default\n"


def test_project_overlay_revert_to_valid_overlay_is_allowed(flow_root: Path, tmp_path: Path) -> None:
    valid = b"extends: default\ntracks:\n  full:\n    stages:\n      ux-design: {enabled: false}\n"
    pipe, preview, target = _overlay_revert(flow_root, tmp_path, valid)
    assert preview["errors"] == []
    pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.yaml")
    assert target.read_bytes() == valid


def test_project_overlay_validated_against_veda_default(flow_root: Path, tmp_path: Path) -> None:
    """default ใน veda ผิดกฎ → overlay ใดก็ตรวจไม่ผ่าน (ไม่ fallback ไปใช้ packaged)."""
    data = default_data()
    stage(data, "requirements-review")["enabled"] = False
    (flow_root / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    issues = file_validators(VedaPaths(flow_root))["flow-project"](b"extends: default\n")
    assert issues[0]["code"] == "default_invalid"


def test_revert_without_flow_validator_fails_closed(flow_root: Path, tmp_path: Path) -> None:
    target = flow_root / DEFAULT_TARGET
    original = DEFAULT_TEMPLATE.read_bytes()
    target.write_bytes(original)
    data = default_data()
    stage(data, "devops")["enabled"] = False
    writer = pipeline(flow_root, tmp_path / "rt", {"flow-default": lambda _raw: []})
    entry = write(writer, DEFAULT_TARGET, dump(data), fs.sha256_hex(original))
    pipe = pipeline(flow_root, tmp_path / "rt", None)
    preview = revert(pipe, DEFAULT_TARGET, entry["change_id"], target.read_bytes())
    assert [e["code"] for e in preview["errors"]] == ["validator_missing"]


def test_app_pipeline_validators_use_settings_root(settings: Settings) -> None:
    """validator ที่แอปต่อจริงต้องตรวจ catalog ใต้ VEDA_ROOT และปฏิเสธ gate bypass (ไม่ใช่แค่มี key)."""
    seed_catalog(settings.veda_root)
    validators = create_app(settings).state.writes.validators
    assert set(validators) == {"flow-default", "flow-project"}
    assert validators["flow-default"](DEFAULT_TEMPLATE.read_bytes()) == []
    issues = validators["flow-default"](bypass_text().encode())
    assert any(i["code"] == "locked_gate_violation" and i.get("stage") == "requirements-review" for i in issues)


def test_app_blocks_hand_edited_bypass_revert_end_to_end(settings: Settings) -> None:
    seed_catalog(settings.veda_root)
    target = settings.veda_root / DEFAULT_TARGET
    hand_edited = bypass_text().encode()
    target.write_bytes(hand_edited)
    app_pipe = create_app(settings).state.writes
    # สร้างประวัติด้วย pipeline ไม่มี validator ที่ใช้ runtime เดียวกับแอป (แอปเองไม่มี kind ที่เขียน flow)
    writer = pipeline(settings.veda_root, settings.runtime_dir, {"flow-default": lambda _raw: []})
    writer._key = app_pipe._key  # noqa: SLF001 — token ของ revert ออกโดยแอป ไม่เกี่ยวกับ writer
    entry = write(writer, DEFAULT_TARGET, DEFAULT_TEMPLATE.read_text(encoding="utf-8"), fs.sha256_hex(hand_edited))
    preview = revert(app_pipe, DEFAULT_TARGET, entry["change_id"], target.read_bytes())
    assert any(e["code"] == "locked_gate_violation" for e in preview["errors"])
    with pytest.raises(StudioError):
        app_pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.default.yaml")
    assert target.read_bytes() == DEFAULT_TEMPLATE.read_bytes()


def test_every_kind_is_validated_not_only_revert(flow_root: Path, tmp_path: Path) -> None:
    """validator อยู่ที่ pipeline: kind ใดที่เขียน flow (เช่น kind flow ของ epic 4) ก็ถูกตรวจ."""
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    preview = pipe.preview(ChangeRequest("flow-text", DEFAULT_TARGET, {"text": bypass_text()}, None), FP, "admin")
    assert any(e["code"] == "locked_gate_violation" for e in preview["errors"])
    bare = pipeline(flow_root, tmp_path / "rt2", None)
    preview = bare.preview(ChangeRequest("flow-text", DEFAULT_TARGET, {"text": bypass_text()}, None), FP, "admin")
    assert [e["code"] for e in preview["errors"]] == ["validator_missing"]


def test_commit_revalidates_under_lock(flow_root: Path, tmp_path: Path) -> None:
    """review 3-1 N4: preview ผ่าน แต่ default เปลี่ยนก่อน commit → commit ปฏิเสธและไม่เขียน."""
    folder = flow_root / ".veda" / "projects" / "demo"
    folder.mkdir()
    target = flow_root / PROJECT_TARGET
    pipe = pipeline(flow_root, tmp_path / "rt", file_validators(VedaPaths(flow_root)))
    text = "extends: default\ntracks:\n  full:\n    stages:\n      devops: {enabled: false}\n"
    preview = pipe.preview(ChangeRequest("flow-text", PROJECT_TARGET, {"text": text}, None), FP, "admin")
    assert preview["errors"] == []
    data = default_data()  # default ใหม่ที่ไม่มีขั้น devops → overlay อ้างขั้นที่ไม่มี
    data["tracks"]["full"]["stages"] = [s for s in data["tracks"]["full"]["stages"] if s["id"] != "devops"]
    (flow_root / ".veda" / "flow.default.yaml").write_text(dump(data), encoding="utf-8")
    with pytest.raises(StudioError) as info:
        pipe.commit(preview["preview_id"], preview["confirm_token"], FP, "admin", "flow.yaml")
    assert info.value.code == "has_errors" and not target.exists()
