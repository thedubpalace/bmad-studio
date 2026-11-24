"""Write pipeline สองขั้น — ทางเดียวที่เขียนไฟล์ภายนอก (AD-5, REQ-043, REQ-038).

**preview** (:meth:`WritePipeline.preview`)
    kind handler สร้าง proposed bytes จากไฟล์ปัจจุบัน → path guard → ตรวจ ``base_sha256`` →
    diff + errors/warnings → เก็บ preview ใน ``.runtime/previews/`` → ออก ``confirm_token``

**commit** (:meth:`WritePipeline.commit`)
    ตรวจ token (HMAC ผูก preview_id, path, base, proposed, session, expiry) → ผลเดิมถ้า commit แล้ว →
    errors ว่าง → typed confirmation (impact high) → ใต้ lock ของ path: guard ซ้ำ, sha ของไฟล์ปัจจุบัน
    ต้องเท่า base (ไม่งั้น 409) → audit → backup bytes → temp + ``os.replace`` → changelog

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
from bmad_studio.core.path_guard import GuardedPath, PathGuard

TOKEN_TTL_SECONDS = 10 * 60
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
    proposed: bytes
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


class KindHandler(Protocol):
    name: str
    rules: frozenset[str]

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal: ...


class TextKind:
    """kind สำหรับ test/dev: แทนเนื้อหาทั้งไฟล์ด้วย ``payload.text``.

    ปิดเป็นค่าเริ่มต้น (``BMAD_STUDIO_ENABLE_TEXT_KIND``) และรับเฉพาะ agent/``_bmad/custom`` —
    ไม่รับ flow/CLAUDE.md เพื่อไม่ให้ข้าม validator ของ AD-8/AD-9.
    """

    name = "text"
    rules = frozenset({"agent", "bmad-custom"})

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        text = payload.get("text")
        if not isinstance(text, str):
            raise StudioError("invalid_request", "ต้องส่ง payload.text เป็นข้อความ", 422)
        if len(text) > fs.MAX_BYTES or len(text.encode("utf-8", "surrogatepass")) > fs.MAX_BYTES:
            raise StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413)
        return Proposal(current.encode(text))


def impact_for(rule: str) -> str:
    return "high" if rule in HIGH_IMPACT_RULES else "normal"


def confirm_phrase(guarded: GuardedPath) -> str:
    """สิ่งที่ผู้ใช้ต้องพิมพ์ยืนยันเมื่อ impact high: ชื่อ agent (ไม่มี .md) หรือชื่อไฟล์."""
    name = guarded.rel.rsplit("/", 1)[-1]
    if guarded.rule == "agent" and name.lower().endswith(".md"):
        return name[:-3]
    return name


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


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
                 max_bytes: int = fs.MAX_BYTES) -> None:
        if len(secret) < 32:
            raise ValueError("secret ต้องยาวอย่างน้อย 32 ตัวอักษร")
        self.guard = guard
        self.state = state
        self.kinds = dict(kinds)
        self.clock = clock
        self.max_bytes = max_bytes
        self._key = hmac.new(secret.encode(), _TOKEN_CONTEXT, hashlib.sha256).digest()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- token ---------------------------------------------------------------
    def _sign(self, meta: Mapping[str, Any], session_fp: str, expiry: int) -> str:
        """ลายเซ็นผูกทุกอย่างที่ commit ใช้ตัดสิน — แก้ไฟล์ preview บนดิสก์แล้ว token ใช้ไม่ได้."""
        issues = json.dumps([meta["errors"], meta["warnings"]], sort_keys=True, ensure_ascii=True)
        message = "|".join([
            meta["preview_id"], meta["rel"], meta["base_sha256"] or "-", meta["proposed_sha256"], session_fp,
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

    def _lock(self, path: GuardedPath) -> threading.Lock:
        key = os.path.normcase(os.fspath(path.path))
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
        current = fs.read_doc(guarded.path, self.max_bytes)
        if request.base_sha256 != current.sha256:
            raise StudioError("conflict", "ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้ โหลดใหม่ก่อน", 409,
                              {"current_sha256": current.sha256})
        proposal = handler.propose(guarded, current, request.payload)
        if len(proposal.proposed) > self.max_bytes:
            raise StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413)
        errors = [*proposal.errors, *_whitespace_errors(current, proposal.proposed)]
        try:
            diff = unified(current.text, fs.decode(proposal.proposed).text)
        except StudioError:  # ผลลัพธ์ไม่ใช่ข้อความ — มี error แล้ว ไม่แสดง diff ที่ทำให้เข้าใจผิด
            diff = {"hunks": [], "added": 0, "removed": 0, "old_eof_newline": True, "new_eof_newline": True}
        now = self.clock()
        self.state.sweep_previews(TOKEN_TTL_SECONDS * 2, now)
        if self.state.count_previews() >= MAX_PREVIEWS:
            raise StudioError("too_many_previews", "มีตัวอย่างการแก้ค้างมากเกินไป รอสักครู่แล้วลองใหม่", 429)
        preview_id = uuid.uuid4().hex
        expiry = int(now) + TOKEN_TTL_SECONDS
        meta: dict[str, Any] = {
            "preview_id": preview_id, "kind": request.kind, "rel": guarded.rel, "rule": guarded.rule,
            "op": "modify" if current.exists else "create",
            "base_sha256": current.sha256, "proposed_sha256": _sha(proposal.proposed),
            "impact": impact_for(guarded.rule), "confirm_phrase": confirm_phrase(guarded),
            "errors": errors, "warnings": proposal.warnings, "added": diff["added"], "removed": diff["removed"],
            "expiry": expiry, "actor": actor, "created_at": utc_iso(now),
        }
        self.state.save_preview(preview_id, meta, proposal.proposed)
        return {
            "preview_id": preview_id, "path": guarded.rel, "kind": request.kind, "op": meta["op"],
            "impact": meta["impact"], "confirm_phrase": meta["confirm_phrase"] if meta["impact"] == "high" else None,
            "base_sha256": current.sha256, "proposed_sha256": meta["proposed_sha256"],
            "diff": diff, "warnings": proposal.warnings, "errors": errors,
            "confirm_token": self._token(meta, session_fp), "expires_at": utc_iso(expiry),
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
        if _sha(proposed) != meta.get("proposed_sha256"):
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
            change_id = uuid.uuid4().hex
            now = self.clock()
            record = {
                "change_id": change_id, "preview_id": preview_id, "at": utc_iso(now), "actor": actor,
                "kind": meta["kind"], "op": meta["op"], "path": meta["rel"], "impact": meta["impact"],
                "base_sha256": meta["base_sha256"], "new_sha256": meta["proposed_sha256"],
                "added": meta.get("added", 0), "removed": meta.get("removed", 0),
                "backup": f".runtime/backups/{change_id}",
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
                    "sha256": _sha(original), "at": record["at"],
                })
            except OSError:
                outcome("failed", step="backup")
                raise StudioError("write_failed", "สำรองไฟล์เดิมไม่ได้ ไม่มีอะไรถูกเปลี่ยน", 503,
                                  {"step": "backup"}) from None
            try:
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
                os.unlink(guarded.path)
            else:
                fs.write_bytes_atomic(guarded.path, original)
        except OSError:
            return False
        return True
