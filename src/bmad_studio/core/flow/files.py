"""validator ของไฟล์ flow สำหรับ write pipeline (AD-5 + AD-7/AD-8): bytes ที่จะเขียน → issues.

ทุก kind ที่เขียน ``.veda/flow.default.yaml`` หรือ ``.veda/projects/<n>/flow.yaml`` (รวม revert) ผ่านที่นี่.
"""
from __future__ import annotations

from bmad_studio.core.flow.merge import validate_overlay_bytes
from bmad_studio.core.flow.validate import Catalog, FileValidator, FlowIssues, validate_flow_bytes
from bmad_studio.core.path_guard import FLOW_DEFAULT_RULE, FLOW_PROJECT_RULE
from bmad_studio.core.paths import VedaPaths


def file_validators(paths: VedaPaths) -> dict[str, FileValidator]:
    def flow_default(raw: bytes) -> FlowIssues:
        return validate_flow_bytes(raw, Catalog.scan(paths))[1]

    def flow_project(raw: bytes) -> FlowIssues:
        # merge กับ default ปัจจุบันแล้วตรวจ effective — AD-8 ตรวจกับผลรวม ไม่ใช่กับ overlay
        return validate_overlay_bytes(raw, paths, Catalog.scan(paths))

    return {FLOW_DEFAULT_RULE: flow_default, FLOW_PROJECT_RULE: flow_project}
