"""path ภายนอกทุกตัวสร้างจากที่นี่ (AD-2) — ไม่ hard-code path ของ veda ที่อื่น."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VedaPaths:
    root: Path

    @property
    def bmad_dir(self) -> Path:
        return self.root / "_bmad"

    @property
    def bmad_scripts_dir(self) -> Path:
        return self.bmad_dir / "scripts"

    @property
    def bmad_custom_dir(self) -> Path:
        return self.bmad_dir / "custom"

    @property
    def skills_dir(self) -> Path:
        return self.root / ".claude" / "skills"

    @property
    def agents_dir(self) -> Path:
        return self.root / ".claude" / "agents"

    @property
    def veda_state_dir(self) -> Path:
        return self.root / ".veda"
