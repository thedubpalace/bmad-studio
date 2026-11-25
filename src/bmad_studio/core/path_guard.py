"""Path guard ของการเขียนไฟล์ภายนอกทุกครั้ง (AD-2, REQ-043 d, REQ-047 a).

ทุก path ที่จะถูกเขียนต้องผ่าน :meth:`PathGuard.check_writable` ก่อน:

1. แปลงเป็น path สัมพัทธ์กับ ``VEDA_ROOT`` แบบ lexical (absolute นอก root, UNC, device path = ปฏิเสธ)
2. ตรวจทุก component: ห้าม ``.``/``..``/ว่าง, อักขระต้องห้ามของ Windows (รวม ``:`` ของ ADS),
   control char, ชื่อที่ลงท้ายด้วยจุด/ช่องว่าง, ชื่ออุปกรณ์ (CON, NUL, …) และ short name แบบ ``~1``
3. ``realpath`` ต้องเท่ากับ path lexical — symlink/junction ตัวใดก็ตามบนเส้นทาง = ปฏิเสธ
   (แม้ชี้กลับเข้ามาใน root) เพื่อไม่ต้องเดาว่าปลายทางคืออะไร
4. **deny ถาวรชนะเสมอ**: ``.veda/projects/registry.json``, ``_bmad/config.toml``, ``**/customize.toml``,
   ``**/SKILL.md`` — เทียบแบบไม่สนตัวพิมพ์ (Windows filesystem ไม่สนตัวพิมพ์)
5. ต้องตรง allowlist ข้อใดข้อหนึ่ง; ไฟล์ที่มีอยู่ต้องเป็นไฟล์ปกติที่ไม่มี hard link อื่น

ข้อความ error ไม่มี path (AD-12); ``details.reason`` บอกเพียงชนิดของเหตุผล.

ข้อจำกัด (TOCTOU): guard ตรวจ ณ เวลาที่เรียก — ผู้เขียน (:mod:`bmad_studio.core.writes`) ต้องเรียก
``check_writable`` ซ้ำภายใต้ lock ของ path ทันทีก่อน ``os.replace`` และเขียน temp ในโฟลเดอร์เดียวกัน
(``os.replace`` แทนที่ตัว link เอง ไม่ตาม link). ผู้โจมตีผ่าน HTTP สร้าง link บนดิสก์ไม่ได้.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bmad_studio.core.errors import StudioError

FORBIDDEN = "path_forbidden"
_MESSAGE_TH = "ไม่อนุญาตให้เขียนไฟล์นี้"

# deny ถาวร (ชนะ allowlist) — เทียบกับ path สัมพัทธ์ตัวพิมพ์เล็กแบบ posix
DENY_EXACT: frozenset[str] = frozenset({".veda/projects/registry.json", "_bmad/config.toml"})
DENY_BASENAMES: frozenset[str] = frozenset({"customize.toml", "skill.md"})

_SAFE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
FLOW_DEFAULT_RULE = "flow-default"
FLOW_PROJECT_RULE = "flow-project"
#: rule ของไฟล์ flow — ทุกการเขียนต้องผ่าน validator ของ flow (AD-8) ไม่ว่า kind ใด
FLOW_RULES: frozenset[str] = frozenset({FLOW_DEFAULT_RULE, FLOW_PROJECT_RULE})
# (rule, regex บน path สัมพัทธ์ posix) — ตรวจแบบ IGNORECASE + ASCII (กัน ſ/K ของ Unicode ที่ match [A-Za-z] เมื่อไม่สนตัวพิมพ์)
ALLOW_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.ASCII))
    for name, pattern in (
        ("agent", rf"\.claude/agents/{_SAFE_NAME}\.md"),
        ("agent-backup", rf"\.claude/agents/\.backup/(?:{_SAFE_NAME}/)*{_SAFE_NAME}"),
        ("claude-md", r"CLAUDE\.md"),
        ("bmad-custom", rf"_bmad/custom/{_SAFE_NAME}\.toml"),
        (FLOW_DEFAULT_RULE, r"\.veda/flow\.default\.yaml"),
        (FLOW_PROJECT_RULE, rf"\.veda/projects/{_SAFE_NAME}/flow\.yaml"),
    )
)

_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f\x7f]')
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]|CONIN\$|CONOUT\$)(?:\..*)?", re.IGNORECASE)
_SHORT_NAME = re.compile(r"~\d")


@dataclass(frozen=True)
class GuardedPath:
    """path ที่ผ่าน guard แล้ว: ``path`` absolute (resolve แล้ว), ``rel`` posix สัมพัทธ์ root, ``rule``."""

    path: Path
    rel: str
    rule: str


def _forbidden(reason: str) -> StudioError:
    return StudioError(FORBIDDEN, _MESSAGE_TH, 403, {"reason": reason})


def _normcase(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


class PathGuard:
    """ผูกกับ ``VEDA_ROOT`` เดียว (มาจาก :class:`~bmad_studio.core.paths.VedaPaths`)."""

    def __init__(self, root: Path | str) -> None:
        real = os.path.realpath(os.fspath(root))
        if not os.path.isdir(real):
            raise ValueError("VEDA_ROOT ต้องเป็นโฟลเดอร์ที่มีอยู่")
        self.root = Path(real)

    # -- ขั้นที่ 1–2: lexical -------------------------------------------------
    def _relative_parts(self, path: Path | str) -> list[str]:
        if not isinstance(path, (str, os.PathLike)):
            raise _forbidden("invalid")
        try:
            raw = os.fspath(path)
        except TypeError:
            raise _forbidden("invalid") from None
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise _forbidden("invalid")
        if raw.startswith(("\\\\", "//")):  # UNC, \\?\, \\.\
            raise _forbidden("unc")
        text = raw.replace("\\", "/")
        if os.path.isabs(raw):
            # ตัด prefix ของ root แบบ text เท่านั้น — ไม่ใช้ normpath/relpath เพราะบน Windows
            # มันตัดจุด/ช่องว่างท้ายชื่อและยุบ separator ซ้ำ ทำให้ตรวจ component ด้านล่างไม่เจอ
            root_text = os.fspath(self.root).replace("\\", "/").rstrip("/") + "/"
            head = text[: len(root_text)]
            if os.path.normcase(head) != os.path.normcase(root_text):
                raise _forbidden("outside_root")
            text = text[len(root_text):]
            if not text:
                raise _forbidden("root")
        elif text.startswith("/") or re.match(r"^[A-Za-z]:", text):
            raise _forbidden("drive_relative")  # "C:foo" หรือ "/foo" บน Windows
        parts = text.split("/")
        for part in parts:
            if part in ("", ".", ".."):
                raise _forbidden("dot_segment")
            if not part.isascii():
                # NTFS เทียบชื่อด้วย upcase table: "ſkill.md" อาจเป็นไฟล์เดียวกับ SKILL.md
                raise _forbidden("non_ascii")
            if _BAD_CHARS.search(part):
                raise _forbidden("bad_char")
            if part[-1] in ". ":
                raise _forbidden("trailing_dot_space")
            if _DEVICE.fullmatch(part.split(".")[0].rstrip(" ")):
                raise _forbidden("device_name")
            if _SHORT_NAME.search(part):
                raise _forbidden("short_name")
        return parts

    def check_writable(self, path: Path | str) -> GuardedPath:
        """คืน :class:`GuardedPath` ถ้าเขียนได้; ไม่เช่นนั้น raise ``StudioError("path_forbidden")``."""
        parts = self._relative_parts(path)
        rel = str(PurePosixPath(*parts))
        rel_lower = rel.lower()

        # ขั้นที่ 4: deny ก่อน — ทั้งบน path ที่ขอและหลัง resolve
        self._check_deny(rel_lower)

        lexical = os.path.join(os.fspath(self.root), *parts)
        real = os.path.realpath(lexical)
        if _normcase(real) != _normcase(lexical):
            raise _forbidden("link")
        # realpath ไม่ตามลิงก์ที่ปลายทางเสียหาย/วนบนบางระบบ — ตรวจ lstat ทุก component ที่มีอยู่ซ้ำ
        current = os.fspath(self.root)
        for part in parts:
            current = os.path.join(current, part)
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError:
                raise _forbidden("stat_failed") from None
            if stat.S_ISLNK(info.st_mode) or _is_reparse_point(info):
                raise _forbidden("link")
        real_rel = os.path.relpath(real, os.fspath(self.root)).replace("\\", "/")
        self._check_deny(real_rel.lower())

        # ขั้นที่ 5: allowlist
        rule = next((name for name, pattern in ALLOW_RULES if pattern.fullmatch(rel)), None)
        if rule is None:
            raise _forbidden("not_allowlisted")
        try:
            info = os.lstat(real)
        except FileNotFoundError:
            pass
        except OSError:
            raise _forbidden("stat_failed") from None
        else:
            if not stat.S_ISREG(info.st_mode):
                raise _forbidden("not_regular_file")
            if info.st_nlink > 1:
                raise _forbidden("hard_link")
        return GuardedPath(Path(real), rel, rule)

    @staticmethod
    def _check_deny(rel_lower: str) -> None:
        if rel_lower in DENY_EXACT or rel_lower.rsplit("/", 1)[-1] in DENY_BASENAMES:
            raise _forbidden("denied")


def _is_reparse_point(info: os.stat_result) -> bool:
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def check_writable(path: Path | str, root: Path | str) -> GuardedPath:
    """ทางลัดของ ``PathGuard(root).check_writable(path)``."""
    return PathGuard(root).check_writable(path)
