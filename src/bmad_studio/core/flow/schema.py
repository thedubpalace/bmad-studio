"""Schema ของ ``flow.yaml`` (AD-7, REQ-045 a) — pydantic model เดียวที่ validator/resolver/API ใช้.

- ``Flow`` = flow เต็ม (default หรือ effective หลัง merge)
- ``Overlay`` = ``.veda/projects/<name>/flow.yaml`` ที่เก็บเฉพาะส่วนต่าง (merge อยู่ใน :mod:`.merge`)

ทุก model เป็น ``strict`` + ``extra="forbid"``: ``enabled: yes`` (string) หรือ key ที่ไม่รู้จัก
เช่น ``include:`` = ผิด schema ไม่เดา. id/agent/skill เป็น ASCII kebab-case ตัวเล็กเท่านั้น
— กัน Unicode lookalike (``requirements-reviеw`` ที่ใช้ Cyrillic ``е``) และ ``Developer``
ที่บน NTFS เป็นไฟล์เดียวกับ ``developer.md`` แต่หลบการตรวจตาม agent ของ AD-8.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = 1
MAX_STAGES = 64
MAX_TRACKS = 8

#: ``^...$`` ของ pydantic-core (Rust regex) = ทั้งสตริง — ``"developer\n"`` ไม่ผ่าน
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64)]

#: field boolean ของ registry ที่ flow อ้างได้ (อ่านอย่างเดียว, AD-7)
RegistryFlag = Literal["requirementsReady", "designReady", "planReady", "hasFrontend"]
#: flag ที่ขั้นหนึ่ง "ทำให้เป็นจริง" — hasFrontend เป็นคุณสมบัติของโปรเจกต์ ไม่ใช่ผลของขั้นใด
ProducedFlag = Literal["requirementsReady", "designReady", "planReady"]
Gate = Literal["none", "human", "agent-confirm"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Stage(_Strict):
    id: Slug
    agent: Slug
    skill: Slug | None = None
    enabled: bool = True
    optional: bool = False
    after: Annotated[list[Slug], Field(max_length=MAX_STAGES)] = []
    gate: Gate = "none"
    produces: Annotated[list[ProducedFlag], Field(max_length=3)] = []
    condition: RegistryFlag | None = None


class EntryCondition(_Strict):
    """เงื่อนไขเข้า track (เช่น quick) — ค่าที่ยอมรับถูกล็อกใน :mod:`.policy`."""

    requirementsReady: bool
    status: Annotated[list[Slug], Field(max_length=16)]


class Track(_Strict):
    entry_condition: EntryCondition | None = None
    stages: Annotated[list[Stage], Field(min_length=1, max_length=MAX_STAGES)]


class Flow(_Strict):
    version: Literal[1]
    tracks: Annotated[dict[Slug, Track], Field(min_length=1, max_length=MAX_TRACKS)]


class StagePatch(_Strict):
    """field ที่ต่างจาก default ของขั้นเดิม (ไม่มี ``id`` — id คือ key)."""

    agent: Slug | None = None
    skill: Slug | None = None
    enabled: bool | None = None
    optional: bool | None = None
    after: Annotated[list[Slug], Field(max_length=MAX_STAGES)] | None = None
    gate: Gate | None = None
    produces: Annotated[list[ProducedFlag], Field(max_length=3)] | None = None
    condition: RegistryFlag | None = None


class TrackOverlay(_Strict):
    entry_condition: EntryCondition | None = None
    stages: Annotated[dict[Slug, StagePatch], Field(max_length=MAX_STAGES)] = {}
    add: Annotated[list[Stage], Field(max_length=MAX_STAGES)] = []


class Overlay(_Strict):
    extends: Literal["default"]
    tracks: Annotated[dict[Slug, TrackOverlay], Field(max_length=MAX_TRACKS)] = {}


def json_schema() -> dict[str, Any]:
    """JSON Schema ของไฟล์ flow ทั้งสองแบบ (export ไป ``docs/flow-schema.json``)."""
    flow = Flow.model_json_schema(ref_template="#/$defs/{model}")
    overlay = Overlay.model_json_schema(ref_template="#/$defs/{model}")
    defs = {**flow.pop("$defs", {}), **overlay.pop("$defs", {})}
    defs["Flow"] = flow
    defs["Overlay"] = overlay
    _close_maps(defs)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/thedubpalace/bmad-studio/docs/flow-schema.json",
        "title": "bmad-studio flow.yaml",
        "description": "flow เต็ม (.veda/flow.default.yaml) หรือ overlay ต่อโปรเจกต์ (.veda/projects/<name>/flow.yaml, extends: default)",
        "oneOf": [{"$ref": "#/$defs/Flow"}, {"$ref": "#/$defs/Overlay"}],
        "$defs": dict(sorted(defs.items())),
    }


def _close_maps(node: Any) -> None:
    """mapping ที่ key ถูกจำกัดด้วย pattern (``dict[Slug, ...]``) ต้องไม่รับ key อื่น — ให้ตรงกับ pydantic."""
    if isinstance(node, dict):
        if "patternProperties" in node:
            node["additionalProperties"] = False
        for value in node.values():
            _close_maps(value)
    elif isinstance(node, list):
        for value in node:
            _close_maps(value)


def json_schema_text() -> str:
    return json.dumps(json_schema(), ensure_ascii=False, indent=2) + "\n"
