"""REQ-043 a / AD-5: ไม่มี handler ใดเขียนไฟล์ภายนอกนอก ``core.writes``.

(1) ไล่ทุก route ที่ลงทะเบียนใน app: module ของ endpoint ต้องไม่อ้างฟังก์ชันเขียนของ ``adapters.fs``
(2) ทั้ง ``src``: มีแค่ ``core/writes.py`` (และตัวนิยามใน ``adapters/fs.py``) ที่อ้าง ``write_bytes_atomic``
    หรือ import ``adapters.fs`` แบบที่เข้าถึง ``write*`` ได้
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

from bmad_studio.adapters import fs
from bmad_studio.api.app import create_app
from bmad_studio.core.settings import Settings

from .test_access_policy import _walk

SRC = Path(__file__).resolve().parents[1] / "src" / "bmad_studio"
FS_WRITERS = {name for name in dir(fs) if name.startswith(("write", "remove")) and callable(getattr(fs, name))}
ALLOWED = {SRC / "core" / "writes.py", SRC / "adapters" / "fs.py"}


def _references_fs_writer(source: str) -> list[int]:
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FS_WRITERS:
            hits.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in FS_WRITERS:
            hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").endswith("adapters.fs"):
            if any(alias.name in FS_WRITERS or alias.name == "*" for alias in node.names):
                hits.append(node.lineno)
    return hits


def test_fs_has_a_writer_to_guard() -> None:
    assert {"write_bytes_atomic", "remove_file"} <= FS_WRITERS


def test_no_route_handler_module_uses_fs_writers(settings: Settings) -> None:
    app = create_app(settings)
    modules = set()
    for _prefix, route in _walk(app.router.routes):
        module = sys.modules[route.endpoint.__module__]
        modules.add(Path(inspect.getfile(module)).resolve())
    assert SRC / "api" / "routes" / "changes.py" in modules  # walker เห็น route ของ story นี้
    offenders = {str(m): _references_fs_writer(m.read_text(encoding="utf-8")) for m in modules}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_only_core_writes_references_fs_writers() -> None:
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        hits = _references_fs_writer(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(SRC))] = hits
    assert offenders == {}
    assert _references_fs_writer((SRC / "core" / "writes.py").read_text(encoding="utf-8"))


def test_scanner_detects_planted_usage() -> None:
    assert _references_fs_writer("from bmad_studio.adapters.fs import write_bytes_atomic\n")
    assert _references_fs_writer("from bmad_studio.adapters import fs\nfs.write_bytes_atomic(p, b'')\n")
    assert not _references_fs_writer("from bmad_studio.adapters import fs\nfs.read_doc(p)\n")


# -- N-scan (review epic 2): primitive เขียน/ย้าย/ลบไฟล์แบบดิบในโมดูลอื่น --
# ยกเว้นเฉพาะ: adapters/fs.py (ตัวเขียนไฟล์ภายนอก), adapters/state.py และ api/audit.py (.runtime ของแอปเอง),
# cli/main.py (export schema ตาม path ที่ผู้ใช้ CLI ระบุ — ไม่มีทางเข้าจาก HTTP)
RAW_WRITE_ALLOWED = {  # ยกเว้นที่ตั้งใจ (ดูคำอธิบายด้านบน)
    SRC / "adapters" / "fs.py",
    SRC / "adapters" / "state.py",
    SRC / "api" / "audit.py",
    SRC / "cli" / "main.py",
    SRC / "adapters" / "mirror.py",  # temp dir นอก veda เท่านั้น (story 6-1, AD-10) — test_config_editor ตรวจ
}
_WRITE_METHODS = {"write_text", "write_bytes", "touch", "unlink", "rmdir", "mkdir", "symlink_to", "hardlink_to"}
_OS_CALLS = {"replace", "rename", "renames", "remove", "unlink", "rmdir", "removedirs", "makedirs", "mkdir",
             "link", "symlink", "truncate", "open", "fdopen", "chmod", "utime"}
_MODES_WRITE = set("wax+")
_SHUTIL_WRITES = {"copy", "copy2", "copyfile", "copytree", "copymode", "copystat", "move", "rmtree", "chown",
                  "make_archive", "unpack_archive"}


def _mode_of(call: ast.Call) -> str | None:
    """mode ของ open(); ``"?"`` = ไม่ใช่ค่าคงที่ (ถือว่าอาจเขียน)."""
    if len(call.args) >= 2:
        arg = call.args[1]
        return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "?"
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value.value if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str) else "?"
    return None


def _writes(mode: str | None) -> bool:
    return mode == "?" or (mode is not None and bool(set(mode) & _MODES_WRITE))


_WATCHED_MODULES = {"os", "shutil", "tempfile", "pathlib", "zipfile", "tarfile"}
_ARCHIVE_CTORS = {"ZipFile", "PyZipFile", "open", "TarFile"}
_ARCHIVE_EXTRACT = {"extractall", "extract"}


def _const_str(node: ast.AST) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else "?"


def _archive_mode(call: ast.Call, position: int) -> str:
    """mode ของ ZipFile(file, mode)/tarfile.open(name, mode); ไม่ระบุ = "r" (อ่าน)."""
    for kw in call.keywords:
        if kw.arg == "mode":
            return _const_str(kw.value)
    if len(call.args) > position:
        return _const_str(call.args[position])
    return "r"


def _archive_writes(mode: str) -> bool:
    return mode == "?" or bool(set(mode.split(":")[0]) & set("wax"))


def raw_write_calls(source: str) -> list[str]:
    """primitive ที่เขียน/ย้าย/ลบไฟล์แบบดิบ (epic 2 retro item 1).

    ครอบคลุม: open/Path.open mode เขียน, Path.write_*/unlink/mkdir/..., Path.rename(x)/Path.replace(x)
    (ชี้ด้วยจำนวน argument: str.replace มี ≥ 2 argument เสมอ), os.*/shutil.*/tempfile.* รวม alias
    (``import os as o``, ``from os import replace as r``), zipfile/tarfile ที่เปิดแบบเขียนและ extract/extractall.
    """
    hits: list[str] = []
    tree = ast.parse(source)
    imported: set[str] = set()       # ชื่อฟังก์ชันเขียนที่ import ตรง
    archive_ctors: set[str] = set()  # ZipFile/TarFile ที่ import ตรง
    aliases: dict[str, str] = {}     # ชื่อใน module → module จริง
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _WATCHED_MODULES and (alias.asname or "." not in alias.name):
                    aliases[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and node.module in _WATCHED_MODULES:
            for alias in node.names:
                name = alias.asname or alias.name
                if (node.module == "os" and alias.name in _OS_CALLS) or (
                        node.module == "shutil" and alias.name in _SHUTIL_WRITES) or node.module == "tempfile":
                    imported.add(name)
                    hits.append(f"{node.lineno}:from {node.module} import {alias.name}")
                elif node.module in {"zipfile", "tarfile"} and alias.name in _ARCHIVE_CTORS:
                    archive_ctors.add(name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in imported:
            hits.append(f"{node.lineno}:{func.id}()")
        elif isinstance(func, ast.Name) and func.id in archive_ctors:
            if _archive_writes(_archive_mode(node, 1)):
                hits.append(f"{node.lineno}:{func.id}(write)")
        elif isinstance(func, ast.Name) and func.id == "open":
            if _writes(_mode_of(node)):
                hits.append(f"{node.lineno}:open({_mode_of(node)})")
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            module = aliases.get(owner, owner if owner in _WATCHED_MODULES else "")
            if module == "shutil" and func.attr in _SHUTIL_WRITES:
                hits.append(f"{node.lineno}:shutil.{func.attr}")
            elif module == "tempfile":
                hits.append(f"{node.lineno}:tempfile.{func.attr}")
            elif module == "os" and func.attr in _OS_CALLS:
                hits.append(f"{node.lineno}:os.{func.attr}")
            elif module in {"zipfile", "tarfile"} and func.attr in _ARCHIVE_CTORS:
                if _archive_writes(_archive_mode(node, 1)):
                    hits.append(f"{node.lineno}:{module}.{func.attr}(write)")
            elif func.attr in _ARCHIVE_EXTRACT:
                hits.append(f"{node.lineno}:.{func.attr}")
            elif func.attr in {"rename", "replace"} and len(node.args) + len(node.keywords) == 1:
                # Path.rename(target)/Path.replace(target); str.replace(old, new) มี ≥ 2 argument
                hits.append(f"{node.lineno}:.{func.attr}(path)")
            elif func.attr in _WRITE_METHODS:
                hits.append(f"{node.lineno}:.{func.attr}")
            elif func.attr == "open":
                # Path.open(mode) — mode เป็น argument ตัวแรก
                mode: str | None = None
                if node.args:
                    mode = _const_str(node.args[0])
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode = _const_str(kw.value)
                if _writes(mode):
                    hits.append(f"{node.lineno}:.open({mode})")
    return hits


def test_no_raw_write_primitives_outside_allowed_modules() -> None:
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        if path in RAW_WRITE_ALLOWED:
            continue
        hits = raw_write_calls(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(SRC))] = hits
    assert offenders == {}


def test_raw_scanner_detects_planted_writes() -> None:
    planted = {
        "open('x', 'w')": True, "open('x', 'a')": True, "open('x', mode='xb')": True, "open('x', 'r+')": True,
        "Path('x').write_text('y')": True, "p.write_bytes(b'')": True, "p.open('w')": True,
        "os.replace(a, b)": True, "os.rename(a, b)": True, "os.remove(a)": True, "shutil.copy(a, b)": True,
        "shutil.rmtree(a)": True, "p.unlink()": True, "shutil.move(a, b)": True, "shutil.which('uv')": False,
        "open(p, m)": True, "p.open(mode=m)": True, "p.open(m)": True, "from os import replace\nreplace(a, b)": True,
        "from shutil import move as mv\nmv(a, b)": True, "tempfile.NamedTemporaryFile(delete=False)": True,
        "from os import path": False,
        "open('x')": False, "open('x', 'rb')": False, "p.read_text()": False, "os.path.join(a, b)": False,
        # epic 2 retro item 1: Path.rename/replace, alias ของ os, zipfile/tarfile
        "p.rename(q)": True, "Path('a').replace(Path('b'))": True, "p.replace(target=q)": True,
        "s.replace('a', 'b')": False, "text.replace('\\r\\n', '\\n').strip()": False, "s.replace(a, b, 1)": False,
        "import os as o\no.replace(a, b)": True, "import os as o\no.remove(a)": True, "import shutil as sh\nsh.move(a, b)": True,
        "import tempfile as tf\ntf.mkstemp()": True, "import os as o\no.path.join(a, b)": False,
        "import os.path as osp\nosp.join(a, b)": False,
        "import zipfile\nzipfile.ZipFile(p, 'w')": True, "import zipfile\nzipfile.ZipFile(p, mode='a')": True,
        "import zipfile as z\nz.ZipFile(p, m)": True, "import zipfile\nzipfile.ZipFile(p)": False,
        "from zipfile import ZipFile\nZipFile(p, 'x')": True, "from zipfile import ZipFile as Z\nZ(p, 'w')": True,
        "from zipfile import ZipFile\nZipFile(p, 'r')": False, "zf.extractall(d)": True, "zf.extract(m, d)": True,
        "import tarfile\ntarfile.open(p, 'w:gz')": True, "import tarfile\ntarfile.open(p)": False,
        "import tarfile\ntarfile.open(p, mode='r:gz')": False,
    }
    for source, expected in planted.items():
        assert bool(raw_write_calls(source)) is expected, source
