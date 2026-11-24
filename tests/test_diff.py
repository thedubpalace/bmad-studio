"""core.diff: hunk unified context 3, เลขบรรทัด, word spans (REQ-043 c)."""
from __future__ import annotations

from bmad_studio.core.diff import unified


def _file(n: int) -> str:
    return "".join(f"line {i} value\n" for i in range(1, n + 1))


def test_one_line_edit_in_300_lines_is_one_small_hunk() -> None:
    old = _file(300)
    new = old.replace("line 150 value\n", "line 150 changed\n")
    diff = unified(old, new)
    assert diff["added"] == 1 and diff["removed"] == 1
    assert len(diff["hunks"]) == 1
    hunk = diff["hunks"][0]
    assert (hunk["old_start"], hunk["old_lines"], hunk["new_start"], hunk["new_lines"]) == (147, 7, 147, 7)
    types = [line["type"] for line in hunk["lines"]]
    assert types == ["context"] * 3 + ["del", "add"] + ["context"] * 3
    deleted = hunk["lines"][3]
    added = hunk["lines"][4]
    assert deleted["old"] == 150 and deleted["new"] is None
    assert added["new"] == 150 and added["old"] is None
    assert deleted["text"][slice(*deleted["spans"][0])] == "value"
    assert added["text"][slice(*added["spans"][0])] == "changed"


def test_two_far_edits_are_two_hunks() -> None:
    old = _file(100)
    new = old.replace("line 10 value", "line 10 x").replace("line 90 value", "line 90 y")
    assert len(unified(old, new)["hunks"]) == 2


def test_pure_insert_and_new_file() -> None:
    diff = unified("", "a\nb\n")
    assert diff["added"] == 2 and diff["removed"] == 0
    hunk = diff["hunks"][0]
    assert hunk["old_start"] == 0 and hunk["old_lines"] == 0 and hunk["new_start"] == 1
    assert hunk["lines"][0]["spans"] == [[0, 1]]


def test_identical_has_no_hunks() -> None:
    diff = unified("a\nb\n", "a\nb\n")
    assert (diff["hunks"], diff["added"], diff["removed"]) == ([], 0, 0)


def test_pure_deletion_header() -> None:
    diff = unified("a\nb\nc\n", "a\nc\n")
    hunk = diff["hunks"][0]
    assert diff["removed"] == 1 and diff["added"] == 0
    assert [line["type"] for line in hunk["lines"]] == ["context", "del", "context"]
    assert (hunk["old_start"], hunk["old_lines"], hunk["new_start"], hunk["new_lines"]) == (1, 3, 1, 2)
    everything = unified("a\nb\n", "")
    assert everything["hunks"][0]["new_lines"] == 0 and everything["removed"] == 2


def test_unequal_replace_spans() -> None:
    diff = unified("x\n", "x1\nx2\nx3\n")
    lines = diff["hunks"][0]["lines"]
    assert [line["type"] for line in lines] == ["del", "add", "add", "add"]
    assert lines[2]["spans"] == [[0, 2]]  # บรรทัดที่ไม่มีคู่ = ทั้งบรรทัด


def test_trailing_newline_change_is_flagged() -> None:
    diff = unified("a\nb\n", "a\nb")
    assert diff["hunks"] == [] and diff["old_eof_newline"] is True and diff["new_eof_newline"] is False


def test_bounded_matcher_equals_difflib_on_small_inputs() -> None:
    import difflib
    import random

    from bmad_studio.core.diff import line_opcodes

    rng = random.Random(7)
    for _ in range(200):
        a = [rng.choice("abcde") for _ in range(rng.randint(0, 30))]
        b = [rng.choice("abcde") for _ in range(rng.randint(0, 30))]
        ops = line_opcodes(a, b)
        # ผลต้องสร้าง b จาก a ได้ถูกต้องเสมอ
        rebuilt: list[str] = []
        for tag, a0, a1, b0, b1 in ops:
            rebuilt.extend(a[a0:a1] if tag == "equal" else b[b0:b1])
        assert rebuilt == b
        for tag, a0, a1, b0, b1 in ops:
            if tag == "equal":
                assert a[a0:a1] == b[b0:b1]
        # จำนวนบรรทัดที่จับคู่ได้ไม่น้อยกว่า difflib มากนัก (prefix/suffix trim อาจต่างเล็กน้อย)
        ours = sum(a1 - a0 for t, a0, a1, _, _ in ops if t == "equal")
        theirs = sum(block.size for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
        assert ours >= theirs - 3


def test_adversarial_repetitive_input_is_fast() -> None:
    import time

    old = "".join(f"{i % 2}\n" for i in range(5000))
    new = "".join(f"{(i + (i % 7 == 0)) % 2}\n" for i in range(5000))
    line = " ".join("ab"[i % 2] for i in range(3000))
    start = time.perf_counter()
    unified(old, new)
    unified(line + "\n", line.replace("a b", "b b", 50) + "\n")
    assert time.perf_counter() - start < 3.0


def test_far_apart_edits_in_big_file_stay_small() -> None:
    old = "".join(f"unique line {i}\n" for i in range(3000))
    new = old.replace("unique line 10\n", "edited 10\n").replace("unique line 2900\n", "edited 2900\n")
    diff = unified(old, new)
    assert diff["added"] == 2 and diff["removed"] == 2 and len(diff["hunks"]) == 2


def test_thai_word_spans() -> None:
    diff = unified("ภาษา: ไทย\n", "ภาษา: อังกฤษ\n")
    lines = diff["hunks"][0]["lines"]
    assert lines[1]["text"][slice(*lines[1]["spans"][0])] == "อังกฤษ"
