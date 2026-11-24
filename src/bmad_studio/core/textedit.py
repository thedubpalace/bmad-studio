"""แก้ YAML/TOML ทีละค่าแบบรักษารูปแบบเดิม (AD-6, REQ-043 c).

- YAML: ``ruamel.yaml`` round-trip (comment, ลำดับ key, quote, ระยะเยื้องเดิม) — ห้าม dump ใหม่ด้วย PyYAML
- TOML: ``tomlkit`` (comment, ช่องว่าง, ลำดับ key เดิม)

ทั้งสองรับ/คืน text แบบ LF (จาก :class:`bmad_studio.adapters.fs.TextDoc`) — BOM/EOL ใส่กลับตอน encode.
"""
from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

import tomlkit
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.util import load_yaml_guess_indent
from tomlkit.exceptions import TOMLKitError

from bmad_studio.core.errors import StudioError

Key = str | int
_LF = chr(10)


def _invalid(kind: str) -> StudioError:
    return StudioError(f"invalid_{kind}", f"ไฟล์ {kind.upper()} ไม่ถูกต้อง", 422)


def _missing_key() -> StudioError:
    return StudioError("key_not_found", "ไม่พบ key ที่ต้องการแก้", 422)


def _invalid_value() -> StudioError:
    return StudioError("invalid_value", "ค่าที่ส่งมาบันทึกลงไฟล์ชนิดนี้ไม่ได้", 422)


def _not_preservable() -> StudioError:
    return StudioError(
        "format_not_preservable",
        "แก้ไฟล์นี้โดยรักษารูปแบบเดิมไม่ได้ (เช่น หลายเอกสาร, anchor หรือ comment ตำแหน่งพิเศษ) — แก้ด้วยมือแทน",
        422,
    )


def _step(node: Any, key: Key, *, last: bool, create: bool) -> Any:
    """ตรวจ key หนึ่งขั้น: mapping ต้องมี key (ยกเว้น create ที่ขั้นสุดท้ายกับ key แบบ str),
    sequence ต้องเป็น index int (ไม่ใช่ bool) ที่อยู่ในช่วง 0..len-1 — ไม่รับ index ติดลบ."""
    if isinstance(node, Mapping):
        if key in node and not isinstance(key, bool):
            return node
        if last and create and isinstance(key, str):
            return node
        raise _missing_key()
    if isinstance(node, list):
        if isinstance(key, int) and not isinstance(key, bool) and 0 <= key < len(node):
            return node
        raise _missing_key()
    raise _missing_key()


def _set_in(container: Any, path: Sequence[Key], value: Any, *, create: bool) -> None:
    if not path:
        raise _missing_key()
    node = container
    for index, key in enumerate(path):
        is_last = index == len(path) - 1
        _step(node, key, last=is_last, create=create)
        if is_last:
            try:
                node[key] = value
            except (TypeError, ValueError):  # tomlkit ConvertError เป็น TypeError/ValueError
                raise _invalid_value() from None
        else:
            node = node[key]


_INDENT_CANDIDATES: tuple[tuple[int, int, int], ...] = (
    (2, 2, 0), (2, 4, 2), (4, 4, 0), (4, 6, 2), (4, 4, 2), (3, 3, 0), (2, 3, 1),
)


def _yaml(mapping: int = 2, sequence: int = 2, offset: int = 0) -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096  # ไม่ตัดบรรทัดยาวใหม่
    yaml.indent(mapping=mapping, sequence=sequence, offset=offset)
    return yaml


def yaml_load(text: str) -> Any:
    try:
        return _yaml().load(text)
    except YAMLError:
        raise _invalid("yaml") from None


def _has_anchor(node: Any, seen: set[int] | None = None) -> bool:
    """มี anchor (``&a``) หรือ alias ที่ชี้ container เดิมซ้ำในเอกสารไหม."""
    seen = set() if seen is None else seen
    anchor = getattr(node, "anchor", None)
    if anchor is not None and getattr(anchor, "value", None):
        return True
    if isinstance(node, (Mapping, list)):
        if id(node) in seen:  # alias ของ container
            return True
        seen.add(id(node))
        items = list(node.items()) if isinstance(node, Mapping) else [(None, item) for item in node]
        return any(_has_anchor(key, seen) or _has_anchor(value, seen) for key, value in items)
    return False


def _dump(yaml: YAML, data: Any, text: str) -> str:
    stream = io.StringIO()
    yaml.dump(data, stream)
    out = stream.getvalue()
    if not text.endswith(_LF) and out.endswith(_LF):
        out = out[:-1]
    return out


def yaml_set(text: str, path: Sequence[Key], value: Any, *, create: bool = False) -> str:
    """คืน YAML ที่ค่าที่ ``path`` เปลี่ยนเป็น ``value``; ส่วนอื่นคงเดิม.

    ruamel ไม่จำระยะเยื้อง/``---`` ต่อไฟล์ จึงหาชุดตั้งค่าที่ dump เอกสาร **ที่ยังไม่แก้** ออกมาได้
    ตรงกับ text เดิมทุกตัวอักษรก่อน แล้วค่อยแก้และ dump ด้วยชุดนั้น — ถ้าไม่มีชุดใดรักษารูปแบบได้
    (หลายเอกสาร, anchor, comment ก่อน ``---`` ฯลฯ) ปฏิเสธด้วย ``format_not_preservable``
    แทนการคืน diff ที่แตะบรรทัดอื่น (REQ-043 c).
    """
    try:
        _data, guess_seq, guess_offset = load_yaml_guess_indent(text)
        data = _yaml().load(text)
    except YAMLError:
        raise _invalid("yaml") from None
    candidates = list(_INDENT_CANDIDATES)
    if guess_seq:
        offset = guess_offset or 0
        candidates.insert(0, (max(2, guess_seq - offset) if offset else guess_seq, guess_seq, offset))
    chosen: YAML | None = None
    for mapping, sequence, offset in candidates:
        if sequence < offset + 2:
            continue
        for explicit_start in (False, True):
            yaml = _yaml(mapping, sequence, offset)
            yaml.explicit_start = explicit_start
            try:
                if _dump(yaml, data, text) == text:
                    chosen = yaml
                    break
            except YAMLError:
                continue
        if chosen is not None:
            break
    if chosen is None or _has_anchor(data):
        # anchor/alias: แก้ค่าที่ถูกอ้างแล้ว ruamel ย้าย anchor ไปที่ alias ตัวถัดไป = diff หลายบรรทัด
        raise _not_preservable()
    _set_in(data, path, value, create=create)
    try:
        return _dump(chosen, data, text)
    except YAMLError:  # RepresenterError: ชนิดค่าที่ YAML แสดงไม่ได้
        raise _invalid_value() from None


def toml_load(text: str) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError:
        raise _invalid("toml") from None


def toml_set(text: str, path: Sequence[str], value: Any, *, create: bool = False) -> str:
    """คืน TOML ที่ค่าที่ ``path`` เปลี่ยนเป็น ``value``; ส่วนอื่นคงเดิม."""
    doc = toml_load(text)
    if tomlkit.dumps(doc) != text:
        raise _not_preservable()
    _set_in(doc, path, value, create=create)
    try:
        return tomlkit.dumps(doc)
    except (TypeError, ValueError, TOMLKitError):
        raise _invalid_value() from None
