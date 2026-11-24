"""อ่านไฟล์ภายนอกแบบรักษา byte (AD-6).

อ่านเป็น bytes → ตรวจ BOM + encoding + EOL หลัก → ให้ core แก้เป็น text แบบ LF →
:meth:`TextDoc.encode` เข้ารหัสกลับโดย **splice รายบรรทัด**: บรรทัดที่ไม่ถูกแก้ได้ bytes เดิม
(รวม EOL เดิมของบรรทัดนั้น แม้ไฟล์จะปน CRLF/LF), บรรทัดใหม่/ที่แก้ใช้ EOL หลักของไฟล์,
BOM เดิมคงไว้. ไม่แก้อะไรเลย = bytes เดิมทุก byte.

ไฟล์ใหม่ = UTF-8 ไม่มี BOM + LF. รับเฉพาะ UTF-8 (มี/ไม่มี BOM) และ UTF-16 ที่มี BOM;
อย่างอื่น (UTF-32, UTF-16 ไม่มี BOM, ไฟล์ binary ที่มี NUL) = ``unsupported_encoding`` ไม่เดา.
การเขียนไฟล์อยู่ที่ :mod:`bmad_studio.core.writes` เท่านั้น (AD-5).
"""
from __future__ import annotations

import codecs
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from bmad_studio.core.diff import line_opcodes  # งานมีขอบเขต: input มาจากผู้ใช้ภายนอก
from bmad_studio.core.errors import StudioError

MAX_BYTES = 2 * 1024 * 1024  # ไฟล์ config/agent/flow จริงเล็กกว่านี้มาก

_BOMS: tuple[tuple[bytes, str | None], ...] = (
    (codecs.BOM_UTF32_LE, None),  # ต้องมาก่อน UTF-16-LE (prefix เดียวกัน)
    (codecs.BOM_UTF32_BE, None),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_LINE = re.compile(r"([^\r\n]*)(\r\n|\n|\r|)")


def _split(text: str) -> list[tuple[str, str]]:
    """แยกเป็น (เนื้อหา, EOL) — EOL ว่างได้เฉพาะบรรทัดสุดท้าย."""
    lines: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _LINE.match(text, pos)
        assert match is not None
        lines.append((match.group(1), match.group(2)))
        pos = match.end()
    return lines


def to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dominant_eol(lines: list[tuple[str, str]]) -> str:
    counts = {"\r\n": 0, "\n": 0, "\r": 0}
    for _content, eol in lines:
        if eol:
            counts[eol] += 1
    best = max(counts, key=lambda key: (counts[key], key == "\n"))
    return best if counts[best] else "\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TextDoc:
    raw: bytes
    text: str  # LF เท่านั้น, ไม่มี BOM
    encoding: str
    bom: bytes
    eol: str
    exists: bool

    @property
    def sha256(self) -> str | None:
        """hash ของไฟล์ปัจจุบัน (``None`` = ยังไม่มีไฟล์) ใช้เป็น ``base_sha256`` ของ AD-5."""
        return sha256_hex(self.raw) if self.exists else None

    def encode(self, new_text: str | None = None) -> bytes:
        """bytes ของ ``new_text`` (ค่าเริ่มต้น = ข้อความเดิม) ด้วย BOM/encoding/EOL ของไฟล์นี้."""
        if new_text is None or to_lf(new_text) == self.text:
            return self.raw if self.exists else self.bom + self.text.encode(self.encoding)
        new_lines = _split(to_lf(new_text))
        old_lines = _split(self.raw[len(self.bom):].decode(self.encoding)) if self.exists else []
        opcodes = line_opcodes([content for content, _ in old_lines], [content for content, _ in new_lines])
        out: list[str] = []
        for tag, a0, _a1, b0, b1 in opcodes:
            for offset, index in enumerate(range(b0, b1)):
                content, new_eol = new_lines[index]
                eol = ""
                if new_eol:
                    eol = self.eol
                    if tag == "equal":
                        eol = old_lines[a0 + offset][1] or self.eol
                out.append(content + eol)
        try:
            return self.bom + "".join(out).encode(self.encoding)
        except UnicodeEncodeError:  # เช่น lone surrogate จาก JSON
            raise StudioError("invalid_text", "ข้อความมีอักขระที่บันทึกไม่ได้", 422) from None


def new_doc() -> TextDoc:
    return TextDoc(raw=b"", text="", encoding="utf-8", bom=b"", eol="\n", exists=False)


def decode(raw: bytes) -> TextDoc:
    bom, encoding = b"", "utf-8"
    for marker, name in _BOMS:
        if raw.startswith(marker):
            bom, encoding = marker, name
            break
    if encoding is None:
        raise _unsupported()
    try:
        text = raw[len(bom):].decode(encoding)
    except UnicodeDecodeError:
        raise _unsupported() from None
    if chr(0) in text:  # UTF-16 ไม่มี BOM หรือไฟล์ binary ที่บังเอิญ decode ได้
        raise _unsupported()
    lines = _split(text)
    return TextDoc(raw=raw, text=to_lf(text), encoding=encoding, bom=bom, eol=_dominant_eol(lines), exists=True)


def read_bytes(path: Path | str, max_bytes: int = MAX_BYTES) -> bytes | None:
    """bytes ของไฟล์ (``None`` ถ้าไม่มี); ใหญ่เกิน ``max_bytes`` = ``file_too_large``."""
    if os.path.isdir(path):
        raise _not_a_file()
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > max_bytes:
                raise _too_large()
            data = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return None
    except (IsADirectoryError, NotADirectoryError):
        raise _not_a_file() from None
    except PermissionError:
        if os.path.isdir(path):
            raise _not_a_file() from None
        raise StudioError("file_unreadable", "อ่านไฟล์นี้ไม่ได้ (ไม่มีสิทธิ์)", 403) from None
    except OSError:
        raise StudioError("file_unreadable", "อ่านไฟล์นี้ไม่ได้", 400) from None
    if len(data) > max_bytes:  # ไฟล์โตระหว่างอ่าน
        raise _too_large()
    return data


def read_doc(path: Path | str, max_bytes: int = MAX_BYTES) -> TextDoc:
    raw = read_bytes(path, max_bytes)
    return new_doc() if raw is None else decode(raw)


def _too_large() -> StudioError:
    return StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413, {"max_bytes": MAX_BYTES})


def _not_a_file() -> StudioError:
    return StudioError("not_a_file", "path นี้ไม่ใช่ไฟล์", 400)


def _unsupported() -> StudioError:
    return StudioError("unsupported_encoding", "ไฟล์นี้ไม่ได้เข้ารหัสแบบ UTF-8 แก้ผ่านสตูดิโอไม่ได้", 422)


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    """เขียน ``data`` แทนไฟล์ ``path`` แบบ atomic: temp ในโฟลเดอร์เดียวกัน → fsync → ``os.replace``.

    ``os.replace`` แทนที่ entry ของ directory (ไม่ตาม link ที่ปลายทาง). เรียกได้จาก
    :mod:`bmad_studio.core.writes` เท่านั้น (test ``test_write_path_scan``) — ผู้เรียกต้องผ่าน
    path guard และถือ lock ของ path แล้ว. ล้มเหลว = ``OSError`` และไฟล์เดิมไม่เปลี่ยน.
    """
    target = os.fspath(path)
    directory, name = os.path.split(target)
    tmp = os.path.join(directory, f".{name}.{os.urandom(8).hex()}.bmad-studio.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
