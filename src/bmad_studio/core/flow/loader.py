"""อ่าน YAML ของ flow แบบปลอดภัย (AD-8: ไม่มี flow.yaml ใดเลี่ยง gate ได้ด้วยลูกเล่นของ YAML).

ขั้นที่ 1 ตรวจ **event stream** ก่อนสร้าง object ใด ๆ:

- ห้าม anchor/alias (``&a``/``*a``) — ตัดทั้ง billion laughs และการแชร์ node ที่ทำให้แก้ขั้นหนึ่งแล้วอีกขั้นเปลี่ยนตาม
- ห้าม tag ที่ระบุเอง (``!!python/object``, ``!!binary``, ``!include``)
- ห้าม merge key ``<<`` (แม้ไม่มี alias ก็ merge mapping แบบ inline ได้)
- document เดียวเท่านั้น, จำกัดจำนวน event และความลึก, จำกัดขนาด bytes

ขั้นที่ 2 สร้าง object ด้วย ruamel ``typ="safe"`` (YAML 1.2: ``yes``/``on`` เป็น string) และ key ซ้ำ = error.
ผิดข้อใดข้อหนึ่ง = :class:`FlowYamlError` (ข้อความไทย ไม่มี path) — ไม่เดาว่าผู้เขียนตั้งใจอะไร.
"""
from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)

MAX_FLOW_BYTES = 256 * 1024
MAX_EVENTS = 5_000  # flow จริง ~300 event; 64 ขั้นเต็ม field ~1,500
MAX_DEPTH = 12


class FlowYamlError(ValueError):
    def __init__(self, code: str, message_th: str, line: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message_th = message_th
        self.line = line


def _line(event: Any) -> int | None:
    mark = getattr(event, "start_mark", None)
    return None if mark is None else int(mark.line) + 1


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        if len(raw) > MAX_FLOW_BYTES or len(raw.encode("utf-8", "surrogatepass")) > MAX_FLOW_BYTES:
            raise FlowYamlError("yaml_too_large", "ไฟล์ flow ใหญ่เกินกำหนด")
        return raw
    if len(raw) > MAX_FLOW_BYTES:
        raise FlowYamlError("yaml_too_large", "ไฟล์ flow ใหญ่เกินกำหนด")
    from bmad_studio.adapters.fs import decode  # อ่านอย่างเดียว: BOM/UTF-16 ตามที่ editor อื่นรับ
    from bmad_studio.core.errors import StudioError

    try:
        text = decode(raw).text
    except StudioError:
        raise FlowYamlError("yaml_invalid", "ไฟล์ flow ไม่ใช่ข้อความ UTF-8/UTF-16") from None
    if len(text.encode("utf-8", "surrogatepass")) > MAX_FLOW_BYTES:  # UTF-16 นับเป็นขนาด UTF-8 เหมือนกัน
        raise FlowYamlError("yaml_too_large", "ไฟล์ flow ใหญ่เกินกำหนด")
    return text


def _scan(text: str) -> None:
    yaml = YAML(typ="safe", pure=True)
    documents = count = 0
    # frame ต่อ collection ที่เปิดอยู่: [is_mapping, next_node_is_key]
    frames: list[list[bool]] = []
    try:
        for event in yaml.parse(text):
            count += 1
            if count > MAX_EVENTS:
                raise FlowYamlError("yaml_too_large", "ไฟล์ flow มีโครงสร้างมากเกินกำหนด")
            if isinstance(event, AliasEvent):
                raise FlowYamlError("yaml_alias", "ไม่รองรับ alias (*) ใน flow — เขียนค่าซ้ำตรง ๆ", _line(event))
            if getattr(event, "anchor", None) is not None:
                raise FlowYamlError("yaml_anchor", "ไม่รองรับ anchor (&) ใน flow", _line(event))
            if getattr(event, "tag", None) is not None:
                raise FlowYamlError("yaml_tag", "ไม่รองรับ tag (!...) ใน flow", _line(event))
            if isinstance(event, DocumentStartEvent):
                if getattr(event, "version", None) is not None or getattr(event, "tags", None):
                    # %YAML 1.1 ทำให้ yes/on เป็น bool และ 010 เป็นฐาน 8 — รับเฉพาะ YAML 1.2 ค่าตั้งต้น
                    raise FlowYamlError("yaml_directive", "ไม่รองรับ directive (%YAML/%TAG) ใน flow", _line(event))
                documents += 1
                if documents > 1:
                    raise FlowYamlError("yaml_multi_document", "ไฟล์ flow ต้องมี document เดียว", _line(event))
            if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                frames.pop()
                continue
            if not isinstance(event, (ScalarEvent, MappingStartEvent, SequenceStartEvent)):
                continue
            is_key = bool(frames) and frames[-1][0] and frames[-1][1]
            if frames and frames[-1][0]:
                frames[-1][1] = not frames[-1][1]
            if isinstance(event, ScalarEvent):
                if is_key and event.value == "<<":
                    raise FlowYamlError("yaml_merge_key", "ไม่รองรับ merge key (<<) ใน flow", _line(event))
                continue
            if is_key:
                raise FlowYamlError("yaml_complex_key", "key ของ flow ต้องเป็นข้อความ", _line(event))
            frames.append([isinstance(event, MappingStartEvent), True])
            if len(frames) > MAX_DEPTH:
                raise FlowYamlError("yaml_too_deep", "ไฟล์ flow ซ้อนลึกเกินกำหนด", _line(event))
    except FlowYamlError:
        raise
    except Exception as exc:  # noqa: BLE001 — parser error ทุกชนิด = YAML ผิด
        mark = getattr(exc, "problem_mark", None)
        raise FlowYamlError("yaml_invalid", "ไฟล์ flow ไม่ใช่ YAML ที่ถูกต้อง",
                            None if mark is None else int(mark.line) + 1) from None


def load_yaml(raw: bytes | str) -> Any:
    """คืน object ของ YAML (dict/list/str/int/bool/None) หรือ raise :class:`FlowYamlError`."""
    text = _decode(raw)
    _scan(text)
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    try:
        return yaml.load(text)
    except DuplicateKeyError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise FlowYamlError("yaml_duplicate_key", "มี key ซ้ำใน flow",
                            None if mark is None else int(mark.line) + 1) from None
    except Exception as exc:  # noqa: BLE001
        mark = getattr(exc, "problem_mark", None)
        line = None if mark is None else int(mark.line) + 1
        raise FlowYamlError("yaml_invalid", "ไฟล์ flow ไม่ใช่ YAML ที่ถูกต้อง", line) from None
