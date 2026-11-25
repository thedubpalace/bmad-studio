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
RAW_WRITE_ALLOWED = {
    SRC / "adapters" / "fs.py",
    SRC / "adapters" / "state.py",
    SRC / "api" / "audit.py",
    SRC / "cli" / "main.py",
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


def raw_write_calls(source: str) -> list[str]:
    hits: list[str] = []
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "shutil", "tempfile", "pathlib"}:
            for alias in node.names:
                name = alias.asname or alias.name
                if (node.module == "os" and alias.name in _OS_CALLS) or (
                        node.module == "shutil" and alias.name in _SHUTIL_WRITES) or node.module == "tempfile":
                    imported.add(name)
                    hits.append(f"{node.lineno}:from {node.module} import {alias.name}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in imported:
            hits.append(f"{node.lineno}:{func.id}()")
        elif isinstance(func, ast.Name) and func.id == "open":
            if _writes(_mode_of(node)):
                hits.append(f"{node.lineno}:open({_mode_of(node)})")
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            if owner == "shutil" and func.attr in _SHUTIL_WRITES:
                hits.append(f"{node.lineno}:shutil.{func.attr}")
            elif owner == "tempfile":
                hits.append(f"{node.lineno}:tempfile.{func.attr}")
            elif owner == "os" and func.attr in _OS_CALLS:
                hits.append(f"{node.lineno}:os.{func.attr}")
            elif func.attr in _WRITE_METHODS:
                hits.append(f"{node.lineno}:.{func.attr}")
            elif func.attr == "open":
                # Path.open(mode) — mode เป็น argument ตัวแรก
                mode: str | None = None
                if node.args:
                    arg = node.args[0]
                    mode = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "?"
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode = kw.value.value if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str) else "?"
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
    }
    for source, expected in planted.items():
        assert bool(raw_write_calls(source)) is expected, source
