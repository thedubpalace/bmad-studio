"""รายชื่อ agent และ skill พร้อมคำอธิบาย ให้ Inspector ของ flow editor เลือก (story 4-1; 6-2/7-1 ขยายต่อ).

ชื่อมาจาก :meth:`bmad_studio.core.flow.validate.Catalog.scan` ตัวเดียวกับที่ validator ใช้ — ชื่อที่ editor
แสดงว่า "มี" จึงตรงกับที่ validator ยอมรับเสมอ (AD-1). อ่านอย่างเดียว; description อ่านจาก front matter
(``description:``) เฉพาะไฟล์ไม่เกิน :data:`MAX_DESCRIPTION_SOURCE` และตัดที่ :data:`MAX_DESCRIPTION`.
"""
from __future__ import annotations

import re
from pathlib import Path

from bmad_studio.adapters import fs
from bmad_studio.adapters.bmad import _frontmatter_value
from bmad_studio.core.errors import StudioError
from bmad_studio.core.flow.validate import Catalog
from bmad_studio.core.paths import VedaPaths

MAX_DESCRIPTION_SOURCE = 256 * 1024
MAX_DESCRIPTION = 400
#: ชื่อที่ flow อ้างได้ (Slug ของ schema) — ไฟล์ชื่ออื่น เช่น ``Developer.md`` ไม่ถูกเสนอให้เลือก
_SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.ASCII)


def _description(path: Path) -> str:
    try:
        raw = fs.read_bytes(path, MAX_DESCRIPTION_SOURCE)
        if raw is None:
            return ""
        text = fs.decode(raw).text
    except (StudioError, OSError):
        return ""
    value = " ".join(_frontmatter_value(text, "description").split())
    return value if len(value) <= MAX_DESCRIPTION else value[: MAX_DESCRIPTION - 1] + "…"


def _ok(name: str) -> bool:
    return len(name) <= 64 and _SLUG.fullmatch(name) is not None


def agent_names(paths: VedaPaths, catalog: Catalog | None = None) -> list[dict[str, str]]:
    catalog = catalog or Catalog.scan(paths)
    return [{"name": name, "description": _description(paths.agents_dir / f"{name}.md")}
            for name in sorted(catalog.agents) if _ok(name)]


def skill_list(paths: VedaPaths, catalog: Catalog | None = None) -> list[dict[str, str]]:
    catalog = catalog or Catalog.scan(paths)
    return [{"id": name, "description": _description(paths.skills_dir / name / "SKILL.md")}
            for name in sorted(catalog.skills) if _ok(name)]
