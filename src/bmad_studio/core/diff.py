"""Diff แบบ unified (context 3) พร้อมเลขบรรทัดและ span ระดับคำ (AD-5, REQ-043 c).

คำนวณบน text แบบ LF ของไฟล์เดิม/ที่เสนอ — ความต่างเฉพาะ EOL/BOM จึงไม่โผล่เป็น hunk
(core.writes ตรวจกรณีนั้นแยกเป็น ``errors[]``).
"""
from __future__ import annotations

import difflib
import re
from typing import Any

CONTEXT = 3
_LF = chr(10)
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)
# SequenceMatcher เป็น O(n*m) ถึง O(n^3) กับบรรทัดซ้ำ ๆ — input มาจากผู้ใช้ภายนอก จึงจำกัดงานต่อช่วง
# (วัดบนเครื่องนี้: 300x300 บรรทัดซ้ำ ~0.08 วินาที, 800x800 ~1.6 วินาที)
MAX_EXACT_WORK = 90_000
MAX_WORD_WORK = 10_000
_MAX_DEPTH = 40

Opcode = tuple[str, int, int, int, int]


def _lis(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """longest increasing subsequence ตามตำแหน่งใน b (pairs เรียงตาม a อยู่แล้ว) — O(k log k)."""
    import bisect

    tails: list[int] = []
    tail_idx: list[int] = []
    prev: list[int] = [-1] * len(pairs)
    for index, (_i, j) in enumerate(pairs):
        pos = bisect.bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tail_idx.append(index)
        else:
            tails[pos] = j
            tail_idx[pos] = index
        prev[index] = tail_idx[pos - 1] if pos else -1
    out: list[tuple[int, int]] = []
    index = tail_idx[-1] if tail_idx else -1
    while index >= 0:
        out.append(pairs[index])
        index = prev[index]
    return out[::-1]


def _region(a: list[str], b: list[str], a0: int, a1: int, b0: int, b1: int, depth: int, out: list[Opcode]) -> None:
    # ตัด prefix/suffix ที่เหมือนกัน
    while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
        out.append(("equal", a0, a0 + 1, b0, b0 + 1))
        a0 += 1
        b0 += 1
    tail: list[Opcode] = []
    while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
        a1 -= 1
        b1 -= 1
        tail.append(("equal", a1, a1 + 1, b1, b1 + 1))
    if a0 == a1 or b0 == b1:
        if a1 > a0:
            out.append(("delete", a0, a1, b0, b0))
        elif b1 > b0:
            out.append(("insert", a0, a0, b0, b1))
    elif (a1 - a0) * (b1 - b0) <= MAX_EXACT_WORK:
        matcher = difflib.SequenceMatcher(a=a[a0:a1], b=b[b0:b1], autojunk=False)
        for tag, i0, i1, j0, j1 in matcher.get_opcodes():
            out.append((tag, a0 + i0, a0 + i1, b0 + j0, b0 + j1))
    else:
        # patience: ใช้บรรทัดที่ไม่ซ้ำในทั้งสองฝั่งเป็นจุดยึด แล้วทำทีละช่วง
        count_a: dict[str, int] = {}
        for i in range(a0, a1):
            count_a[a[i]] = count_a.get(a[i], 0) + 1
        pos_b: dict[str, int] = {}
        count_b: dict[str, int] = {}
        for j in range(b0, b1):
            count_b[b[j]] = count_b.get(b[j], 0) + 1
            pos_b[b[j]] = j
        pairs = [(i, pos_b[a[i]]) for i in range(a0, a1) if count_a[a[i]] == 1 and count_b.get(a[i]) == 1]
        anchors = _lis(pairs) if depth < _MAX_DEPTH else []
        if not anchors:
            out.append(("replace", a0, a1, b0, b1))
        else:
            ca, cb = a0, b0
            for i, j in anchors:
                _region(a, b, ca, i, cb, j, depth + 1, out)
                out.append(("equal", i, i + 1, j, j + 1))
                ca, cb = i + 1, j + 1
            _region(a, b, ca, a1, cb, b1, depth + 1, out)
    out.extend(reversed(tail))


def line_opcodes(a: list[str], b: list[str]) -> list[Opcode]:
    """opcode แบบ ``SequenceMatcher.get_opcodes`` ที่งานมีขอบเขต (ช่วงเล็ก = ผลเท่า difflib)."""
    raw: list[Opcode] = []
    _region(a, b, 0, len(a), 0, len(b), 0, raw)
    merged: list[Opcode] = []
    for op in raw:
        if op[1] == op[2] and op[3] == op[4]:
            continue
        if merged and merged[-1][0] == op[0] and merged[-1][2] == op[1] and merged[-1][4] == op[3]:
            last = merged[-1]
            merged[-1] = (last[0], last[1], op[2], last[3], op[4])
        else:
            merged.append(op)
    return merged


class _BoundedMatcher(difflib.SequenceMatcher):
    def get_opcodes(self):  # type: ignore[override]
        if self.opcodes is None:
            self.opcodes = line_opcodes(list(self.a), list(self.b))
        return self.opcodes


def _lines(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _word_spans(old: str, new: str) -> tuple[list[list[int]], list[list[int]]]:
    """ช่วงอักขระ [start, end) ที่ต่างกันในแต่ละฝั่ง."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    if len(a) * len(b) > MAX_WORD_WORK:
        return ([[0, len(old)]] if old else []), ([[0, len(new)]] if new else [])
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    a_pos = [0]
    for token in a:
        a_pos.append(a_pos[-1] + len(token))
    b_pos = [0]
    for token in b:
        b_pos.append(b_pos[-1] + len(token))
    old_spans: list[list[int]] = []
    new_spans: list[list[int]] = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if a1 > a0:
            old_spans.append([a_pos[a0], a_pos[a1]])
        if b1 > b0:
            new_spans.append([b_pos[b0], b_pos[b1]])
    return old_spans, new_spans


def unified(old_text: str, new_text: str, context: int = CONTEXT) -> dict[str, Any]:
    """``{"hunks": [...], "added": N, "removed": M}``.

    แต่ละ hunk: ``old_start``, ``old_lines``, ``new_start``, ``new_lines`` (เลขบรรทัดเริ่มที่ 1)
    และ ``lines``: ``{"type": "context"|"add"|"del", "old": n|None, "new": n|None, "text", "spans"}``.
    """
    a, b = _lines(old_text), _lines(new_text)
    matcher = _BoundedMatcher(a=a, b=b, autojunk=False)
    hunks: list[dict[str, Any]] = []
    added = removed = 0
    for group in matcher.get_grouped_opcodes(context):
        lines: list[dict[str, Any]] = []
        for tag, a0, a1, b0, b1 in group:
            if tag == "equal":
                for offset in range(a1 - a0):
                    lines.append({"type": "context", "old": a0 + offset + 1, "new": b0 + offset + 1,
                                  "text": a[a0 + offset], "spans": []})
                continue
            dels = list(range(a0, a1))
            adds = list(range(b0, b1))
            pair_spans: dict[tuple[str, int], list[list[int]]] = {}
            if tag == "replace":
                for i, j in zip(dels, adds):
                    old_spans, new_spans = _word_spans(a[i], b[j])
                    pair_spans[("del", i)] = old_spans
                    pair_spans[("add", j)] = new_spans
            for i in dels:
                lines.append({"type": "del", "old": i + 1, "new": None, "text": a[i],
                              "spans": pair_spans.get(("del", i), [[0, len(a[i])]] if a[i] else [])})
            for j in adds:
                lines.append({"type": "add", "old": None, "new": j + 1, "text": b[j],
                              "spans": pair_spans.get(("add", j), [[0, len(b[j])]] if b[j] else [])})
            removed += len(dels)
            added += len(adds)
        first, last = group[0], group[-1]
        hunks.append({
            "old_start": first[1] + 1 if last[2] > first[1] else first[1],
            "old_lines": last[2] - first[1],
            "new_start": first[3] + 1 if last[4] > first[3] else first[3],
            "new_lines": last[4] - first[3],
            "lines": lines,
        })
    return {"hunks": hunks, "added": added, "removed": removed,
            "old_eof_newline": old_text.endswith(_LF) or not old_text,
            "new_eof_newline": new_text.endswith(_LF) or not new_text}
