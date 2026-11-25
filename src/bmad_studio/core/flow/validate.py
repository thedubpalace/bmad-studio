"""Validator ของ flow (REQ-045 a, REQ-046 c, REQ-047 b, AD-1/AD-7/AD-8).

คืนรายการ issue ``{code, error, track?, stage?, field?}`` ภาษาไทย — ว่าง = ผ่าน.
ทุกทางเข้า (revert ใน write pipeline, resolver/CLI, API ใน epic 4) ใช้ฟังก์ชันชุดนี้ที่เดียว.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bmad_studio.core.flow import policy
from bmad_studio.core.flow.loader import FlowYamlError, load_yaml
from bmad_studio.core.flow.schema import Flow, Stage, Track
from bmad_studio.core.path_guard import FLOW_DEFAULT_RULE, FLOW_PROJECT_RULE
from bmad_studio.core.paths import VedaPaths

DEFAULT_TEMPLATE = Path(__file__).resolve().parents[2] / "defaults" / "flow.default.yaml"
issue = policy.issue
MAX_SCHEMA_ISSUES = 50
FlowIssues = list[dict[str, str]]
FileValidator = Callable[[bytes], FlowIssues]


@dataclass(frozen=True)
class Catalog:
    """agent (ไฟล์ ``.claude/agents/<name>.md``) และ skill (โฟลเดอร์ ``.claude/skills/<name>/SKILL.md``) ที่มีจริง.

    ชื่อเทียบแบบตรงตัวพิมพ์ — บน NTFS ``Developer.md`` เปิดได้แต่ไม่ใช่ชื่อที่ policy รู้จัก.
    """

    agents: frozenset[str]
    skills: frozenset[str]

    @classmethod
    def scan(cls, paths: VedaPaths) -> Catalog:
        def names(folder: Path, want_dir: bool) -> frozenset[str]:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                return frozenset()
            out = set()
            for entry in entries:
                try:
                    if want_dir:
                        if entry.is_dir(follow_symlinks=False) and os.path.isfile(os.path.join(entry.path, "SKILL.md")):
                            out.add(entry.name)
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".md"):
                        out.add(entry.name[:-3])
                except OSError:
                    continue
            return frozenset(out)

        return cls(names(paths.agents_dir, False), names(paths.skills_dir, True))


def parse_flow(data: Any) -> tuple[Flow | None, list[dict[str, str]]]:
    """object ของ YAML → :class:`Flow` หรือ issue ``schema_invalid`` ต่อจุด (ไม่เดา)."""
    if not isinstance(data, Mapping):
        return None, [issue("schema_invalid", "flow ต้องเป็น mapping ที่มี version และ tracks")]
    try:
        return Flow.model_validate(dict(data)), []
    except ValidationError as exc:
        return None, schema_issues(exc)


def schema_issues(exc: ValidationError) -> list[dict[str, str]]:
    out = []
    errors = exc.errors()
    for err in errors[:MAX_SCHEMA_ISSUES]:
        loc = ".".join(str(part) for part in err.get("loc", ()))
        out.append(issue("schema_invalid", f"ผิด schema ที่ {loc or '(ราก)'}: {_schema_reason(err)}", field=loc or None))
    if len(errors) > MAX_SCHEMA_ISSUES:
        out.append(issue("schema_invalid",
                         f"และผิด schema อีก {len(errors) - MAX_SCHEMA_ISSUES} จุด (แสดง {MAX_SCHEMA_ISSUES} จุดแรก)"))
    return out


def _schema_reason(err: Mapping[str, Any]) -> str:
    kind = str(err.get("type", ""))
    return {
        "extra_forbidden": "ไม่รู้จัก key นี้",
        "missing": "ขาด field ที่ต้องมี",
        "string_pattern_mismatch": "ต้องเป็นตัวพิมพ์เล็ก ASCII แบบ kebab-case",
        "literal_error": "ค่าไม่อยู่ในรายการที่อนุญาต",
        "bool_type": "ต้องเป็น true/false",
        "string_type": "ต้องเป็นข้อความ",
        "list_type": "ต้องเป็นรายการ",
        "dict_type": "ต้องเป็น mapping",
        "too_long": "ยาว/มากเกินกำหนด",
        "too_short": "ต้องมีอย่างน้อยหนึ่งรายการ",
        "string_too_long": "ยาวเกินกำหนด",
    }.get(kind, "ค่าไม่ถูกต้อง")


def _topo_issues(name: str, track: Track) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[str] = set()
    for stage in track.stages:
        if stage.id in seen:
            issues.append(issue("duplicate_stage", f"มีขั้น id {stage.id} ซ้ำ", track=name, stage=stage.id))
        seen.add(stage.id)
    by_id = {s.id: s for s in track.stages}
    for stage in track.stages:
        if len(set(stage.after)) != len(stage.after):
            issues.append(issue("duplicate_after", f"ขั้น {stage.id}: after มีค่าซ้ำ", track=name, stage=stage.id))
        for dep in stage.after:
            if dep == stage.id:
                issues.append(issue("cycle", f"ขั้น {stage.id} อ้างตัวเองใน after", track=name, stage=stage.id))
            elif dep not in by_id:
                issues.append(issue("unknown_after", f"ขั้น {stage.id}: after อ้างขั้น {dep} ที่ไม่มี",
                                    track=name, stage=stage.id))
    roots = [s for s in track.stages if not s.after]
    if not roots:
        issues.append(issue("no_predecessor", "ไม่มีขั้นเริ่มต้น (ทุกขั้นมี after) — เกิด cycle", track=name))
    for extra in roots[1:]:
        issues.append(issue("no_predecessor", f"ขั้น {extra.id} ไม่มีขั้นก่อนหน้า (มีขั้นเริ่มต้นได้ขั้นเดียว: {roots[0].id})",
                            track=name, stage=extra.id))
    cycle = find_cycle(track.stages)
    if cycle:
        issues.append(issue("cycle", f"ลำดับขั้นวนกลับ: {' → '.join(cycle)}", track=name, stage=cycle[0]))
    elif roots:
        reach = {roots[0].id}
        children: dict[str, list[str]] = {}
        for stage in track.stages:
            for dep in stage.after:
                children.setdefault(dep, []).append(stage.id)
        todo = [roots[0].id]
        while todo:
            for child in children.get(todo.pop(), []):
                if child not in reach:
                    reach.add(child)
                    todo.append(child)
        for stage in track.stages:
            if stage.id not in reach and stage not in roots[1:]:
                issues.append(issue("unreachable", f"ขั้น {stage.id} ไปไม่ถึงจากขั้นเริ่มต้น {roots[0].id}",
                                    track=name, stage=stage.id))
    return issues


def find_cycle(stages: Iterable[Stage]) -> list[str]:
    """คืนเส้นทางของ cycle แรกที่เจอ (ว่าง = ไม่มี) — iterative DFS ไม่ใช้ recursion."""
    by_id: dict[str, Stage] = {}
    for stage in stages:
        by_id.setdefault(stage.id, stage)
    color: dict[str, int] = {}
    for start in by_id:
        if color.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                color[node] = 1
                path.append(node)
            deps = [d for d in by_id[node].after if d in by_id and d != node]
            if index < len(deps):
                stack.append((node, index + 1))
                nxt = deps[index]
                if color.get(nxt) == 1:
                    return path[path.index(nxt):] + [nxt]
                if not color.get(nxt):
                    stack.append((nxt, 0))
            else:
                color[node] = 2
                path.pop()
    return []


def _catalog_issues(name: str, track: Track, catalog: Catalog) -> list[dict[str, str]]:
    issues = []
    for stage in track.stages:
        if stage.agent != policy.HUMAN_AGENT and stage.agent not in catalog.agents:
            issues.append(issue("unknown_agent", f"ขั้น {stage.id}: ไม่มี agent {stage.agent} ใน .claude/agents",
                                track=name, stage=stage.id, field="agent"))
        if stage.skill is not None and stage.skill not in catalog.skills:
            issues.append(issue("unknown_skill", f"ขั้น {stage.id}: ไม่มี skill {stage.skill} ใน .claude/skills",
                                track=name, stage=stage.id, field="skill"))
    return issues


def _plan_gate_issues(name: str, track: Track) -> list[dict[str, str]]:
    """REQ-046: ขั้น developer ต้องตามหลังขั้นที่ produces planReady (architect) ที่ enabled."""
    issues = []
    if name != policy.FULL:
        return issues  # quick เข้าได้เฉพาะโปรเจกต์ที่พัฒนาแล้ว (entry_condition)
    by_id = {s.id: s for s in track.stages}
    for stage in track.stages:
        if not stage.enabled or stage.agent != "developer":
            continue
        before = policy.ancestors(track.stages, stage.id)
        if not any(a in by_id and by_id[a].enabled and "planReady" in by_id[a].produces for a in before):
            issues.append(issue("plan_gate_missing", f"ขั้น {stage.id} (developer) ต้องมาหลังขั้นวางแผนที่ produces planReady",
                                track=name, stage=stage.id))
    return issues


def validate_flow(flow: Flow, catalog: Catalog | None = None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for name, track in flow.tracks.items():
        issues.extend(_topo_issues(name, track))
        if catalog is not None:
            issues.extend(_catalog_issues(name, track, catalog))
        issues.extend(_plan_gate_issues(name, track))
    issues.extend(policy.check(flow))
    return issues


def validate_data(data: Any, catalog: Catalog | None = None) -> tuple[Flow | None, list[dict[str, str]]]:
    flow, issues = parse_flow(data)
    if flow is None:
        return None, issues
    issues = validate_flow(flow, catalog)
    return (flow if not issues else None), issues


def yaml_issue(exc: FlowYamlError) -> dict[str, str]:
    where = f" (บรรทัด {exc.line})" if exc.line else ""
    return {**issue("yaml_invalid", f"{exc.message_th}{where}"), "reason": exc.code}


def validate_flow_bytes(raw: bytes | str, catalog: Catalog | None = None) -> tuple[Flow | None, list[dict[str, str]]]:
    """bytes ของ flow เต็ม (เช่น ``.veda/flow.default.yaml``) → (flow ที่ผ่าน, issues)."""
    try:
        data = load_yaml(raw)
    except FlowYamlError as exc:
        return None, [yaml_issue(exc)]
    return validate_data(data, catalog)


def load_packaged_default() -> Flow:
    """default template ที่มากับแพ็กเกจ — ต้องผ่าน schema และ policy เสมอ (test ยืนยัน)."""
    flow, issues = validate_flow_bytes(DEFAULT_TEMPLATE.read_bytes())
    if flow is None:
        raise RuntimeError(f"packaged default flow ผิดกฎ: {issues}")
    return flow


def file_validators(paths: VedaPaths) -> dict[str, FileValidator]:
    """validator ต่อ rule ของ path guard สำหรับ write pipeline (เช่น revert): bytes → issues.

    ``flow-project`` (overlay) ต้อง merge กับ default ก่อนตรวจ AD-8 — จนกว่าจะมี merge
    (story 3-2) ปฏิเสธไว้ก่อน (fail closed) แทนการผ่านโดยไม่ตรวจ.
    """

    def flow_default(raw: bytes) -> list[dict[str, str]]:
        return validate_flow_bytes(raw, Catalog.scan(paths))[1]

    def flow_project(_raw: bytes) -> list[dict[str, str]]:
        return [issue("flow_overlay_unchecked", "ยังตรวจ flow ต่อโปรเจกต์ไม่ได้ จึงคืนค่าไม่ได้")]

    return {FLOW_DEFAULT_RULE: flow_default, FLOW_PROJECT_RULE: flow_project}
