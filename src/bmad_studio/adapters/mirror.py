"""Temp mirror ของ ``_bmad`` สำหรับรัน resolve script กับไฟล์ proposed (AD-10, story 6-1/6-3).

โมดูลเดียว (นอกจาก ``adapters.fs``/``adapters.state``) ที่มี primitive เขียนไฟล์ — และเขียนได้เฉพาะใต้
``tempfile.TemporaryDirectory`` ของตัวเอง (นอก ``VEDA_ROOT`` เสมอ, assert) แล้วลบทิ้งเมื่อจบ. veda อ่านอย่างเดียว.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from bmad_studio.core.paths import VedaPaths

MAX_MIRROR_FILES = 500
#: ขนาดสูงสุดต่อไฟล์ที่คัดลอก — เกิน = :class:`MirrorIncomplete` (ไม่รันกับชุดไฟล์ที่ขาด)
MAX_MIRROR_FILE_BYTES = 2 * 1024 * 1024


class MirrorIncomplete(Exception):
    """สร้าง mirror ให้ครบทุก layer ไม่ได้ — ผลของ script จะไม่ตรงกับที่ BMAD ใช้จริง จึงไม่รัน."""


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


@contextmanager
def bmad_mirror(paths: VedaPaths, files: Mapping[str, bytes | None] | None = None, *,
                installer_only: bool = False) -> Iterator[Path]:
    """โฟลเดอร์ชั่วคราวที่มี ``_bmad/config.toml``, ``_bmad/config.user.toml`` และ ``_bmad/custom/*.toml`` สำเนาจาก veda
    แล้วแทนด้วย ``files`` (rel ใต้ ``_bmad/custom`` → bytes, ``None`` = ไม่มีไฟล์นั้น).

    ``installer_only`` = ไม่คัด ``_bmad/custom`` (ค่า base ของ installer ล้วน ๆ).
    """
    bmad = paths.bmad_dir
    with tempfile.TemporaryDirectory(prefix="bmad-studio-mirror-") as tmp:
        root = Path(tmp)
        if _inside(root, paths.root) or _inside(paths.root, root):
            raise RuntimeError("temp mirror ต้องอยู่นอก VEDA_ROOT")
        custom = root / "_bmad" / "custom"
        custom.mkdir(parents=True)
        sources = [bmad / "config.toml", bmad / "config.user.toml"]
        if not installer_only:
            try:
                entries = sorted(os.scandir(paths.bmad_custom_dir), key=lambda e: e.name)
            except OSError:
                entries = []
            tomls = []
            for e in entries:
                if not e.name.lower().endswith(".toml"):
                    continue
                if not e.is_file(follow_symlinks=False):
                    raise MirrorIncomplete(e.name)  # link/โฟลเดอร์ชื่อ .toml — BMAD อาจอ่านได้ แต่เราจำลองไม่ได้
                tomls.append(Path(e.path))
            if len(tomls) > MAX_MIRROR_FILES:
                raise MirrorIncomplete("too_many_files")
            sources += tomls
        for source in sources:
            rel = source.relative_to(paths.root).as_posix()
            if not source.exists():
                continue  # layer ที่ไม่มีไฟล์ = BMAD ก็ไม่อ่าน
            try:
                if not source.is_file() or source.stat().st_size > MAX_MIRROR_FILE_BYTES:
                    raise MirrorIncomplete(rel)
                shutil.copyfile(source, root / rel)
            except OSError:
                raise MirrorIncomplete(rel) from None
        for rel, data in (files or {}).items():
            target = root / rel
            if target.parent != custom or not _inside(target, custom):
                raise ValueError("mirror แทนไฟล์ได้เฉพาะใน _bmad/custom")
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(data)
        yield root
