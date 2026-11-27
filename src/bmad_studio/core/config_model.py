"""Config/override ของ BMAD ใน ``_bmad/custom`` (story 6-1/6-3, REQ-040/041, AD-2/5/6/10).

กฎระดับ rule ``bmad-custom`` ของ path guard — write pipeline เรียกกับ **ทุก kind และทุก op** (config, override,
text, revert, ลบ/ย้ายไป backup) ทั้งตอน preview และตรวจซ้ำตอน commit ใต้ lock:

- :func:`validate_toml` — bytes ต้องเป็น TOML ที่ ``tomllib`` (ตัวเดียวกับที่ BMAD ใช้) อ่านได้ทั้งไฟล์ รวม BOM
- :func:`classify` — **ข้อความที่ BMAD ส่งเข้า prompt ของ agent เป็น injection class เดียวกับ Epic 5**: ทุกค่า
  string ใน config/customization ถูกแทนลงคำสั่งของ skill (``communication_language``, persona, ``principles``,
  ``instruction``, ``on_complete`` …) และ ``activation_steps_prepend/append`` / ``persistent_facts`` คือคำสั่งที่
  agent รันทุกครั้งที่เริ่ม skill. การเปลี่ยนที่แตะค่า string (เพิ่ม/แก้/ลบ, ทุกระดับ, ทุกรูปการเขียน TOML —
  เทียบบนค่าที่ parse แล้ว ไม่ใช่บนข้อความ) = impact ``high`` (พิมพ์ชื่อไฟล์ยืนยัน) พร้อมคำเตือนที่บอก key;
  เปลี่ยนเฉพาะตัวเลข/boolean = ``normal``

ค่า effective อ่านผ่าน resolve script ของ BMAD เท่านั้น (AD-10) — โมดูลนี้ไม่ merge layer เอง.
"""
from __future__ import annotations

import io
import json
import re
import tomllib
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import tomlkit
from tomlkit.exceptions import TOMLKitError

from bmad_studio.adapters import fs
from bmad_studio.adapters.bmad import BmadScripts, BmadUnavailable
from bmad_studio.core.errors import StudioError
from bmad_studio.core.path_guard import GuardedPath
from bmad_studio.core.writes import Proposal

RULE = "bmad-custom"
CONFIG_TEAM = "_bmad/custom/config.toml"
CONFIG_USER = "_bmad/custom/config.user.toml"
CONFIG_TARGETS: Mapping[str, str] = {CONFIG_TEAM: "team", CONFIG_USER: "user"}
#: key ของ customization ที่เป็น "คำสั่งที่ agent รันตอนเริ่ม skill" — เทียบแบบไม่สนตัวพิมพ์/ตัวคั่น
ACTIVATION_KEYS = frozenset({"activationstepsprepend", "activationstepsappend", "persistentfacts"})
MAX_EDITS = 100
MAX_VALUE_CHARS = 64 * 1024
MAX_PATH_DEPTH = 8
MAX_LISTED = 10

Issue = dict[str, Any]


def issue(code: str, error: str, **extra: Any) -> Issue:
    return {"code": code, "error": error, **extra}


# -- อ่าน TOML แบบที่ BMAD อ่าน --------------------------------------------------------------------

def parse_bytes(raw: bytes) -> tuple[dict[str, Any] | None, Issue | None]:
    """``tomllib.load`` บน bytes ตรง ๆ (เหมือน ``config_utils.load_toml``) → (dict, None) หรือ (None, issue)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, issue("toml_bom", "BMAD อ่านไฟล์ TOML ที่มี BOM ไม่ได้ — บันทึกเป็น UTF-8 ไม่มี BOM")
    try:
        data = tomllib.load(io.BytesIO(raw))
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", None)
        where = f" (บรรทัด {line})" if isinstance(line, int) else ""
        detail = str(getattr(exc, "msg", "") or "")[:120]
        return None, issue("invalid_toml", f"TOML ไม่ถูกต้อง{where}: {detail}",
                           **({"line": line} if isinstance(line, int) else {}))
    except UnicodeDecodeError:
        return None, issue("invalid_toml", "ไฟล์ไม่ใช่ UTF-8")
    except RecursionError:
        return None, issue("invalid_toml", "TOML ซ้อนลึกเกินไป")
    return data, None


#: Default_Ignorable_Code_Point ทั้งหมดของ Unicode (DerivedCoreProperties) — รวม variation selector (FE00–FE0F,
#: E0100–E01EF), CGJ 034F, Hangul filler 115F/1160/3164/FFA0, Khmer 17B4–17B5, Mongolian FVS 180B–180F, tag E00xx
#: + อักขระว่างที่ไม่ ignorable แต่มองไม่เห็น (braille blank 2800) — review 6-3 B2
_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
    (0x2800, 0x2800),
)


def _hidden(ch: str) -> bool:
    """อักขระที่มองไม่เห็นหรือสลับทิศการแสดงผล — ซ่อนคำสั่งจากผู้ตรวจ diff ได้: Default_Ignorable_Code_Point ทุกตัว,
    Cf/Co/Cs, control ยกเว้น tab/ขึ้นบรรทัด, Zl/Zp."""
    if ch in "\t\n\r":
        return False
    code = ord(ch)
    if any(lo <= code <= hi for lo, hi in _IGNORABLE_RANGES):
        return True
    return unicodedata.category(ch) in {"Cf", "Co", "Cs", "Cc", "Zl", "Zp"}


def hidden_characters(text: str) -> list[str]:
    """ตำแหน่ง (บรรทัด:คอลัมน์ + code point) ของอักขระที่มองไม่เห็นในข้อความทั้งไฟล์ (สูงสุด MAX_LISTED)."""
    found = []
    for line_no, line in enumerate(text.split("\n"), 1):
        for col, ch in enumerate(line, 1):
            if _hidden(ch):
                found.append(f"{line_no}:{col} U+{ord(ch):04X}")
                if len(found) >= MAX_LISTED:
                    return found
    return found


def _hidden_in_values(data: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    """(path, อักขระ) แรกที่ซ่อนอยู่ในชื่อ key หรือค่า string ทุกระดับ (รวมใน array/table ซ้อน)."""
    out: list[tuple[tuple[str, ...], str]] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            here = (*path, str(key))
            bad = next((ch for ch in str(key) if _hidden(ch)), None)
            if bad:
                out.append((here, bad))
            out += _hidden_in_values(value, here)
    elif isinstance(data, list):
        for item in data:
            out += _hidden_in_values(item, path)
    elif isinstance(data, str):
        bad = next((ch for ch in data if _hidden(ch)), None)
        if bad:
            out.append((path, bad))
    return out


def validate_toml(proposed: bytes) -> list[Issue]:
    """file validator ของ rule ``bmad-custom`` (ทุก kind รวม revert/text): TOML ที่ BMAD อ่านได้ และไม่มีอักขระที่
    มองไม่เห็น/สลับทิศ (review 6-3: ข้อความที่ agent อ่านต้องเห็นครบใน diff ก่อนยืนยัน)."""
    data, problem = parse_bytes(proposed)
    if problem:
        return [problem]
    hidden = hidden_characters(proposed.decode("utf-8"))
    # escape ใน TOML ("​", "\u0007") ไม่ปรากฏเป็นอักขระดิบในไฟล์ แต่ค่าที่ BMAD ส่งให้ agent มี — ตรวจค่าที่ parse แล้วด้วย
    hidden += [f"{dotted(path)} U+{ord(ch):04X}" for path, ch in _hidden_in_values(data or {})][:MAX_LISTED]
    if hidden:
        return [issue("hidden_characters", "มีอักขระที่มองไม่เห็นหรือสลับทิศการแสดงผล (เช่น zero-width, bidi, tag) — "
                                           "ผู้ตรวจ diff มองไม่เห็นข้อความจริง ลบออกก่อนบันทึก", keys=hidden)]
    return []


# -- leaf ของ TOML + การจัดชั้น impact --------------------------------------------------------------

def leaves(data: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """table → ลงไปทีละ key; ค่าอื่น (รวม array) เป็น leaf ทั้งก้อน."""
    if isinstance(data, Mapping):
        out: dict[tuple[str, ...], Any] = {}
        for key, value in data.items():
            out.update(leaves(value, (*prefix, str(key))))
        if not data and prefix:
            out[prefix] = {}
        return out
    return {prefix: data}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def dotted(path: Sequence[str]) -> str:
    return ".".join(path)


def changed_paths(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[tuple[str, ...]]:
    a, b = leaves(old), leaves(new)
    return sorted(p for p in set(a) | set(b) if a.get(p, _MISSING) != b.get(p, _MISSING)
                  or type(a.get(p, _MISSING)) is not type(b.get(p, _MISSING)))


_MISSING = object()
#: บรรทัดที่แก้ได้แบบ normal (review 6-1 B1 — fail closed): ``<เยื้อง><bare/dotted key><=><literal ตัวเลข/boolean><ท้าย>``
#: ทั้งบรรทัด; ส่วนอื่นนอก literal (เยื้อง, key, ช่องว่าง, comment ท้ายบรรทัด) ต้องเหมือนเดิมทุกตัวอักษร. quoted key,
#: inline table/array, string และ comment ทั้งบรรทัดไม่ match = ไม่ใช่ normal
_BARE_KEY = r"[A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*"
_LITERAL = (r"(?:true|false|[+-]?(?:inf|nan|0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+"
            r"|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?))")
_VALUE_LINE = re.compile(rf"(?P<head>[ \t]*{_BARE_KEY}[ \t]*=[ \t]*)(?P<lit>{_LITERAL})(?P<tail>[ \t]*(?:#.*)?)")


def _scalar_class(value: Any) -> str | None:
    """ชนิดที่เปลี่ยนได้แบบ normal: boolean หรือตัวเลข (int/float). ชนิดอื่น (string, table, array, วันเวลา) = None."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return None


def _lines(raw: bytes) -> list[str]:
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _only_value_literals_differ(old_raw: bytes, new_raw: bytes) -> bool:
    """ทุกบรรทัดเหมือนเดิม ยกเว้นบรรทัด ``key = <literal>`` ที่ต่างเฉพาะ literal (comment ท้ายบรรทัดเหมือนเดิม)."""
    old_lines, new_lines = _lines(old_raw), _lines(new_raw)
    if len(old_lines) != len(new_lines):
        return False
    for a, b in zip(old_lines, new_lines):
        if a == b:
            continue
        ma, mb = _VALUE_LINE.fullmatch(a), _VALUE_LINE.fullmatch(b)
        if not ma or not mb or ma["head"] != mb["head"] or ma["tail"] != mb["tail"]:
            return False
    return True


def _numbers_only_change(old_raw: bytes, new_raw: bytes, old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """normal ได้ก็ต่อเมื่อ (fail closed — ทุกข้อ): ชุด key เท่าเดิมทุกชั้น, ทุกค่าที่ต่างเป็น boolean/ตัวเลขชนิดเดิม
    ทั้งสองฝั่ง, comment ทั้งไฟล์เหมือนเดิม และทุกบรรทัดที่ต่างเป็นบรรทัด ``key = literal`` ที่ต่างเฉพาะ literal."""
    if comments("\n".join(_lines(old_raw))) != comments("\n".join(_lines(new_raw))):
        return False
    if not _only_value_literals_differ(old_raw, new_raw):
        return False
    a, b = leaves(old), leaves(new)
    if set(a) != set(b):
        return False
    for path in a:
        if a[path] == b[path] and type(a[path]) is type(b[path]):
            continue
        kind = _scalar_class(a[path])
        if kind is None or kind != _scalar_class(b[path]):
            return False
    return True


def classify(rel: str, current: bytes | None, proposed: bytes | None) -> tuple[str, list[Issue]]:
    """impact + คำเตือนของการเปลี่ยน ``current`` → ``proposed`` (``None`` = ไม่มีไฟล์) ใน ``_bmad/custom``.

    **fail closed**: ``normal`` เฉพาะการเปลี่ยนค่าตัวเลข/boolean ที่มีอยู่แล้ว (ดู :func:`_numbers_only_change`).
    อย่างอื่นทั้งหมด = ``high`` — ค่า string, ชื่อ key/table (BMAD ส่ง JSON ทั้งก้อนรวมชื่อ key ให้ agent), เพิ่ม/ลบ key,
    เปลี่ยนชนิด (เช่น ``principles = false`` ทับข้อความของ installer/skill ตอน merge), comment (agent อ่านไฟล์ดิบเมื่อ
    script ล้ม), สร้าง/ลบไฟล์ และไฟล์ที่ parse ไม่ได้.
    """
    parsed: list[dict[str, Any]] = []
    for raw in (current, proposed):
        if raw is None:
            parsed.append({})
            continue
        data, problem = parse_bytes(raw)
        if problem is not None:
            return "high", [issue("prompt_text_change", "ไฟล์นี้อ่านเป็น TOML ไม่ได้ทั้งก้อน — ถือว่าเปลี่ยนข้อความที่ BMAD ส่งให้ agent")]
        parsed.append(data)
    old, new = parsed
    if current is not None and proposed is not None and _numbers_only_change(current, proposed, old, new):
        return "normal", []
    changed = changed_paths(old, new)
    activation = [p for p in changed if any(_norm_key(k) in ACTIVATION_KEYS for k in p)]
    shown = lambda ps: [dotted(p) for p in ps[:MAX_LISTED]]  # noqa: E731
    warnings: list[Issue] = []
    if activation:
        warnings.append(issue(
            "override_activation_change",
            "เปลี่ยนคำสั่งที่ agent จะรันทุกครั้งที่เริ่ม skill (activation_steps / persistent_facts) — "
            "ข้อความนี้กลายเป็นคำสั่งของ agent โดยตรง ตรวจทุกบรรทัดใน diff แล้วพิมพ์ชื่อไฟล์เพื่อยืนยัน",
            keys=shown(activation)))
    rest = [p for p in changed if p not in activation]
    warnings.append(issue(
        "prompt_text_change",
        "การแก้นี้เปลี่ยนข้อความ ชื่อคีย์ ชนิดค่า หรือ comment ใน config/override ซึ่ง BMAD ส่งให้ agent อ่าน "
        "— ตรวจว่าไม่มีคำสั่งแฝงก่อนยืนยัน", keys=shown(rest)))
    return "high", warnings


def impact_rules() -> dict[str, Callable[[str, bytes | None, bytes | None], tuple[str, list[Issue]]]]:
    return {RULE: classify}


# -- แก้ TOML ทีละค่า (AD-6) ------------------------------------------------------------------------

def _key_path(raw: Any) -> list[str]:
    if (not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PATH_DEPTH
            or not all(isinstance(k, str) and 0 < len(k) <= 128 for k in raw)):
        raise StudioError("invalid_request", "path ของคีย์ต้องเป็นรายการชื่อ (1–8 ชั้น)", 422)
    return raw


def _plain_value(value: Any, depth: int = 0) -> Any:
    """ค่าที่ฟอร์มส่งได้: string/ตัวเลข/boolean และ array ของค่าเหล่านั้น (table ใช้โหมดข้อความ)."""
    if isinstance(value, bool) or isinstance(value, int) and not isinstance(value, bool):
        if isinstance(value, int) and not isinstance(value, bool) and not -(2 ** 63) <= value < 2 ** 63:
            raise StudioError("invalid_value", "ตัวเลขใหญ่เกินที่ TOML เก็บได้", 422)
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise StudioError("invalid_value", "ค่าตัวเลขนี้บันทึกไม่ได้", 422)
        return value
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            raise StudioError("invalid_value", "ข้อความยาวเกินไป", 422)
        return value
    if isinstance(value, list) and depth == 0 and len(value) <= 200:
        return [_plain_value(v, 1) for v in value]
    raise StudioError("invalid_value", "ค่าที่ส่งมาบันทึกลงฟอร์มไม่ได้ — ใช้โหมดแก้เป็นข้อความ TOML", 422)


def path_key(path: Sequence[str]) -> str:
    """id ของคีย์ที่ไม่ชนกัน (ชื่อ key ใน TOML มีจุดได้ — ``"a.b"`` ≠ ``a.b``)."""
    return json.dumps(list(path), ensure_ascii=False)


def _container(doc: Any, path: Sequence[str], *, create: bool) -> Any:
    node = doc
    for index, key in enumerate(path):
        if key not in node:
            if not create:
                return None
            node[key] = tomlkit.table(is_super_table=index == 0 and len(path) > 1)
        node = node[key]
        if not isinstance(node, Mapping):
            raise StudioError("invalid_value", f"คีย์ {key} ไม่ใช่ table — แก้ในโหมดข้อความ", 422)
    return node


def _prune(doc: Any, path: Sequence[str]) -> None:
    """ลบ table แม่ที่ว่างหลัง unset (ไม่ทิ้ง ``[core]`` เปล่าไว้เป็นแถวผี)."""
    for depth in range(len(path), 0, -1):
        parent = _container(doc, path[:depth - 1], create=False) if depth > 1 else doc
        if parent is None:
            return
        node = parent.get(path[depth - 1])
        if isinstance(node, Mapping) and len(node) == 0:
            del parent[path[depth - 1]]
        else:
            return


def apply_edits(text: str, sets: Sequence[Mapping[str, Any]], unsets: Sequence[Any]) -> str:
    """TOML ใหม่ที่ตั้งค่า ``sets`` และลบ ``unsets``; ส่วนอื่นคงรูป (tomlkit)."""
    if len(sets) + len(unsets) > MAX_EDITS:
        raise StudioError("invalid_request", "แก้ได้ไม่เกิน 100 ค่าต่อครั้ง", 422)
    try:
        doc = tomlkit.parse(text)
    except (TOMLKitError, RecursionError):
        raise StudioError("invalid_toml", "ไฟล์เดิมไม่ใช่ TOML ที่ถูกต้อง — แก้ในโหมดข้อความ", 422) from None
    for item in sets:
        if not isinstance(item, Mapping) or set(item) - {"path", "value"} or "value" not in item:
            raise StudioError("invalid_request", "set ต้องเป็น {path, value}", 422)
        path = _key_path(item.get("path"))
        value = _plain_value(item["value"])
        parent = _container(doc, path[:-1], create=True)
        existing = parent.get(path[-1])
        if isinstance(existing, Mapping) or (isinstance(existing, list) and any(isinstance(v, Mapping) for v in existing)):
            # ฟอร์มทับ table / array ของ table ทั้งก้อนไม่ได้ (ลบค่าซ้อนเงียบ ๆ)
            raise StudioError("invalid_value", f"คีย์ {path[-1]} เป็น table — แก้ในโหมดข้อความ", 422)
        try:
            parent[path[-1]] = value
        except (TypeError, ValueError, TOMLKitError):
            raise StudioError("invalid_value", "ค่าที่ส่งมาบันทึกลง TOML ไม่ได้", 422) from None
    for raw in unsets:
        path = _key_path(raw)
        parent = _container(doc, path[:-1], create=False)
        if parent is not None and path[-1] in parent:
            del parent[path[-1]]
            _prune(doc, path[:-1])
    try:
        return tomlkit.dumps(doc)
    except (TypeError, ValueError, TOMLKitError):
        raise StudioError("invalid_value", "ค่าที่ส่งมาบันทึกลง TOML ไม่ได้", 422) from None


_QUOTES = frozenset({'"', "'"})


def comments(text: str) -> list[str]:
    """comment ทุกตัว (ทั้งบรรทัดเต็มและท้ายบรรทัด) — ข้าม ``#`` ที่อยู่ใน string บรรทัดเดียว (หยาบ ๆ)."""
    out = []
    for line in text.split("\n"):
        quote: str | None = None
        escaped = False
        for index, ch in enumerate(line):
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\" and quote == '"':
                    escaped = True
                elif ch == quote:
                    quote = None
            elif ch in _QUOTES:
                quote = ch
            elif ch == "#":
                out.append(line[index:].strip())
                break
    return out


def proposed_text(current: fs.TextDoc, payload: Mapping[str, Any]) -> tuple[str, list[Issue]]:
    """โหมดฟอร์ม (``set``/``unset``) หรือโหมดข้อความ (``text``) → text ใหม่ (LF) + คำเตือน AD-6."""
    keys = set(payload) - {"acknowledged"}
    if "text" in keys:
        if keys != {"text"} or not isinstance(payload["text"], str):
            raise StudioError("invalid_request", "โหมดข้อความส่งได้เฉพาะ payload.text", 422)
        text = payload["text"]
        if len(text) > fs.MAX_BYTES:
            raise StudioError("file_too_large", "ไฟล์ใหญ่เกินกว่าที่สตูดิโอแก้ได้", 413)
        return fs.to_lf(text), []
    if not keys or keys - {"set", "unset"}:
        raise StudioError("invalid_request", "ต้องส่ง payload.set/unset หรือ payload.text", 422)
    sets, unsets = payload.get("set", []), payload.get("unset", [])
    if not isinstance(sets, list) or not isinstance(unsets, list):
        raise StudioError("invalid_request", "set/unset ต้องเป็นรายการ", 422)
    text = apply_edits(current.text, sets, unsets)
    warnings: list[Issue] = []
    lost = sum((Counter(comments(current.text)) - Counter(comments(text))).values())
    if lost > 0:
        warnings.append(issue("comments_dropped", f"comment ในไฟล์เดิม {lost} รายการจะหายไป — ตรวจใน diff ก่อนบันทึก"))
    return text, warnings


def encode_for_bmad(current: fs.TextDoc, text: str) -> tuple[bytes, list[Issue]]:
    """bytes ที่จะเขียน: EOL เดิม (AD-6) แต่ **ไม่มี BOM** — BMAD (tomllib) อ่าน TOML ที่มี BOM ไม่ได้."""
    if not current.exists:
        return text.encode("utf-8"), []
    if current.bom or current.encoding != "utf-8":
        stripped = fs.TextDoc(raw=current.raw[len(current.bom):].decode(current.encoding).encode("utf-8"),
                              text=current.text, encoding="utf-8", bom=b"", eol=current.eol, exists=True)
        return stripped.encode(text), [issue("bom_removed", "ไฟล์เดิมมี BOM ที่ BMAD อ่านไม่ได้ — จะบันทึกเป็น UTF-8 ไม่มี BOM")]
    return current.encode(text), []


# -- ค่า effective ผ่าน script (AD-10) -------------------------------------------------------------

def _unavailable(exc: BmadUnavailable) -> Issue:
    return issue(exc.code, exc.message_th, reason=exc.reason)


def rows(base: Mapping[str, Any], team: Mapping[str, Any], user: Mapping[str, Any],
         effective: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """แถวต่อคีย์ (leaf) จาก base/team/user/effective — ไม่คำนวณ merge: effective มาจาก script."""
    maps = [leaves(base), leaves(team), leaves(user), leaves(effective or {})]
    keys = sorted(set().union(*maps))
    out = []
    for path in keys:
        values = [m.get(path, _MISSING) for m in maps]
        out.append({"key": dotted(path), "id": path_key(path), "path": list(path),
                    **{name: (None if v is _MISSING else v) for name, v in zip(("base", "team", "user", "effective"), values)},
                    "has": {name: v is not _MISSING for name, v in zip(("base", "team", "user", "effective"), values)}})
    return out


def _layer(paths_root: Any, rel: str) -> dict[str, Any]:
    path = paths_root / rel
    try:
        raw = fs.read_bytes(path)
        doc = fs.decode(raw) if raw is not None else None
    except (StudioError, OSError):
        return {"exists": True, "text": None, "sha256": None, "values": {},
                "issue": issue("file_unreadable", "อ่านไฟล์นี้ไม่ได้")}
    if raw is None or doc is None:
        return {"exists": False, "text": "", "sha256": None, "values": {}, "issue": None}
    data, problem = parse_bytes(raw)
    return {"exists": True, "text": doc.text, "sha256": doc.sha256, "values": data or {}, "issue": problem}


def config_view(scripts: BmadScripts) -> dict[str, Any]:
    """มุมมองของ ``/bmad/config``: base (installer, อ่านอย่างเดียว) → team/user (แก้ได้) → effective."""
    root = scripts.paths.root
    team, user = _layer(root, CONFIG_TEAM), _layer(root, CONFIG_USER)
    issues: list[Issue] = []
    base: dict[str, Any] | None
    try:
        with scripts.mirror(installer_only=True) as mirror:
            base = scripts.resolve_config_at(mirror)
    except BmadUnavailable as exc:
        base = None
        issues.append(_unavailable(exc))
    try:
        effective: dict[str, Any] | None = scripts.resolve_config()
    except BmadUnavailable as exc:
        effective = None
        if not any(i.get("reason") == exc.reason for i in issues):
            issues.append(_unavailable(exc))
    return {
        "layers": {"team": {k: v for k, v in team.items() if k != "values"},
                   "user": {k: v for k, v in user.items() if k != "values"}},
        "targets": {"team": CONFIG_TEAM, "user": CONFIG_USER},
        "rows": rows(base or {}, team["values"], user["values"], effective),
        "baseAvailable": base is not None,
        "effectiveAvailable": effective is not None,
        "issues": issues,
    }


MAX_VIEW_CHANGES = 200


def effective_view(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> dict[str, Any]:
    """``view`` ของ preview: คีย์ที่ค่าที่ใช้จริง (บนเครื่องนี้) เปลี่ยน — ``before`` = None คือไม่รู้ค่าก่อนแก้."""
    if before is None:
        return {"changed": [], "changedTotal": 0, "beforeAvailable": False}
    now = leaves(after)
    changed = [{"key": dotted(p), "id": path_key(p), "path": list(p), "value": now.get(p), "removed": p not in now}
               for p in changed_paths(before, after)]
    return {"changed": changed[:MAX_VIEW_CHANGES], "changedTotal": len(changed), "beforeAvailable": True}


def _resolve_issue(exc: BmadUnavailable) -> Issue:
    if exc.reason == "script_failed":
        return issue("bmad_resolve_failed", "BMAD อ่าน config หลังแก้ไม่ได้ (resolve script ล้ม) — ตรวจ keyed array/ชนิดค่า",
                     reason=exc.reason)
    return issue(exc.code, "ตรวจผลกับ resolve script ของ BMAD ไม่ได้ จึงบันทึกไม่ได้ — " + exc.message_th, reason=exc.reason)


def resolve_issues(scripts: BmadScripts, rel: str, proposed: bytes) -> list[Issue]:
    """path validator ของ rule ``bmad-custom`` (ทุก kind, preview และ commit ใต้ lock): ไฟล์ config ต้องให้
    ``resolve_config.py`` รันผ่านบน mirror ที่ใส่ bytes นี้ — ใช้ script ไม่ได้ = ปฏิเสธ (fail closed)."""
    if rel.lower() not in CONFIG_TARGETS:
        return []
    try:
        scripts.resolve_config_with(rel, proposed)
    except BmadUnavailable as exc:
        return [_resolve_issue(exc)]
    return []


def path_validators(scripts: BmadScripts) -> dict[str, Callable[[str, bytes], list[Issue]]]:
    return {RULE: lambda rel, proposed: resolve_issues(scripts, rel, proposed)}


class ConfigKind:
    """``ChangeRequest{kind: "config", target: _bmad/custom/config(.user).toml, payload: {set, unset} | {text}}``.

    ความถูกต้องกับ BMAD ตรวจที่ :func:`resolve_issues` (ทุก kind, ซ้ำตอน commit); kind นี้คำนวณเฉพาะ ``view``.
    """

    name = "config"
    rules = frozenset({RULE})
    check_whitespace = True

    def __init__(self, scripts: BmadScripts) -> None:
        self.scripts = scripts

    @staticmethod
    def check_target(target: GuardedPath) -> None:
        """ตัวสะกดตรงเท่านั้น — ``CONFIG.toml`` เปิดไฟล์เดียวกันบน NTFS แต่ไม่ใช่ชื่อที่ BMAD อ่าน."""
        if target.rel not in CONFIG_TARGETS:
            raise StudioError("path_forbidden", "kind config เขียนได้เฉพาะ _bmad/custom/config.toml และ config.user.toml",
                              403, {"reason": "kind_target"})

    def propose(self, target: GuardedPath, current: fs.TextDoc, payload: Mapping[str, Any]) -> Proposal:
        self.check_target(target)
        text, warnings = proposed_text(current, payload)
        proposed, encode_warnings = encode_for_bmad(current, text)
        view: dict[str, Any] | None = None
        if not validate_toml(proposed):
            try:
                after = self.scripts.resolve_config_with(target.rel, proposed)
                view = effective_view(self._effective_now(), after)
            except BmadUnavailable:
                view = None  # error มาจาก path validator ของ pipeline
        return Proposal(proposed, warnings + encode_warnings, [], view=view)

    def _effective_now(self) -> dict[str, Any] | None:
        try:
            return self.scripts.resolve_config()
        except BmadUnavailable:
            return None


__all__ = ["ACTIVATION_KEYS", "CONFIG_TARGETS", "ConfigKind", "RULE", "apply_edits", "classify", "comments",
           "config_view", "impact_rules", "leaves", "parse_bytes", "path_key", "path_validators", "proposed_text",
           "resolve_issues", "rows", "validate_toml"]
