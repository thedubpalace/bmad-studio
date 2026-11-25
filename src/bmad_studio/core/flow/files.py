"""validator ของไฟล์ flow สำหรับ write pipeline (AD-5 + AD-7/AD-8): bytes ที่จะเขียน → issues.

ทุก kind ที่เขียน ``.veda/flow.default.yaml`` หรือ ``.veda/projects/<n>/flow.yaml`` (รวม revert) ผ่านที่นี่.
"""
from __future__ import annotations

from collections.abc import Callable

from bmad_studio.core.flow.merge import validate_overlay_bytes
from bmad_studio.core.flow.validate import Catalog, FileValidator, FlowIssues
from bmad_studio.core.path_guard import FLOW_DEFAULT_RULE, FLOW_PROJECT_RULE
from bmad_studio.core.paths import VedaPaths


def file_validators(paths: VedaPaths) -> dict[str, FileValidator]:
    def flow_default(raw: bytes) -> FlowIssues:
        # ไฟล์เอง + overlay ของทุกโปรเจกต์กับ default ใหม่ (AD-7, story 4-3) — รวม revert ของไฟล์นี้
        from bmad_studio.core.flow.default_editor import validate_default_bytes

        return validate_default_bytes(paths, raw)

    def flow_project(raw: bytes) -> FlowIssues:
        # merge กับ default ปัจจุบันแล้วตรวจ effective — AD-8 ตรวจกับผลรวม ไม่ใช่กับ overlay
        return validate_overlay_bytes(raw, paths, Catalog.scan(paths))

    return {FLOW_DEFAULT_RULE: flow_default, FLOW_PROJECT_RULE: flow_project}


def delete_validators(paths: VedaPaths) -> dict[str, Callable[[], FlowIssues]]:
    """validator ของการลบ (ย้ายไป backup) ต่อ rule — ลบ ``.veda/flow.default.yaml`` = กลับไปใช้ packaged default
    ซึ่งต้องตรวจ overlay ทุกโปรเจกต์เหมือนการแก้ (AD-7). ลบ overlay ของโปรเจกต์ = กลับไปใช้ default ที่ตรวจแล้ว."""
    def flow_default() -> FlowIssues:
        from bmad_studio.core.flow.default_editor import validate_default_delete

        return validate_default_delete(paths)

    return {FLOW_DEFAULT_RULE: flow_default}
