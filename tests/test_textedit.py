"""YAML/TOML แก้ค่าเดียวแล้ว diff มีเฉพาะบรรทัดนั้น (REQ-043 c, AD-6)."""
from __future__ import annotations

import difflib

import pytest

from bmad_studio.adapters.fs import decode
from bmad_studio.core.errors import StudioError
from bmad_studio.core.textedit import toml_set, yaml_set

FLOW_YAML = """\
# flow ของโปรเจกต์ — comment ต้องอยู่
version: 1
tracks:
  full:
    stages:
      - id: requirements-review
        gate: human   # ล็อก
        enabled: true
        produces: [requirementsReady]
      - id: ux
        agent: "ux-designer"
        enabled: true
        after:
          - requirements-review
      - id: build
        agent: 'developer'
        enabled: true
        note: ข้อความไทยยาว ๆ ที่ยาวมากกว่าแปดสิบตัวอักษรเพื่อทดสอบว่า ruamel ไม่ตัดบรรทัดใหม่ให้เราเลยแม้แต่บรรทัดเดียว
"""

INDENTED_SEQ_YAML = """\
stages:
    -   id: a
        enabled: true
    -   id: b
        enabled: true
"""

CONFIG_TOML = """\
# team config
[core]
communication_language = "Thai"   # ภาษา
document_output_language = 'Thai'

[agents.dev]
model = "opus"
tools = ["Read", "Write"]

[[extra]]
name = "x"
"""


def _changed_lines(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def test_yaml_single_value_changes_single_line() -> None:
    after = yaml_set(FLOW_YAML, ["tracks", "full", "stages", 1, "enabled"], False)
    assert _changed_lines(FLOW_YAML, after) == ["-        enabled: true", "+        enabled: false"]


def test_yaml_preserves_indented_sequences() -> None:
    after = yaml_set(INDENTED_SEQ_YAML, ["stages", 1, "enabled"], False)
    assert _changed_lines(INDENTED_SEQ_YAML, after) == ["-        enabled: true", "+        enabled: false"]


def test_yaml_keeps_missing_trailing_newline() -> None:
    text = "a: 1\nb: 2"
    assert yaml_set(text, ["b"], 3) == "a: 1\nb: 3"


def test_yaml_through_crlf_bom_doc_only_touches_one_line() -> None:
    raw = b"\xef\xbb\xbf" + FLOW_YAML.replace("\n", "\r\n").encode("utf-8")
    doc = decode(raw)
    new = doc.encode(yaml_set(doc.text, ["version"], 2))
    assert new.startswith(b"\xef\xbb\xbf")
    assert new.count(b"\r\n") == raw.count(b"\r\n")
    assert raw.replace(b"version: 1", b"version: 2") == new


def test_yaml_unknown_key_and_invalid() -> None:
    with pytest.raises(StudioError) as exc:
        yaml_set(FLOW_YAML, ["nope", "x"], 1)
    assert exc.value.code == "key_not_found"
    with pytest.raises(StudioError) as exc:
        yaml_set("a: [1, 2\n", ["a"], 1)
    assert exc.value.code == "invalid_yaml"


def test_toml_single_value_changes_single_line() -> None:
    after = toml_set(CONFIG_TOML, ["agents", "dev", "model"], "sonnet")
    assert _changed_lines(CONFIG_TOML, after) == ['-model = "opus"', '+model = "sonnet"']


def test_toml_preserves_inline_comment() -> None:
    after = toml_set(CONFIG_TOML, ["core", "communication_language"], "English")
    changed = _changed_lines(CONFIG_TOML, after)
    assert len(changed) == 2 and changed[1].endswith("# ภาษา")


def test_toml_unknown_key_rejected_unless_create() -> None:
    with pytest.raises(StudioError) as exc:
        toml_set(CONFIG_TOML, ["core", "nope"], "x")
    assert exc.value.code == "key_not_found"
    after = toml_set(CONFIG_TOML, ["core", "new_key"], "x", create=True)
    assert 'new_key = "x"' in after


def test_toml_invalid() -> None:
    with pytest.raises(StudioError) as exc:
        toml_set("[core\nx = 1\n", ["core", "x"], 2)
    assert exc.value.code == "invalid_toml"


def test_yaml_keeps_explicit_document_start() -> None:
    text = "---\na: 1\nb: 2\n"
    after = yaml_set(text, ["b"], 3)
    assert after == "---\na: 1\nb: 3\n"


@pytest.mark.parametrize(
    "text",
    [
        "# c\n---\na: 1\n",  # comment ก่อน ---
        "x: &a 1\ny: *a\n",  # anchor/alias
        "a: 1\n---\nb: 2\n",  # หลายเอกสาร
        "a:   1\nb: {c: 1,   d: 2}\n",  # ช่องว่างที่ ruamel ไม่เก็บ
    ],
)
def test_yaml_not_preservable_is_rejected_not_reformatted(text: str) -> None:
    path = ["a"] if not text.startswith("x") else ["x"]
    with pytest.raises(StudioError) as exc:
        yaml_set(text, path, 2)
    assert exc.value.code in {"format_not_preservable", "invalid_yaml"}


def test_yaml_key_semantics() -> None:
    with pytest.raises(StudioError) as exc:
        yaml_set("a: 1\n", [5], 2)  # int key ที่ไม่มีอยู่ ไม่สร้างใหม่
    assert exc.value.code == "key_not_found"
    with pytest.raises(StudioError):
        yaml_set("a:\n- 1\n- 2\n", ["a", -1], 3)  # ไม่รับ index ติดลบ
    with pytest.raises(StudioError):
        yaml_set("a:\n- 1\n- 2\n", ["a", True], 3)
    with pytest.raises(StudioError):
        yaml_set("a:\n- 1\n", ["a", 1], 3, create=True)  # create ไม่ต่อ list
    with pytest.raises(StudioError):
        yaml_set("a: 1\n", ["nope", "x"], 2, create=True)  # create ไม่สร้าง parent
    assert yaml_set("a: 1\n", ["b"], 2, create=True) == "a: 1\nb: 2\n"


def test_invalid_values_are_422_not_500() -> None:
    with pytest.raises(StudioError) as exc:
        yaml_set("a: 1\n", ["a"], object())
    assert exc.value.code == "invalid_value"
    with pytest.raises(StudioError) as exc:
        toml_set(CONFIG_TOML, ["core", "communication_language"], None)
    assert exc.value.code == "invalid_value"
    with pytest.raises(StudioError) as exc:
        toml_set(CONFIG_TOML, ["core", "communication_language"], object())
    assert exc.value.code == "invalid_value"


def test_toml_array_of_tables_index() -> None:
    after = toml_set(CONFIG_TOML, ["extra", 0, "name"], "y")  # type: ignore[list-item]
    assert _changed_lines(CONFIG_TOML, after) == ['-name = "x"', '+name = "y"']



# -- yaml_sync (story 4-1: overlay ของ flow editor) ---------------------------------------------

from bmad_studio.core.errors import StudioError as _StudioError  # noqa: E402
from bmad_studio.core.textedit import yaml_sync  # noqa: E402

SYNC_DOC = "# head\na: 1  # keep a\nb:\n  c: [x, y]\n  d: true\ne: old\n"


def test_yaml_sync_noop_keeps_bytes() -> None:
    assert yaml_sync(SYNC_DOC, {"a": 1, "b": {"c": ["x", "y"], "d": True}, "e": "old"}) == SYNC_DOC


def test_yaml_sync_changes_only_what_differs() -> None:
    out = yaml_sync(SYNC_DOC, {"a": 1, "b": {"c": ["x", "y"], "d": False}, "e": "old"})
    assert out == SYNC_DOC.replace("d: true", "d: false")


def test_yaml_sync_deletes_and_adds_keys() -> None:
    out = yaml_sync(SYNC_DOC, {"a": 1, "b": {"c": ["x", "y"]}, "f": 2})
    assert "# head" in out and "# keep a" in out and "d:" not in out and "e:" not in out and "f: 2" in out


def test_yaml_sync_distinguishes_bool_and_number_types() -> None:
    assert yaml_sync("a: 1\nb: true\n", {"a": True, "b": 1}) == "a: true\nb: 1\n"


def test_yaml_sync_mapping_to_scalar() -> None:
    assert yaml_sync("a:\n  b: 1\n", {"a": "x"}) == "a: x\n"


@pytest.mark.parametrize("text", ["- a\n", "a: &x 1\nb: *x\n", "a: [\n"])
def test_yaml_sync_rejects_unpreservable(text: str) -> None:
    with pytest.raises(_StudioError):
        yaml_sync(text, {"a": 1})
