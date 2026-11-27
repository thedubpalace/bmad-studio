"""อ่าน BMAD ที่ติดตั้งใน veda (AD-10).

- ค่า config ที่ merge แล้วมาจาก ``uv run _bmad/scripts/resolve_config.py --project-root <VEDA_ROOT>``
  เท่านั้น (subprocess, ``cwd=VEDA_ROOT``, timeout, ไม่ใช้ shell) — ห้าม merge TOML เอง
- หา ``uv`` ไม่เจอ / exit != 0 / timeout / output ไม่ใช่ JSON → :class:`BmadUnavailable`
  (code ``bmad_unavailable`` ข้อความไทย) โดยส่วนอื่น (รายการ skill จากโฟลเดอร์) ยังใช้ได้
- cache ผลตาม mtime ของไฟล์ input
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from bmad_studio.adapters.mirror import MirrorIncomplete, bmad_mirror
from bmad_studio.core.paths import VedaPaths

UNAVAILABLE = "bmad_unavailable"


class BmadUnavailable(Exception):
    """resolve script ใช้ไม่ได้. ``reason`` = uv_missing | script_missing | script_failed | timeout | bad_output."""

    def __init__(self, reason: str, message_th: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = UNAVAILABLE
        self.message_th = message_th


@dataclass(frozen=True)
class SkillInfo:
    id: str
    description: str
    override_team: bool
    override_user: bool


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)


def _frontmatter_value(text: str, key: str) -> str:
    match = _FRONTMATTER.match(text)
    if not match:
        return ""
    lines = match.group(1).splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(f"{key}:"):
            continue
        value = line[len(key) + 1:].strip()
        if value in ("|", ">", "|-", ">-"):  # block scalar
            block = []
            for nxt in lines[index + 1:]:
                if nxt and not nxt[0].isspace():
                    break
                block.append(nxt.strip())
            return (" " if value.startswith(">") else "\n").join(part for part in block if part).strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            return value[1:-1].replace("''", "'")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                return json.loads(value)
            except ValueError:
                return value[1:-1]
        return value
    return ""


def list_skills(paths: VedaPaths) -> list[SkillInfo]:
    """skill ``bmad-*`` จากโฟลเดอร์จริงใน ``.claude/skills`` พร้อม description และสถานะ override."""
    skills: list[SkillInfo] = []
    if not paths.skills_dir.is_dir():
        return skills
    for folder in sorted(paths.skills_dir.iterdir(), key=lambda p: p.name):
        if not folder.is_dir() or not folder.name.startswith("bmad-"):
            continue
        description = ""
        skill_md = folder / "SKILL.md"
        try:
            description = _frontmatter_value(skill_md.read_text(encoding="utf-8-sig"), "description")
        except (OSError, UnicodeDecodeError):
            description = ""
        skills.append(SkillInfo(
            id=folder.name,
            description=description,
            override_team=(paths.bmad_custom_dir / f"{folder.name}.toml").is_file(),
            override_user=(paths.bmad_custom_dir / f"{folder.name}.user.toml").is_file(),
        ))
    return skills


_VERSION = re.compile(r"^installation:\s*$(?:\n[ \t]+.*$)*?\n[ \t]+version:\s*['\"]?([^'\"\s]+)", re.MULTILINE)


def read_version(paths: VedaPaths) -> str | None:
    """เวอร์ชัน BMAD จาก ``_bmad/_config/manifest.yaml`` (installer-managed, อ่านอย่างเดียว)."""
    try:
        text = (paths.bmad_dir / "_config" / "manifest.yaml").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    match = _VERSION.search(text.replace("\r\n", "\n"))
    return match.group(1) if match else None


class BmadScripts:
    """เรียก resolve script ทางการของ BMAD ผ่าน ``uv`` พร้อม cache ตาม mtime."""

    def __init__(self, paths: VedaPaths, timeout_seconds: float = 20.0, uv_path: str | None = None) -> None:
        self.paths = paths
        self.timeout = timeout_seconds
        self._uv_path = uv_path
        self._lock = threading.Lock()
        self._config_cache: tuple[tuple, dict] | None = None
        #: ผลของ resolve_config บน mirror ที่แทนไฟล์หนึ่งด้วย bytes ที่เสนอ — key รวม stamp ของ layer ทุกไฟล์
        #: (layer อื่นเปลี่ยน = รันใหม่) จึงใช้ร่วมระหว่าง validator, view ของ preview และการตรวจซ้ำตอน commit ได้
        self._proposed_cache: dict[tuple, dict | BmadUnavailable] = {}

    def uv(self) -> str | None:
        return self._uv_path or shutil.which("uv")

    @property
    def resolve_config_script(self) -> Path:
        return self.paths.bmad_scripts_dir / "resolve_config.py"

    def _config_inputs(self) -> list[Path]:
        bmad = self.paths.bmad_dir
        return [bmad / "config.toml", bmad / "config.user.toml", bmad / "custom" / "config.toml",
                bmad / "custom" / "config.user.toml", self.resolve_config_script,
                self.paths.bmad_scripts_dir / "config_utils.py"]

    @staticmethod
    def _stamp(files: list[Path]) -> tuple:
        stamp = []
        for path in files:
            try:
                stat = path.stat()
                stamp.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamp.append((str(path), None, None))
        return tuple(stamp)

    def run_json(self, script: Path, *args: str) -> object:
        uv = self.uv()
        if not uv:
            raise BmadUnavailable("uv_missing", "ไม่พบ uv — ติดตั้ง uv แล้วกด ลองใหม่")
        if not script.is_file():
            raise BmadUnavailable("script_missing", f"ไม่พบสคริปต์ {script.name} ของ BMAD ใน _bmad/scripts")
        command = [uv, "run", "--no-project", "--quiet", str(script), *args]
        try:
            result = subprocess.run(command, cwd=self.paths.root, capture_output=True, timeout=self.timeout,
                                    shell=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise BmadUnavailable("timeout", f"สคริปต์ {script.name} ทำงานเกิน {self.timeout:g} วินาที") from None
        except OSError:
            raise BmadUnavailable("uv_missing", "เรียก uv ไม่ได้ — ตรวจการติดตั้ง uv แล้วกด ลองใหม่") from None
        if result.returncode != 0:
            raise BmadUnavailable("script_failed",
                                  f"สคริปต์ {script.name} ทำงานไม่สำเร็จ (exit {result.returncode}) — ตรวจไฟล์ config ของ BMAD")
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BmadUnavailable("bad_output", f"อ่านผลลัพธ์ของ {script.name} ไม่ได้") from None

    # -- temp mirror (AD-10: preview รัน script กับไฟล์ proposed ผ่าน --project-root) ----------------
    @contextmanager
    def mirror(self, files: Mapping[str, bytes | None] | None = None, *,
               installer_only: bool = False) -> Iterator[Path]:
        """ดู :func:`bmad_studio.adapters.mirror.bmad_mirror` — temp dir นอก veda ลบทิ้งเมื่อจบ.
        สร้างไม่ได้/ไม่ครบ (temp เต็ม, ไฟล์ใหญ่/อ่านไม่ได้) = :class:`BmadUnavailable` ``mirror_failed``."""
        with ExitStack() as stack:
            try:
                root = stack.enter_context(bmad_mirror(self.paths, files, installer_only=installer_only))
            except (OSError, RuntimeError, MirrorIncomplete):
                raise BmadUnavailable("mirror_failed",
                                      "จำลองไฟล์ config ของ BMAD เพื่อตรวจไม่ครบ (ไฟล์ใหญ่เกิน/อ่านไม่ได้/temp เต็ม)") from None
            yield root

    def resolve_config_at(self, root: Path) -> dict:
        """``resolve_config.py --project-root <root>`` (ไม่ cache) — root = mirror ชั่วคราว."""
        data = self.run_json(self.resolve_config_script, "--project-root", str(root))
        if not isinstance(data, dict):
            raise BmadUnavailable("bad_output", "ผลลัพธ์ของ resolve_config.py ไม่ใช่ object")
        return data

    def _mirror_stamp(self) -> tuple:
        custom = self.paths.bmad_custom_dir
        try:
            names = sorted(e.name for e in os.scandir(custom) if e.name.endswith(".toml"))
        except OSError:
            names = []
        return self._stamp(self._config_inputs()[:2] + [custom / n for n in names] + self._config_inputs()[4:])

    def resolve_config_with(self, rel: str, proposed: bytes) -> dict:
        """effective เมื่อแทน ``rel`` (ใน ``_bmad/custom``) ด้วย ``proposed`` — cache ตาม (rel, sha, stamp ของทุก layer).
        ล้ม = :class:`BmadUnavailable` (cache ผลล้มด้วย ยกเว้น timeout/หา uv ไม่เจอ ที่อาจหายเอง)."""
        key = (rel.lower(), hashlib.sha256(proposed).hexdigest(), self._mirror_stamp())
        with self._lock:
            hit = self._proposed_cache.get(key)
        if isinstance(hit, BmadUnavailable):
            raise hit
        if hit is not None:
            return hit
        try:
            with self.mirror({rel: proposed}) as root:
                data: dict | BmadUnavailable = self.resolve_config_at(root)
        except BmadUnavailable as exc:
            if exc.reason in {"timeout", "uv_missing", "mirror_failed"}:
                raise
            data = exc
        with self._lock:
            if len(self._proposed_cache) >= 32:
                self._proposed_cache.pop(next(iter(self._proposed_cache)))
            self._proposed_cache[key] = data
        if isinstance(data, BmadUnavailable):
            raise data
        return data

    def resolve_config(self) -> dict:
        """ผลของ ``resolve_config.py --project-root <VEDA_ROOT>`` (cache ตาม mtime ของ input)."""
        stamp = self._stamp(self._config_inputs())
        with self._lock:
            if self._config_cache and self._config_cache[0] == stamp:
                return self._config_cache[1]
        data = self.run_json(self.resolve_config_script, "--project-root", str(self.paths.root))
        if not isinstance(data, dict):
            raise BmadUnavailable("bad_output", "ผลลัพธ์ของ resolve_config.py ไม่ใช่ object")
        with self._lock:
            self._config_cache = (stamp, data)
        return data
