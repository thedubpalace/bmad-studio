"""TextDoc เก็บ BOM/encoding/EOL และ encode กลับได้ bytes เดิม (AD-6)."""
from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from bmad_studio.adapters import fs
from bmad_studio.core.errors import StudioError

SAMPLES = {
    "lf": b"a: 1\nb: 2\n",
    "crlf": b"a: 1\r\nb: 2\r\n",
    "cr": b"a: 1\rb: 2\r",
    "mixed": b"a: 1\r\nb: 2\nc: 3\r\n",
    "bom_crlf": codecs.BOM_UTF8 + "ไทย: 1\r\nb: 2\r\n".encode("utf-8"),
    "bom_only": codecs.BOM_UTF8,
    "no_final_newline": b"a: 1\nb: 2",
    "crlf_no_final_newline": b"a: 1\r\nb: 2",
    "empty": b"",
    "blank_lines": b"\n\n\r\n",
    "utf16le": codecs.BOM_UTF16_LE + "a: 1\r\nไทย\r\n".encode("utf-16-le"),
    "utf16be": codecs.BOM_UTF16_BE + "a: 1\nb\n".encode("utf-16-be"),
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_round_trip_is_byte_exact(name: str) -> None:
    raw = SAMPLES[name]
    doc = fs.decode(raw)
    assert doc.encode() == raw
    assert doc.encode(doc.text) == raw
    assert "\r" not in doc.text


def test_detects_bom_and_dominant_eol() -> None:
    doc = fs.decode(SAMPLES["bom_crlf"])
    assert doc.bom == codecs.BOM_UTF8 and doc.encoding == "utf-8" and doc.eol == "\r\n"
    assert fs.decode(SAMPLES["lf"]).eol == "\n"
    assert fs.decode(SAMPLES["cr"]).eol == "\r"
    assert fs.decode(b"x").eol == "\n"


def test_edit_one_line_keeps_other_lines_bytes() -> None:
    doc = fs.decode(SAMPLES["mixed"])
    new = doc.encode("a: 1\nb: 20\nc: 3\n")
    # บรรทัดที่ไม่แก้คง EOL เดิมของตัวเอง; บรรทัดที่แก้ใช้ EOL หลัก (CRLF)
    assert new == b"a: 1\r\nb: 20\r\nc: 3\r\n"


def test_edit_crlf_bom_file_keeps_bom_and_crlf() -> None:
    doc = fs.decode(SAMPLES["bom_crlf"])
    new = doc.encode(doc.text + "c: 3\n")
    assert new == SAMPLES["bom_crlf"] + b"c: 3\r\n"


def test_adding_final_newline_and_removing_it() -> None:
    doc = fs.decode(SAMPLES["crlf_no_final_newline"])
    assert doc.encode("a: 1\nb: 2\n") == b"a: 1\r\nb: 2\r\n"
    doc2 = fs.decode(SAMPLES["crlf"])
    assert doc2.encode("a: 1\nb: 2") == b"a: 1\r\nb: 2"


def test_input_with_crlf_is_normalized_before_encoding() -> None:
    doc = fs.decode(SAMPLES["lf"])
    assert doc.encode("a: 1\r\nb: 3\r\n") == b"a: 1\nb: 3\n"


def test_new_doc_is_utf8_no_bom_lf() -> None:
    doc = fs.new_doc()
    assert not doc.exists and doc.sha256 is None
    assert doc.encode("x\ny\n") == b"x\ny\n"
    assert doc.encode("x\r\ny") == b"x\ny"


def test_sha256_of_existing() -> None:
    import hashlib

    assert fs.decode(b"a").sha256 == hashlib.sha256(b"a").hexdigest()


def test_non_utf8_rejected() -> None:
    with pytest.raises(StudioError) as exc:
        fs.decode(b"not utf8 \x80\xff")
    assert exc.value.code == "unsupported_encoding"
    with pytest.raises(StudioError):
        fs.decode(b"\xff\xfe\x00")  # BOM UTF-16 แต่ความยาวคี่


def test_read_doc_missing_file_is_new(tmp_path: Path) -> None:
    assert fs.read_doc(tmp_path / "nope.md").exists is False


def test_read_doc_round_trip_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_bytes(SAMPLES["bom_crlf"])
    assert fs.read_doc(path).encode() == SAMPLES["bom_crlf"]


def test_read_too_large(tmp_path: Path) -> None:
    path = tmp_path / "big.md"
    path.write_bytes(b"a" * 1025)
    with pytest.raises(StudioError) as exc:
        fs.read_doc(path, max_bytes=1024)
    assert exc.value.code == "file_too_large" and exc.value.status == 413
    assert fs.read_doc(path, max_bytes=1025).exists


@pytest.mark.parametrize(
    "raw",
    [
        codecs.BOM_UTF32_LE + "a".encode("utf-32-le"),
        codecs.BOM_UTF32_BE + "a".encode("utf-32-be"),
        "a: 1\n".encode("utf-16-le"),  # UTF-16 ไม่มี BOM = ASCII + NUL
        b"PK\x03\x04\x00\x00binary",
    ],
)
def test_ambiguous_or_binary_encodings_rejected(raw: bytes) -> None:
    with pytest.raises(StudioError) as exc:
        fs.decode(raw)
    assert exc.value.code == "unsupported_encoding"


def test_utf16_edit_keeps_bom_and_encoding() -> None:
    raw = SAMPLES["utf16le"]
    doc = fs.decode(raw)
    new = doc.encode(doc.text + "b\n")
    assert new == raw + "b\r\n".encode("utf-16-le")


def test_lone_surrogate_is_422() -> None:
    with pytest.raises(StudioError) as exc:
        fs.decode(b"a\n").encode("a\n\ud800\n")
    assert exc.value.code == "invalid_text" and exc.value.status == 422


def test_directory_read_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(StudioError) as exc:
        fs.read_doc(tmp_path)
    assert exc.value.code == "not_a_file" and exc.value.status == 400


def test_parent_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "f.md").write_text("x", encoding="utf-8")
    try:
        doc = fs.read_doc(tmp_path / "f.md" / "x")
    except StudioError as exc:
        assert exc.code == "not_a_file"
    else:  # Windows รายงาน FileNotFoundError — guard ปฏิเสธ path แบบนี้อยู่แล้ว (ไม่อยู่ใน allowlist)
        assert doc.exists is False
