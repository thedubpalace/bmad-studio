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
