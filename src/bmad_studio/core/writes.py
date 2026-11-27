"""Write pipeline สองขั้น — ทางเดียวที่เขียนไฟล์ภายนอก (AD-5, REQ-043, REQ-038).

**preview** (:meth:`WritePipeline.preview`)
    kind handler สร้าง proposed bytes จากไฟล์ปัจจุบัน → path guard → ตรวจ ``base_sha256`` →
    diff + errors/warnings → เก็บ preview ใน ``.runtime/previews/`` → ออก ``confirm_token``

**commit** (:meth:`WritePipeline.commit`)
    ตรวจ token (HMAC ผูก preview_id, path, base, proposed, session, expiry) → ผลเดิมถ้า commit แล้ว →
    errors ว่าง → typed confirmation (impact high) → ใต้ lock ของ path: guard ซ้ำ, sha ของไฟล์ปัจจุบัน
    ต้องเท่า base (ไม่งั้น 409) → validator ซ้ำ → ชุดโปรเจกต์ของคำเตือนที่ต้องยืนยันต้องไม่โตเกินที่แสดงใน preview
    (ไม่งั้น 409 ``ux_ack_stale``) → audit → backup bytes → temp + ``os.replace`` → changelog

ไม่ import FastAPI: API ส่ง ``session_fp`` และ callback ``audit`` เข้ามา.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from bmad_studio.adapters import fs
from bmad_studio.adapters.state import RuntimeState, valid_id
from bmad_studio.core.diff import unified
from bmad_studio.core.errors import StudioError
from bmad_studio.core.path_guard import FLOW_RULES, GuardedPath, PathGuard

#: validator ต่อ rule ของ path guard: bytes ที่จะเขียน → issues (ว่าง = ผ่าน)
FileValidator = Callable[[bytes], list[dict[str, str]]]
#: rule ที่ต้องมี validator — ไม่มี = ปฏิเสธการเขียน (fail closed, AD-8/AD-9); ลบไฟล์ของ rule เหล่านี้ต้องมี
#: delete validator (ไม่มี = ปฏิเสธ) — CLAUDE.md ลบผ่านแอปไม่ได้เลย
VALIDATED_RULES = FLOW_RULES | frozenset({"claude-md"})
#: rule ที่ลบได้โดยไม่ต้องมี delete validator (ลบ overlay = กลับไปใช้ default ที่ตรวจแล้ว)
DELETE_WITHOUT_VALIDATOR = frozenset({"flow-project"})

#: คำเตือน key นอกพื้นผิวของ override (story 6-3) — ทุก kind ต้องยืนยันด้วย ``payload.acknowledged``
ACK_OUTSIDE = "override_outside_surface"
ACK_OUTSIDE_UNCONFIRMED = "override_outside_surface_unconfirmed"
TOKEN_TTL_SECONDS = 10 * 60
HISTORY_LIMIT = 500
MAX_PREVIEWS = 200  # preview ที่ยังไม่หมดอายุทั้งระบบ (ผู้ใช้เดียว); เกิน = 429 ไม่ไล่ของเดิมทิ้ง
HIGH_IMPACT_RULES = frozenset({"agent", "agent-backup", "claude-md", "flow-default", "flow-project"})
_TOKEN_CONTEXT = b"bmad-studio confirm-token v1"


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ChangeRequest:
    kind: str
    target: str
    payload: Mapping[str, Any]
    base_sha256: str | None


@dataclass
class Proposal:
    """``proposed`` = bytes ใหม่ทั้งไฟล์ หรือ ``None`` = ย้ายไฟล์ไป backup (ลบออกจากที่เดิม)."""

    proposed: bytes | None
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # ต่อท้าย changelog (เช่น ``reverts``)
    #: ข้อมูลแสดงผลของ preview เท่านั้น (เช่น effective หลังบันทึกจาก resolve script) — ไม่เก็บ ไม่ลงนาม
    view: dict[str, Any] | None = None


class KindHandler(Protocol):
    name: str
    rules: frozenset[str]
    #: ตรวจ EOL/whitespace ทั้งไฟล์ (AD-6) — revert คืน bytes ตรง ๆ จึงปิด
    check_whitespace: bool

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal: ...


class TextKind:
    """kind สำหรับ test/dev: แทนเนื้อหาทั้งไฟล์ด้วย ``payload.text``.

    ปิดเป็นค่าเริ่มต้น (``BMAD_STUDIO_ENABLE_TEXT_KIND``) และรับเฉพาะ agent/``_bmad/custom`` —
    ไม่รับ flow/CLAUDE.md เพื่อไม่ให้ข้าม validator ของ AD-8/AD-9.
    """

    name = "text"
    rules = frozenset({"agent", "bmad-custom"})
    check_whitespace = True

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        text = payload.get("text")
        if not isinstance(text, str):
            raise StudioError("invalid_request", "ต้องส่ง payload.text เป็นข้อความ", 422)
        if len(text) > fs.MAX_BYTES or len(text.encode("utf-8", "surrogatepass")) > fs.MAX_BYTES:
            raise StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413)
        return Proposal(current.encode(text))


class RevertKind:
    """คืนไฟล์เป็น bytes ใน backup ของรายการ ``payload.change_id`` (REQ-043 b, AD-6).

    - เดิมมีไฟล์: proposed = ``original.bin`` ตรง ๆ (ไม่ผ่าน text) → byte-for-byte
    - เดิมไม่มีไฟล์ (รายการนั้นสร้างไฟล์): ย้ายไฟล์ปัจจุบันไป backup ของรายการใหม่
    - ไฟล์ถูกแก้ต่อหลังรายการนั้น: ``warnings[]`` (ยังคืนค่าได้)
    impact เท่ากับไฟล์เป้าหมาย (AD-5). bytes ที่จะคืนผ่าน validator ต่อ rule ของ :class:`WritePipeline`
    เหมือนทุก kind — ฉบับสำรองอาจเป็นฉบับที่ถูกแก้มือให้ข้าม gate.
    """

    name = "revert"
    #: diff ใหญ่เกินแสดงได้ยังคืนค่าได้ — ผลคือฉบับที่ระบบเคยเห็น/สำรองเอง และคืนกลับได้อีกจากประวัติ
    allow_truncated = True
    rules = frozenset({"agent", "agent-backup", "claude-md", "bmad-custom", "flow-default", "flow-project"})
    check_whitespace = False

    def __init__(self, state: RuntimeState) -> None:
        self.state = state

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        change_id = payload.get("change_id")
        if not valid_id(change_id):
            raise StudioError("invalid_request", "ต้องส่ง payload.change_id ที่ถูกต้อง", 422)
        entry = next((item for item in self.state.read_changes() if item.get("change_id") == change_id), None)
        if entry is None:
            raise StudioError("change_not_found", "ไม่พบรายการนี้ในประวัติ", 404)
        if entry.get("path") != target.rel:
            raise StudioError("revert_target_mismatch", "รายการนี้ไม่ใช่ของไฟล์ที่ระบุ", 422)
        backup = self.state.read_backup(change_id)
        if backup is None:
            raise StudioError("backup_missing", "ไม่พบไฟล์สำรองของรายการนี้", 410)
        meta, original = backup
        existed = bool(meta.get("existed"))
        if meta.get("path") != target.rel or (existed and original is None):
            raise StudioError("backup_missing", "ไม่พบไฟล์สำรองของรายการนี้", 410)
        if existed and _sha(original) != meta.get("sha256"):
            raise StudioError("backup_corrupt", "ไฟล์สำรองเสียหาย คืนค่าไม่ได้", 422)
        # backup ต้องตรงกับ changelog (ทั้งมี/ไม่มีไฟล์เดิม และ hash ของฉบับเดิม) — กันการสลับโฟลเดอร์สำรอง
        if existed != (entry.get("base_sha256") is not None) or _sha(original) != entry.get("base_sha256"):
            raise StudioError("backup_corrupt", "ไฟล์สำรองไม่ตรงกับประวัติ คืนค่าไม่ได้", 422)
        warnings: list[dict[str, str]] = []
        if current.sha256 != entry.get("new_sha256"):
            warnings.append({"code": "changed_since", "error": "ไฟล์ถูกแก้ต่อหลังรายการนี้ — การคืนค่าจะทับการแก้เหล่านั้นด้วย"})
        errors: list[dict[str, str]] = []
        extra = {"reverts": change_id}
        if not existed:
            if not current.exists:
                errors.append({"code": "no_change", "error": "ไฟล์นี้ไม่มีอยู่แล้ว ไม่มีอะไรต้องคืนค่า"})
            return Proposal(None, warnings, errors, extra)
        if current.exists and current.raw == original:
            errors.append({"code": "no_change", "error": "ไฟล์ตรงกับฉบับสำรองอยู่แล้ว"})
        return Proposal(original, warnings, errors, extra)


def impact_for(rule: str) -> str:
    return "high" if rule in HIGH_IMPACT_RULES else "normal"


def confirm_phrase(guarded: GuardedPath) -> str:
    """สิ่งที่ผู้ใช้ต้องพิมพ์ยืนยันเมื่อ impact high: ชื่อ agent (ไม่มี .md) หรือชื่อไฟล์."""
    name = guarded.rel.rsplit("/", 1)[-1]
    if guarded.rule == "agent" and name.lower().endswith(".md"):
        return name[:-3]
    return name


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _dedupe(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    """ตัด issue ที่ซ้ำทุก field (kind กับ file validator อาจตรวจกฎเดียวกันซ้ำ) — คงลำดับเดิม."""
    seen: set[str] = set()
    out = []
    for item in issues:
        key = json.dumps(item, sort_keys=True, ensure_ascii=True)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _whitespace_errors(current: fs.TextDoc, proposed: bytes) -> list[dict[str, str]]:
    """ปฏิเสธ proposed ที่ไม่เปลี่ยน, ต่างเฉพาะ EOL/BOM, หรือต่างเฉพาะ whitespace ทั้งไฟล์ (AD-6)."""
    if current.exists and proposed == current.raw:
        return [{"code": "no_change", "error": "ไม่มีอะไรเปลี่ยน"}]
    try:
        new_doc = fs.decode(proposed)
    except StudioError:
        return [{"code": "unsupported_encoding", "error": "ผลลัพธ์ไม่ใช่ข้อความ UTF-8"}]
    if not current.exists:
        return []
    if new_doc.text == current.text:
        return [{"code": "eol_only_change", "error": "การแก้นี้เปลี่ยนแค่ line ending/BOM ของทั้งไฟล์"}]
    old_lines, new_lines = current.text.split("\n"), new_doc.text.split("\n")
    squash = lambda line: "".join(line.split())  # noqa: E731
    if len(old_lines) == len(new_lines) and len(old_lines) > 2:
        changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        if changed and all(squash(a) == squash(b) for a, b in changed) and len(changed) * 2 > len(old_lines):
            return [{"code": "whitespace_only_change", "error": "การแก้นี้เปลี่ยนแค่ช่องว่าง/ระยะเยื้องเกือบทั้งไฟล์"}]
    return []


class WritePipeline:
    def __init__(self, guard: PathGuard, state: RuntimeState, secret: str,
                 kinds: Mapping[str, KindHandler], *, clock: Callable[[], float] = time.time,
                 max_bytes: int = fs.MAX_BYTES, validators: Mapping[str, FileValidator] | None = None,
                 delete_validators: Mapping[str, Callable[[], list[dict[str, str]]]] | None = None,
                 advisories: Mapping[str, Callable[[str, bytes | None], list[dict[str, str]]]] | None = None,
                 impact_rules: Mapping[str, Callable[[str, bytes | None, bytes | None],
                                                     tuple[str, list[dict[str, Any]]]]] | None = None,
                 path_validators: Mapping[str, Callable[[str, bytes], list[dict[str, Any]]]] | None = None) -> None:
        if len(secret) < 32:
            raise ValueError("secret ต้องยาวอย่างน้อย 32 ตัวอักษร")
        self.guard = guard
        self.state = state
        self.kinds = dict(kinds)
        self.validators = dict(validators or {})
        #: ตรวจการลบต่อ rule (เช่น ลบ flow เริ่มต้น = กลับไปใช้ packaged default ต้องตรวจทุกโปรเจกต์ — AD-7)
        self.delete_validators = dict(delete_validators or {})
        #: คำเตือนต่อ rule ที่ทุก kind ได้ (รวม revert/ลบ) — เช่น ปิดขั้นออกแบบ UI ของโปรเจกต์ที่มี frontend
        self.advisories = dict(advisories or {})
        #: impact ตามเนื้อหาต่อ rule (ทุก kind/op รวม revert/ลบ): (rel, bytes เดิม|None, bytes ใหม่|None) → (impact, คำเตือน)
        #: ยกระดับได้อย่างเดียว (normal → high) และตรวจซ้ำตอน commit ใต้ lock (เช่น ข้อความที่ BMAD ส่งเข้า prompt)
        self.impact_rules = dict(impact_rules or {})
        #: validator ที่ต้องรู้ path (rel, bytes) — รันหลัง validator ปกติผ่าน ทั้ง preview และ commit ใต้ lock
        self.path_validators = dict(path_validators or {})
        self.clock = clock
        self.max_bytes = max_bytes
        self._key = hmac.new(secret.encode(), _TOKEN_CONTEXT, hashlib.sha256).digest()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- token ---------------------------------------------------------------
    def _sign(self, meta: Mapping[str, Any], session_fp: str, expiry: int) -> str:
        """ลายเซ็นผูกทุกอย่างที่ commit ใช้ตัดสิน — แก้ไฟล์ preview บนดิสก์แล้ว token ใช้ไม่ได้."""
        issues = json.dumps([meta["errors"], meta["warnings"], meta.get("extra", {}), meta.get("ack_scope", [])],
                            sort_keys=True, ensure_ascii=True)
        message = "|".join([
            meta["preview_id"], meta["rel"], meta["base_sha256"] or "-", meta["proposed_sha256"] or "-", session_fp,
            str(expiry), meta["kind"], meta["op"], meta["impact"], meta["confirm_phrase"],
            hashlib.sha256(issues.encode()).hexdigest(),
        ])
        return hmac.new(self._key, message.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()

    def _token(self, meta: Mapping[str, Any], session_fp: str) -> str:
        expiry = int(meta["expiry"])
        return f"{expiry}.{self._sign(meta, session_fp, expiry)}"

    def _check_token(self, meta: Mapping[str, Any], token: object, session_fp: str) -> None:
        bad = StudioError("confirm_invalid", "การยืนยันไม่ถูกต้อง เปิดตัวอย่างการแก้ใหม่", 403)
        if not isinstance(token, str) or len(token) > 200:
            raise bad
        try:
            expiry_raw, supplied = token.split(".", 1)
            expiry = int(expiry_raw)
            expected = self._sign(meta, session_fp, expiry)
        except (ValueError, KeyError, TypeError):
            raise bad from None
        if expiry != int(meta.get("expiry", -1)):
            raise bad
        if not hmac.compare_digest(supplied.encode("utf-8", "surrogateescape"), expected.encode()):
            raise bad
        if expiry < self.clock():
            raise StudioError("confirm_expired", "การยืนยันหมดอายุ เปิดตัวอย่างการแก้ใหม่", 403)

    def _validate(self, rule: str, proposed: bytes, rel: str = "") -> list[dict[str, str]]:
        """validator ของ rule นี้ (ทุก kind รวม revert); rule ใน VALIDATED_RULES ที่ไม่มี validator = ปฏิเสธ.
        ``path_validators`` (ต้องรู้ว่าเป็นไฟล์ไหน เช่น รัน resolve script ของ BMAD กับไฟล์นั้น) รันต่อจาก validator ปกติ."""
        issues: list[dict[str, str]] = []
        by_path = self.path_validators.get(rule)
        validator = self.validators.get(rule)
        if validator is not None:
            issues = list(validator(proposed))
            if by_path is not None and not issues:
                issues = list(by_path(rel, proposed))
            return issues
        if rule in VALIDATED_RULES:
            return [{"code": "validator_missing", "error": "ตรวจไฟล์นี้ไม่ได้ จึงบันทึกไม่ได้"}]
        return []

    def _validate_delete(self, rule: str) -> list[dict[str, str]]:
        """การลบ (ย้ายไป backup) ของ rule นี้ — rule ที่ต้องตรวจแต่ไม่มี delete validator = ปฏิเสธ (fail closed)."""
        check = self.delete_validators.get(rule)
        if check is not None:
            return list(check())
        if rule in VALIDATED_RULES and rule not in DELETE_WITHOUT_VALIDATOR:
            return [{"code": "delete_forbidden", "error": "ลบไฟล์นี้ผ่านแอปไม่ได้"}]
        return []

    @staticmethod
    def _ack_scope(advice: list[dict[str, Any]]) -> list[str]:
        """ชุดที่การยืนยันครอบคลุม: โปรเจกต์ที่ ux จะถูกปิด (A3) + key นอก surface ของ override (``outside:<key>``,
        review 6-3 N1/N2) — commit ใต้ lock ปฏิเสธถ้าชุดโตกว่าที่แสดงใน preview."""
        from bmad_studio.core.flow.ux_guard import ack_scope

        outside = sorted({f"outside:{k}" for w in advice if w.get("code") == ACK_OUTSIDE for k in (w.get("keys") or [])})
        return ack_scope(advice) + outside

    @staticmethod
    def _acknowledged(request: ChangeRequest) -> list[str]:
        value = request.payload.get("acknowledged") if isinstance(request.payload, Mapping) else None
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 8 or not all(isinstance(v, str) and len(v) <= 64 for v in value):
            raise StudioError("invalid_request", "payload.acknowledged ต้องเป็นรายการ code ของคำเตือน", 422)
        return value

    def _impact(self, guarded: GuardedPath, current: bytes | None,
                proposed: bytes | None) -> tuple[str, list[dict[str, Any]]]:
        impact = impact_for(guarded.rule)
        rule = self.impact_rules.get(guarded.rule)
        if rule is None:
            return impact, []
        level, warnings = rule(guarded.rel, current, proposed)
        return ("high" if "high" in (impact, level) else "normal"), list(warnings)

    def _lock(self, path: GuardedPath) -> threading.Lock:
        # ไฟล์ flow ทุกไฟล์ใช้ lock เดียว: overlay ถูกตรวจกับ default ปัจจุบัน — default ต้องไม่เปลี่ยนระหว่างตรวจกับเขียน
        key = "flow-files" if path.rule in FLOW_RULES else os.path.normcase(os.fspath(path.path))
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # -- preview -------------------------------------------------------------
    def preview(self, request: ChangeRequest, session_fp: str, actor: str) -> dict[str, Any]:
        handler = self.kinds.get(request.kind)
        if handler is None:
            raise StudioError("unknown_kind", "ไม่รู้จักชนิดการแก้ไขนี้", 422)
        guarded = self.guard.check_writable(request.target)
        if guarded.rule not in handler.rules:
            raise StudioError("path_forbidden", "ไม่อนุญาตให้เขียนไฟล์นี้", 403, {"reason": "kind_rule"})
        check_target = getattr(handler, "check_target", None)
        if check_target is not None:  # kind ที่เขียนได้เฉพาะบางไฟล์ของ rule — ตรวจก่อนอ่านไฟล์/เทียบ base
            check_target(guarded)
        # ตรวจที่ถูกก่อนงานแพง (propose/diff): จำนวน preview ค้าง
        self.state.sweep_previews(TOKEN_TTL_SECONDS * 2, self.clock())
        if self.state.count_previews() >= MAX_PREVIEWS:
            raise StudioError("too_many_previews", "มีตัวอย่างการแก้ค้างมากเกินไป รอสักครู่แล้วลองใหม่", 429)
        current = fs.read_doc(guarded.path, self.max_bytes)
        if request.base_sha256 != current.sha256:
            raise StudioError("conflict", "ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้ โหลดใหม่ก่อน", 409,
                              {"current_sha256": current.sha256})
        proposal = handler.propose(guarded, current, request.payload)
        delete = proposal.proposed is None
        if not delete and len(proposal.proposed) > self.max_bytes:
            raise StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413)
        errors = list(proposal.errors)
        if delete and not current.exists and not errors:
            errors.append({"code": "no_change", "error": "ไม่มีไฟล์ให้ย้าย"})
        if not delete and not os.path.isdir(os.path.dirname(os.fspath(guarded.path))):
            errors.append({"code": "parent_missing", "error": "ไม่มีโฟลเดอร์ปลายทาง"})
        if not delete and getattr(handler, "check_whitespace", True):
            errors.extend(_whitespace_errors(current, proposal.proposed))
        if not delete:
            errors.extend(self._validate(guarded.rule, proposal.proposed, guarded.rel))
        else:
            errors.extend(self._validate_delete(guarded.rule))
        try:
            diff = unified(current.text, "" if delete else fs.decode(proposal.proposed).text)
        except StudioError:  # ผลลัพธ์ไม่ใช่ข้อความ — ไม่แสดง diff ที่ทำให้เข้าใจผิด
            diff = {"hunks": [], "added": 0, "removed": 0, "truncated": False,
                    "old_eof_newline": True, "new_eof_newline": True}
            if not errors:
                errors.append({"code": "unsupported_encoding", "error": "ผลลัพธ์ไม่ใช่ข้อความ UTF-8"})
        advisory = self.advisories.get(guarded.rule)
        advice = list(advisory(guarded.rel, proposal.proposed)) if advisory is not None else []
        impact, impact_warnings = self._impact(guarded, current.raw if current.exists else None, proposal.proposed)
        extra = dict(proposal.extra)
        if any(w.get("code") == ACK_OUTSIDE for w in advice):
            # key นอก surface ต้องยืนยันทุก kind (override, text, revert) — ไม่ใช่แค่ kind ที่ตรวจเอง (review 6-3 N1)
            acknowledged = list(extra.get("acknowledged") or self._acknowledged(request))
            extra["acknowledged"] = acknowledged
            if ACK_OUTSIDE not in acknowledged:
                errors.append({"code": ACK_OUTSIDE_UNCONFIRMED,
                               "error": "มี key นอกพื้นผิวที่ปรับได้ (customize.toml) — ยืนยันคำเตือนก่อนบันทึก"})
        errors = _dedupe(errors)
        warnings = _dedupe(list(proposal.warnings) + advice + impact_warnings)
        if diff.get("truncated"):
            if getattr(handler, "allow_truncated", False):
                warnings.append({"code": "diff_summary_only",
                                 "error": "diff ใหญ่เกินจะแสดงรายบรรทัด — การคืนค่านี้ย้อนกลับได้จากประวัติ"})
            else:
                # ผู้ใช้ต้องเห็นสิ่งที่จะเขียน (AD-5) — diff ที่ใหญ่เกินแสดงไม่ได้จึงบันทึกไม่ได้
                errors.append({"code": "diff_too_large",
                               "error": "การเปลี่ยนแปลงใหญ่เกินกว่าจะแสดงให้ตรวจได้ — แบ่งแก้เป็นส่วนเล็กลง"})
        now = self.clock()
        preview_id = uuid.uuid4().hex
        expiry = int(now) + TOKEN_TTL_SECONDS
        meta: dict[str, Any] = {
            "preview_id": preview_id, "kind": request.kind, "rel": guarded.rel, "rule": guarded.rule,
            "op": "delete" if delete else "modify" if current.exists else "create",
            "base_sha256": current.sha256, "proposed_sha256": _sha(proposal.proposed),
            "extra": extra,
            "impact": impact, "confirm_phrase": confirm_phrase(guarded),
            "errors": errors, "warnings": warnings, "added": diff["added"], "removed": diff["removed"],
            # ชุดโปรเจกต์ที่คำเตือน ux ใน preview นี้แสดง — การยืนยันครอบคลุมเฉพาะชุดนี้ (retro epic-4 A3)
            "ack_scope": self._ack_scope(advice) if advisory is not None else [],
            "expiry": expiry, "actor": actor, "created_at": utc_iso(now),
        }
        if self.state.count_previews() >= MAX_PREVIEWS:  # ตรวจซ้ำหลังงานแพง (คำขอพร้อมกัน)
            raise StudioError("too_many_previews", "มีตัวอย่างการแก้ค้างมากเกินไป รอสักครู่แล้วลองใหม่", 429)
        self.state.save_preview(preview_id, meta, b"" if delete else proposal.proposed)
        return {
            "preview_id": preview_id, "path": guarded.rel, "kind": request.kind, "op": meta["op"],
            "impact": meta["impact"], "confirm_phrase": meta["confirm_phrase"] if meta["impact"] == "high" else None,
            "base_sha256": current.sha256, "proposed_sha256": meta["proposed_sha256"],
            "diff": diff, "warnings": warnings, "errors": errors,
            "confirm_token": self._token(meta, session_fp), "expires_at": utc_iso(expiry),
            "view": proposal.view,
        }

    # -- commit --------------------------------------------------------------
    def commit(self, preview_id: str, confirm_token: object, session_fp: str, actor: str,
               typed_confirmation: str | None = None,
               audit: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        invalid = StudioError("confirm_invalid", "การยืนยันไม่ถูกต้อง เปิดตัวอย่างการแก้ใหม่", 403)
        if not valid_id(preview_id):
            raise invalid
        loaded = self.state.load_preview(preview_id)
        if loaded is None:
            raise invalid
        meta, proposed = loaded
        self._check_token(meta, confirm_token, session_fp)
        done = self.state.load_result(preview_id)
        if done is not None:
            return {**done, "replayed": True}
        if meta.get("errors"):
            raise StudioError("has_errors", "แก้ปัญหาที่แจ้งก่อนจึงจะบันทึกได้", 422, {"errors": meta["errors"]})
        if meta.get("impact") == "high":
            if not isinstance(typed_confirmation, str) or typed_confirmation.strip() != meta.get("confirm_phrase"):
                raise StudioError("confirmation_required", "พิมพ์ชื่อไฟล์/agent ให้ตรงเพื่อยืนยัน", 400)
        delete = meta.get("op") == "delete"
        if delete:
            if proposed != b"" or meta.get("proposed_sha256") is not None:
                raise invalid
        elif _sha(proposed) != meta.get("proposed_sha256"):
            raise invalid  # ไฟล์ preview บนดิสก์ถูกแก้ — ไม่เขียนสิ่งที่ผู้ใช้ไม่ได้เห็น

        guarded = self.guard.check_writable(meta["rel"])
        with self._lock(guarded):
            done = self.state.load_result(preview_id)  # commit พร้อมกันด้วย preview เดียวกัน
            if done is not None:
                return {**done, "replayed": True}
            guarded = self.guard.check_writable(meta["rel"])  # TOCTOU: ตรวจซ้ำใต้ lock
            original = fs.read_bytes(guarded.path, self.max_bytes)
            if _sha(original) != meta.get("base_sha256"):
                raise StudioError("conflict", "ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้ โหลดใหม่ก่อน", 409,
                                  {"current_sha256": _sha(original)})
            if not os.path.isdir(os.path.dirname(os.fspath(guarded.path))):
                raise StudioError("parent_missing", "ไม่มีโฟลเดอร์ปลายทาง", 422)
            # ตรวจซ้ำใต้ lock: default/catalog/overlay ของโปรเจกต์อาจเปลี่ยนระหว่าง preview กับ commit (TTL 10 นาที).
            # การลบตรวจด้วย delete validator ของ rule (ลบ flow เริ่มต้น); ลบ overlay = กลับไปใช้ default ที่ตรวจแล้ว
            if not delete:
                late = self._validate(guarded.rule, proposed, guarded.rel)
            else:
                late = self._validate_delete(guarded.rule)
            extra = meta.get("extra") or {}
            if not late and guarded.rule in self.advisories:
                # ทุก kind/op ของ rule ที่มีคำเตือน (flow, flow-default, revert, ลบ): default/overlay/registry อาจเปลี่ยน
                # หลัง preview จนปิด ux ของโปรเจกต์ที่ผู้ใช้ไม่ได้เห็น — ตรวจซ้ำใต้ lock เหมือน validator
                from bmad_studio.core.flow.ux_guard import ack_errors, scope_grew

                advice = self.advisories[guarded.rule](guarded.rel, None if delete else proposed)
                grown = scope_grew(list(meta.get("ack_scope") or []), self._ack_scope(advice))
                grown_keys = [g[len("outside:"):] for g in grown if g.startswith("outside:")]
                if grown_keys:
                    raise StudioError(
                        "ack_stale",
                        "key นอกพื้นผิวที่ปรับได้เปลี่ยนไปหลังตัวอย่าง (customize.toml ถูกแก้) — ไม่มีอะไรถูกเขียน "
                        "เปิดตัวอย่างใหม่แล้วยืนยันอีกครั้ง", 409, {"keys": grown_keys})
                if grown:
                    raise StudioError(
                        "ux_ack_stale",
                        "รายชื่อโปรเจกต์ที่การแก้นี้จะปิดขั้นออกแบบ UI เปลี่ยนไปหลังตัวอย่าง — ไม่มีอะไรถูกเขียน "
                        "เปิดตัวอย่างใหม่แล้วยืนยันอีกครั้ง", 409, {"projects": grown})
                acknowledged = list(extra.get("acknowledged") or [])
                if "acknowledged" in extra:
                    late = ack_errors(advice, acknowledged)
                if any(w.get("code") == ACK_OUTSIDE for w in advice) and ACK_OUTSIDE not in acknowledged:
                    late = list(late) + [{"code": ACK_OUTSIDE_UNCONFIRMED,
                                          "error": "มี key นอกพื้นผิวที่ปรับได้ (customize.toml เปลี่ยนหลังตัวอย่าง) — เปิดตัวอย่างใหม่"}]
            check_target = getattr(self.kinds.get(str(meta.get("kind"))), "check_target", None)
            if check_target is not None:
                check_target(guarded)  # เป้าของ kind ยังใช้ได้ (เช่น skill ยังมี customize.toml) — review 6-3 N3
            if not late and meta.get("impact") != "high" and self._impact(guarded, original, None if delete else proposed)[0] == "high":
                # impact ตามเนื้อหาต้องไม่สูงกว่าที่ผู้ใช้ยืนยันไว้ (กันข้าม typed confirmation)
                raise StudioError("impact_changed", "การแก้นี้ต้องยืนยันแบบพิมพ์ชื่อไฟล์ — เปิดตัวอย่างใหม่", 409)
            if late:
                raise StudioError("has_errors", "ไฟล์นี้ไม่ผ่านการตรวจแล้ว (มีการเปลี่ยนแปลงหลังตัวอย่าง) — เปิดตัวอย่างใหม่",
                                  422, {"errors": late})
            change_id = uuid.uuid4().hex
            now = self.clock()
            record = {
                "change_id": change_id, "preview_id": preview_id, "at": utc_iso(now), "actor": actor,
                "kind": meta["kind"], "op": meta["op"], "path": meta["rel"], "impact": meta["impact"],
                "base_sha256": meta["base_sha256"], "new_sha256": meta["proposed_sha256"],
                "added": meta.get("added", 0), "removed": meta.get("removed", 0),
                "backup": f".runtime/backups/{change_id}",
                **{key: value for key, value in (meta.get("extra") or {}).items() if key == "reverts"},
            }
            if audit is not None:
                try:
                    audit({"event": "write", "phase": "requested", **record})
                except OSError:
                    raise StudioError("audit_unavailable", "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง", 503) from None

            def outcome(phase: str, **extra: Any) -> None:
                if audit is None:
                    return
                try:
                    audit({"event": "write", "phase": phase, "change_id": change_id, "path": meta["rel"], **extra})
                except OSError:
                    pass  # ผลจริงอยู่ใน changelog/response; บันทึกผลเป็น best effort

            try:
                self.state.write_backup(change_id, original, {
                    "change_id": change_id, "path": meta["rel"], "existed": original is not None,
                    "sha256": _sha(original), "new_sha256": meta["proposed_sha256"], "at": record["at"],
                }, new=None if delete else proposed)
            except OSError:
                outcome("failed", step="backup")
                raise StudioError("write_failed", "สำรองไฟล์เดิมไม่ได้ ไม่มีอะไรถูกเปลี่ยน", 503,
                                  {"step": "backup"}) from None
            try:
                if delete:
                    fs.remove_file(guarded.path)  # ฉบับเดิมอยู่ใน backup ของรายการนี้แล้ว
                else:
                    fs.write_bytes_atomic(guarded.path, proposed)
            except OSError:
                outcome("failed", step="write")
                raise StudioError("write_failed", "เขียนไฟล์ไม่ได้ ไม่มีอะไรถูกเปลี่ยน", 503, {"step": "write"}) from None
            try:
                self.state.append_change(record)
            except OSError:
                if self._restore(guarded, original):
                    outcome("rolled_back", step="changelog")
                    raise StudioError("write_failed", "บันทึกประวัติไม่ได้ คืนไฟล์เดิมแล้ว", 503,
                                      {"step": "changelog"}) from None
                outcome("restore_failed", step="changelog")
                raise StudioError("restore_failed",
                                  "บันทึกประวัติไม่ได้และคืนไฟล์เดิมไม่สำเร็จ — ไฟล์เดิมอยู่ในสำรองตาม change_id", 500,
                                  {"change_id": change_id}) from None
            outcome("committed", sha256=meta["proposed_sha256"])
            result = {"change_id": change_id, "path": meta["rel"], "op": meta["op"],
                      "sha256": meta["proposed_sha256"], "backup": record["backup"], "at": record["at"]}
            try:
                self.state.save_result(preview_id, result)
            except OSError:
                pass  # เขียนแล้วและอยู่ใน changelog; commit ซ้ำจะได้ 409 แทนผลเดิม (ไม่เขียนทับ)
            return {**result, "replayed": False}

    def _restore(self, guarded: GuardedPath, original: bytes | None) -> bool:
        try:
            if original is None:
                fs.remove_file(guarded.path)
            else:
                fs.write_bytes_atomic(guarded.path, original)
        except OSError:
            return False
        return True

    # -- history (read-only) -------------------------------------------------
    def history(self, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
        """รายการใน changelog ล่าสุดก่อน (ไม่เกิน ``limit``) พร้อม sha ปัจจุบันของไฟล์ (base ของ revert)."""
        limit = max(1, min(int(limit), HISTORY_LIMIT))
        entries = list(reversed(self.state.read_changes()))[:limit]
        current: dict[str, str | None] = {}
        out: list[dict[str, Any]] = []
        for entry in entries:
            rel = entry.get("path")
            if not isinstance(rel, str) or not valid_id(entry.get("change_id")):
                continue
            if rel not in current:
                try:
                    guarded = self.guard.check_writable(rel)
                    current[rel] = _sha(fs.read_bytes(guarded.path, self.max_bytes))
                except (StudioError, OSError):
                    current[rel] = None
            out.append({
                "change_id": entry["change_id"], "at": entry.get("at", ""), "path": rel,
                "kind": str(entry.get("kind", "")),
                "op": entry.get("op") if entry.get("op") in {"create", "modify", "delete"} else "modify",
                "added": _count(entry.get("added")), "removed": _count(entry.get("removed")),
                "reverts": entry.get("reverts") if valid_id(entry.get("reverts")) else None,
                "impact": "high" if entry.get("impact") == "high" else "normal",
                "new_sha256": entry.get("new_sha256"), "current_sha256": current[rel],
                "changed_since": current[rel] != entry.get("new_sha256"),
            })
        return out

    def change_diff(self, change_id: str) -> dict[str, Any]:
        """diff ของรายการหนึ่ง (ฉบับก่อน → ฉบับที่เขียน) จาก backup สำหรับโหมดอ่าน."""
        if not valid_id(change_id):
            raise StudioError("change_not_found", "ไม่พบรายการนี้ในประวัติ", 404)
        entry = next((e for e in self.state.read_changes() if e.get("change_id") == change_id), None)
        backup = self.state.read_backup(change_id)
        if entry is None or backup is None:
            raise StudioError("change_not_found", "ไม่พบรายการนี้ในประวัติ", 404)
        meta, original = backup
        new = self.state.read_backup_new(change_id)
        op = entry.get("op", "modify")
        missing = StudioError("diff_unavailable", "ไม่มีข้อมูลพอสำหรับแสดง diff ของรายการนี้", 410)
        if meta.get("path") != entry.get("path"):
            raise missing
        if bool(meta.get("existed")) and (original is None or _sha(original) != meta.get("sha256")):
            raise missing
        if op != "delete" and (new is None or _sha(new) != entry.get("new_sha256")):
            raise missing  # รายการก่อน story 2-4 ไม่มี new.bin — ไม่แสดงเป็น "ลบทั้งไฟล์" ให้เข้าใจผิด
        try:
            old_text = fs.decode(original).text if original is not None else ""
            new_text = fs.decode(new).text if new is not None else ""
        except StudioError:
            raise StudioError("unsupported_encoding", "แสดง diff ของรายการนี้ไม่ได้", 422) from None
        return {"change_id": change_id, "path": entry.get("path"), "op": entry.get("op", "modify"),
                "kind": entry.get("kind", ""), "at": entry.get("at", ""), "diff": unified(old_text, new_text)}
