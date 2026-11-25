"""Story 3.1 — ลูกเล่นของ YAML ที่ต้องไม่ทำให้ flow ข้าม gate หรือกิน CPU/memory (AD-8)."""
from __future__ import annotations

import codecs
import time

import pytest

from bmad_studio.core.flow.loader import MAX_FLOW_BYTES, FlowYamlError, load_yaml
from bmad_studio.core.flow.validate import DEFAULT_TEMPLATE, validate_flow_bytes

from .flowkit import CATALOG, codes

DEFAULT_TEXT = DEFAULT_TEMPLATE.read_text(encoding="utf-8")

BILLION_LAUGHS = "\n".join(
    ["a: &a [lol, lol, lol, lol, lol, lol, lol, lol, lol]"]
    + [f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, "
       f"*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]" for i in range(8)]
) + "\n"


def reject(text: str | bytes) -> str:
    with pytest.raises(FlowYamlError) as info:
        load_yaml(text)
    _flow, issues = validate_flow_bytes(text, CATALOG)
    assert codes(issues) == {"yaml_invalid"}
    return info.value.code


def test_billion_laughs_rejected_before_expansion() -> None:
    start = time.perf_counter()
    assert reject(BILLION_LAUGHS) in {"yaml_anchor", "yaml_alias"}
    assert time.perf_counter() - start < 1.0  # วัดจริงได้ ~1 ms; ขอบกว้างกัน machine ช้า


def test_alias_reusing_the_gate_stage_is_rejected() -> None:
    text = DEFAULT_TEXT.replace("      - id: requirements-review", "      - &gate\n        id: requirements-review", 1)
    assert reject(text) == "yaml_anchor"
    assert reject("a: 1\nb: *x\n") in {"yaml_alias", "yaml_invalid"}


@pytest.mark.parametrize("text", [
    "<<: {version: 1}\ntracks: {}\n",
    "version: 1\ntracks:\n  full:\n    <<: {stages: []}\n",
    "version: 1\n'<<': 2\n",
])
def test_merge_keys_rejected(text: str) -> None:
    assert reject(text) == "yaml_merge_key"


def test_merge_like_value_is_fine() -> None:
    assert load_yaml("a: '<<'\nb: ['<<']\n") == {"a": "<<", "b": ["<<"]}


def test_duplicate_keys_rejected_anywhere() -> None:
    assert reject("version: 1\nversion: 1\n") == "yaml_duplicate_key"
    dup_enabled = DEFAULT_TEXT.replace(
        "      - id: requirements-review\n        agent: human\n        enabled: true\n",
        "      - id: requirements-review\n        agent: human\n        enabled: true\n        enabled: false\n", 1)
    assert dup_enabled != DEFAULT_TEXT
    assert reject(dup_enabled) == "yaml_duplicate_key"


@pytest.mark.parametrize("text", [
    "a: !!python/object/apply:os.system ['echo hi']\n",
    "a: !!binary aGVsbG8=\n",
    "a: !include other.yaml\n",
    "a: !!str x\n",
    "!!map {a: 1}\n",
])
def test_explicit_tags_rejected(text: str) -> None:
    assert reject(text) == "yaml_tag"


@pytest.mark.parametrize("text", [
    "%YAML 1.1\n---\nversion: 1\nenabled: yes\n",
    "%TAG !e! tag:example.com,2000:\n---\na: 1\n",
])
def test_directives_rejected(text: str) -> None:
    """%YAML 1.1 จะทำให้ yes/on เป็น bool — ต้องไม่ผ่านไปถึง schema."""
    assert reject(text) == "yaml_directive"


def test_str_size_cap_counts_utf8_bytes() -> None:
    text = "a: '" + "ก" * (MAX_FLOW_BYTES // 2) + "'\n"  # ตัวอักษร < cap แต่ UTF-8 ~1.5x cap
    assert len(text) < MAX_FLOW_BYTES
    assert reject(text) == "yaml_too_large"


def test_utf16_size_cap_counts_utf8_bytes() -> None:
    body = ("a: '" + "ก" * 100_000 + "'\n").encode("utf-16-le")  # UTF-16 ~200 KB, UTF-8 ~300 KB
    assert len(body) < MAX_FLOW_BYTES
    assert reject(codecs.BOM_UTF16_LE + body) == "yaml_too_large"


def test_yaml_issue_carries_reason_not_field() -> None:
    _flow, issues = validate_flow_bytes("a: &x 1\n", CATALOG)
    assert issues[0]["reason"] == "yaml_anchor" and "field" not in issues[0]


def test_multi_document_rejected() -> None:
    assert reject(DEFAULT_TEXT + "---\nversion: 1\n") == "yaml_multi_document"


def test_complex_keys_rejected() -> None:
    assert reject("? [a, b]\n: 1\n") == "yaml_complex_key"


def test_size_and_depth_limits() -> None:
    assert reject("a: '" + "x" * (MAX_FLOW_BYTES + 1) + "'\n") == "yaml_too_large"
    assert reject(b"a: 1\n" + b"#" * MAX_FLOW_BYTES) == "yaml_too_large"
    assert reject("a: " + "[" * 50 + "]" * 50 + "\n") == "yaml_too_deep"


def test_many_events_rejected_fast() -> None:
    text = "a: [" + ", ".join(["1"] * 6_000) + "]\n"  # < 256 KiB แต่ event เยอะ
    assert len(text) < MAX_FLOW_BYTES
    start = time.perf_counter()
    assert reject(text) == "yaml_too_large"
    assert time.perf_counter() - start < 5.0


def test_garbage_and_binary() -> None:
    assert reject("version: [1\n") == "yaml_invalid"
    assert reject(b"\x00\x01\x02") == "yaml_invalid"
    assert reject(codecs.BOM_UTF32_LE + b"a\x00\x00\x00") == "yaml_invalid"


def test_yaml_12_booleans_are_strings_so_schema_rejects() -> None:
    _flow, issues = validate_flow_bytes(DEFAULT_TEXT.replace("enabled: true", "enabled: yes", 1), CATALOG)
    assert codes(issues) == {"schema_invalid"}


def test_bom_and_crlf_and_utf16_default_accepted() -> None:
    for raw in (codecs.BOM_UTF8 + DEFAULT_TEXT.encode(), DEFAULT_TEXT.replace("\n", "\r\n").encode(),
                codecs.BOM_UTF16_LE + DEFAULT_TEXT.encode("utf-16-le")):
        flow, issues = validate_flow_bytes(raw, CATALOG)
        assert issues == [] and flow is not None


def test_non_mapping_root() -> None:
    for text in ("- a\n", "just text\n", ""):
        _flow, issues = validate_flow_bytes(text, CATALOG)
        assert codes(issues) == {"schema_invalid"}
