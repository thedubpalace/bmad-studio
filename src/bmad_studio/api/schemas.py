"""Pydantic model ของ response (AD-12) — export เป็น OpenAPI แล้ว generate เป็น ``web/src/api/types.ts``."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ErrorEnvelope(BaseModel):
    ok: Literal[False]
    code: str
    error: str
    details: Any | None = None


class SessionData(BaseModel):
    authenticated: bool
    username: str | None = None
    role: Literal["admin", "viewer"] | None = None
    csrf: str | None = None
    review_url: str | None = None


class SessionEnvelope(BaseModel):
    ok: Literal[True]
    data: SessionData


class SectionError(BaseModel):
    code: str
    error: str
    reason: str | None = None


class SkillItem(BaseModel):
    id: str
    description: str
    override_team: bool
    override_user: bool


class AgentItem(BaseModel):
    id: str
    name: str | None = None
    title: str | None = None
    module: str | None = None
    description: str | None = None


class ResolvedConfig(BaseModel):
    """ส่วนที่มาจาก resolve_config.py — ``error`` ไม่ว่างเมื่อสคริปต์ใช้ไม่ได้ (ส่วนอื่นยังแสดงได้)."""
    ok: bool
    agents: list[AgentItem] = []
    core: dict[str, Any] = {}
    error: SectionError | None = None


class ToolStatus(BaseModel):
    uv: bool
    resolve_script: bool


class Inventory(BaseModel):
    version: str | None
    skills: list[SkillItem]
    config: ResolvedConfig
    tools: ToolStatus


class InventoryEnvelope(BaseModel):
    ok: Literal[True]
    data: Inventory


class DiffLine(BaseModel):
    type: Literal["context", "add", "del"]
    old: int | None = None
    new: int | None = None
    text: str
    spans: list[list[int]] = []


class DiffHunk(BaseModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine]


class Diff(BaseModel):
    hunks: list[DiffHunk]
    added: int
    removed: int
    truncated: bool = False  # true = ใหญ่เกินจะแสดงรายบรรทัด (hunks ว่าง, มีแค่ added/removed)
    old_eof_newline: bool = True  # false = ไม่มี newline ท้ายไฟล์ (UI แสดงป้าย)
    new_eof_newline: bool = True


class Issue(BaseModel):
    code: str
    error: str


class ChangePreview(BaseModel):
    preview_id: str
    path: str
    kind: str
    op: Literal["create", "modify", "delete"]
    impact: Literal["normal", "high"]
    confirm_phrase: str | None = None
    base_sha256: str | None = None
    proposed_sha256: str | None = None  # None = ย้ายไฟล์ไป backup
    diff: Diff
    warnings: list[Issue] = []
    errors: list[Issue] = []
    confirm_token: str
    expires_at: str


class ChangePreviewEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangePreview


class ChangeCommit(BaseModel):
    change_id: str
    path: str
    op: Literal["create", "modify", "delete"]
    sha256: str | None = None
    backup: str
    at: str
    replayed: bool


class ChangeCommitEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangeCommit


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 422, 429, 503)
}


class ChangeEntry(BaseModel):
    change_id: str
    at: str
    path: str
    kind: str
    op: Literal["create", "modify", "delete"]
    added: int
    removed: int
    impact: Literal["normal", "high"] = "normal"
    reverts: str | None = None
    new_sha256: str | None = None
    current_sha256: str | None = None
    changed_since: bool


class ChangeListEnvelope(BaseModel):
    ok: Literal[True]
    data: list[ChangeEntry]


class ChangeDiff(BaseModel):
    change_id: str
    path: str
    op: Literal["create", "modify", "delete"]
    kind: str
    at: str
    diff: Diff


class ChangeDiffEnvelope(BaseModel):
    ok: Literal[True]
    data: ChangeDiff


CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (400, 401, 403, 409, 411, 413, 422, 429, 500, 503)
}


# -- flow status (REQ-050, AD-1/AD-12) — ค่าทั้งหมดคำนวณใน core.flow.resolve ---------------------

StageStatus = Literal["done", "active", "pending", "skipped"]
GateKind = Literal["none", "human", "agent-confirm"]


class FlowIssue(BaseModel):
    code: str
    error: str
    track: str | None = None
    stage: str | None = None
    field: str | None = None
    reason: str | None = None


class NextStage(BaseModel):
    stage: str
    agent: str
    skill: str | None = None
    gate: GateKind
    optional: bool
    status: StageStatus
    note: str | None = None


class StageDefault(BaseModel):
    agent: str
    skill: str | None = None
    enabled: bool
    optional: bool
    after: list[str]
    gate: GateKind
    produces: list[str]
    condition: str | None = None


class StageRow(BaseModel):
    id: str
    agent: str
    skill: str | None = None
    gate: GateKind
    after: list[str]
    enabled: bool
    optional: bool
    condition: str | None = None
    status: StageStatus
    skipReason: str | None = None
    note: str | None = None
    locked: bool
    lockReason: str | None = None
    overridden: bool
    default: StageDefault | None = None


class LockedItem(BaseModel):
    stage: str | None = None
    field: str | None = None
    reason: str


class EntryCheck(BaseModel):
    ok: bool
    requirementsReady: bool
    status: list[str]
    projectStatus: str | None = None
    reason: str | None = None


class FlowSource(BaseModel):
    default: str
    overlay: str | None = None


class FlowWarning(BaseModel):
    code: str
    error: str
    stage: str | None = None


class FlowStatus(BaseModel):
    project: str
    hasSprint: bool
    flow: FlowSource
    registry: dict[str, Any]
    track: Literal["full", "quick"]
    next: NextStage | None = None
    stages: list[StageRow]
    locked: list[LockedItem]
    entry: EntryCheck | None = None
    complete: bool
    hints: list[str] = []
    warnings: list[FlowWarning] = []
    reviewUrl: str | None = None


class FlowStatusEnvelope(BaseModel):
    ok: Literal[True]
    data: FlowStatus


class StageChip(BaseModel):
    id: str
    agent: str
    status: StageStatus
    skipReason: str | None = None
    gate: GateKind


class ProjectFlowError(BaseModel):
    code: str
    source: str
    issues: list[FlowIssue]


class ProjectSummary(BaseModel):
    project: str
    description: str | None = None
    status: str | None = None
    error: ProjectFlowError | None = None
    next: NextStage | None = None
    complete: bool
    overridden: bool
    stages: list[StageChip]


class ProjectListEnvelope(BaseModel):
    ok: Literal[True]
    data: list[ProjectSummary]


# -- flow editor (story 4-1, REQ-046/047, AD-1/AD-12) — lock/overridden/validation คำนวณใน core.flow.editor -----

class FlowStage(BaseModel):
    id: str
    agent: str
    skill: str | None = None
    enabled: bool
    optional: bool
    after: list[str]
    gate: GateKind
    produces: list[str]
    condition: str | None = None


class EntryConditionModel(BaseModel):
    requirementsReady: bool
    status: list[str]


class FlowTrackModel(BaseModel):
    entry_condition: EntryConditionModel | None = None
    stages: list[FlowStage]


class FlowModel(BaseModel):
    version: int
    tracks: dict[str, FlowTrackModel]


class EditorStage(FlowStage):
    locked: bool
    lockReason: str | None = None
    overridden: bool
    overriddenFields: list[str] = []
    default: StageDefault | None = None
    mustFollowLocked: bool


class LockedField(BaseModel):
    field: str
    reason: str


class EditorTrack(BaseModel):
    entry_condition: EntryConditionModel | None = None
    stages: list[EditorStage]
    lockedFields: list[LockedField] = []


class FieldOptions(BaseModel):
    gates: list[GateKind]
    conditions: list[str]
    reservedAgents: list[str] = []  # agent สงวนที่ไม่มีไฟล์ใน .claude/agents (เช่น human ของ gate)


class FlowEditor(BaseModel):
    project: str
    target: str
    hasFrontend: bool | None = None
    base_sha256: str | None = None
    editable: bool = True
    notEditableReason: str | None = None
    exists: bool
    flow: FlowSource
    version: int
    tracks: dict[str, EditorTrack]
    defaultFlow: FlowModel
    issues: list[FlowIssue] = []
    loadError: bool
    policyFix: FlowModel | None = None
    overrides: int
    warnings: list[FlowIssue] = []
    options: FieldOptions


class FlowEditorEnvelope(BaseModel):
    ok: Literal[True]
    data: FlowEditor


class FlowDraftCheck(BaseModel):
    errors: list[FlowIssue]
    warnings: list[FlowIssue]
    ackRequired: list[str]
    overlay: dict[str, Any] | None = None
    overrides: int
    resetsToDefault: bool
    tracks: dict[str, EditorTrack] | None = None


class FlowDraftCheckEnvelope(BaseModel):
    ok: Literal[True]
    data: FlowDraftCheck


class AgentName(BaseModel):
    name: str
    description: str


class AgentNameListEnvelope(BaseModel):
    ok: Literal[True]
    data: list[AgentName]


class SkillName(BaseModel):
    id: str
    description: str


class SkillNameListEnvelope(BaseModel):
    ok: Literal[True]
    data: list[SkillName]
