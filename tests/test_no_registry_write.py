"""ไม่มีโมดูลใดใน src เปิด registry.json ในโหมดเขียน (REQ-047 a, AD-2, AD-8).

ตรวจสองชั้น:
(1) ชื่อ ``registry`` (รวมแบบต่อ string) อ้างได้เฉพาะโมดูลใน ``MAY_NAME_REGISTRY`` — ตอนนี้มีแค่
    ``core/path_guard.py`` (รายการ deny). story ที่ต้อง **อ่าน** registry (เช่น status view) ต้องเพิ่ม
    โมดูลของตัวเองที่นี่อย่างชัดเจน และโมดูลนั้นต้องไม่ผ่านชั้นที่ 2
(2) ไม่มีการเรียกฟังก์ชันเขียน/ย้าย/ลบไฟล์ที่ argument อ้าง registry ในโมดูลใดเลย
ชั้นที่ 1 ปิดช่องที่ชั้นที่ 2 มองไม่เห็น (path ผ่านตัวแปร) เพราะชื่อต้องปรากฏที่ใดที่หนึ่งก่อน.
ด่านจริงตอน runtime คือ :mod:`bmad_studio.core.path_guard` (test_path_guard).
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bmad_studio"
MAY_NAME_REGISTRY = {
    SRC / "core" / "path_guard.py",
    # story 3-2: resolver อ่าน registry (อ่านอย่างเดียว: fs.read_bytes) และอ้างชื่อในข้อความ/skipReason
    SRC / "core" / "flow" / "status.py",
    SRC / "core" / "flow" / "resolve.py",
    # story 4-1: flow editor อ่าน hasFrontend/ชื่อโปรเจกต์ผ่าน status.find_project (อ่านอย่างเดียว)
    SRC / "core" / "flow" / "editor.py",
}
WRITE_CALLS = {
    "open", "write_text", "write_bytes", "write", "replace", "rename", "dump", "touch", "unlink",
    "remove", "copy", "copy2", "copyfile", "move", "rmtree", "mkstemp", "NamedTemporaryFile",
}


def _modules() -> list[Path]:
    modules = sorted(SRC.rglob("*.py"))
    # กัน scan ว่างแล้วผ่าน: ต้องเห็นโมดูลหลักที่รู้จัก
    for known in ("core/path_guard.py", "adapters/fs.py", "api/app.py"):
        assert SRC / known in modules
    return modules


def _strings(node: ast.AST) -> str:
    """string literal ทั้งหมดใต้ node ต่อกัน (จับ "regis" + "try.json" ได้)."""
    return "".join(
        sub.value for sub in ast.walk(node) if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    ).lower()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def scan_registry_names(source: str) -> bool:
    tree = ast.parse(source)
    return any("registry" in _strings(node) for node in ast.walk(tree) if isinstance(node, (ast.Expr, ast.Assign, ast.Call, ast.BinOp, ast.JoinedStr, ast.Constant)))


def scan_write_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) in WRITE_CALLS and "registry" in _strings(node)
    ]


def test_registry_only_named_in_allowed_modules() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in _modules()
        if path not in MAY_NAME_REGISTRY and scan_registry_names(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_write_call_references_registry() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{line}"
        for path in _modules()
        for line in scan_write_calls(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_scanners_detect_planted_violations() -> None:
    assert scan_write_calls("open('.veda/projects/registry.json', 'w').write('{}')\n")
    assert scan_write_calls("import shutil\nshutil.copy('x', root / '.veda' / 'projects' / 'registry.json')\n")
    assert scan_write_calls("p.write_text('{}', 'registry')\n")
    assert scan_registry_names("REG = 'regis' + 'try.json'\n")
    assert scan_registry_names("x = f'{root}/.veda/projects/registry.json'\n")
    assert not scan_write_calls("open('CLAUDE.md', 'w')\n")
    assert not scan_registry_names("x = 'flow.yaml'\n")
