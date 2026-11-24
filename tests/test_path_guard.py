"""Path guard ตาม AD-2: allowlist, deny ถาวร, traversal, link และเทคนิค path ของ Windows."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bmad_studio.core.errors import StudioError
from bmad_studio.core.path_guard import PathGuard, check_writable


@pytest.fixture
def guard(veda_root: Path) -> PathGuard:
    (veda_root / ".veda" / "projects" / "demo").mkdir()
    return PathGuard(veda_root)


def _forbidden(guard: PathGuard, path: str | Path) -> str:
    with pytest.raises(StudioError) as exc:
        guard.check_writable(path)
    assert exc.value.code == "path_forbidden" and exc.value.status == 403
    assert "/" not in exc.value.message_th and "\\" not in exc.value.message_th
    return exc.value.details["reason"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("rel", "rule"),
    [
        (".claude/agents/developer.md", "agent"),
        (".claude/agents/.backup/2026/developer.md", "agent-backup"),
        ("CLAUDE.md", "claude-md"),
        ("_bmad/custom/config.toml", "bmad-custom"),
        ("_bmad/custom/bmad-build.user.toml", "bmad-custom"),
        (".veda/flow.default.yaml", "flow-default"),
        (".veda/projects/demo/flow.yaml", "flow-project"),
        (".claude\\agents\\developer.md", "agent"),
    ],
)
def test_allowlisted_paths(guard: PathGuard, rel: str, rule: str) -> None:
    result = guard.check_writable(rel)
    assert result.rule == rule
    assert result.path.is_absolute()
    assert os.path.normcase(str(result.path)).startswith(os.path.normcase(str(guard.root)))


def test_absolute_path_inside_root_allowed(guard: PathGuard) -> None:
    assert guard.check_writable(guard.root / "CLAUDE.md").rel == "CLAUDE.md"


@pytest.mark.parametrize(
    "rel",
    [
        ".veda/projects/registry.json",
        ".VEDA/Projects/REGISTRY.JSON",
        "_bmad/config.toml",
        "_BMAD/Config.Toml",
        "_bmad/custom/customize.toml",
        ".claude/agents/customize.toml",
        ".claude/agents/.backup/x/customize.toml",
        ".claude/skills/bmad-build/customize.toml",
        ".claude/skills/bmad-build/SKILL.md",
        ".claude/agents/SKILL.md",
        ".claude/agents/skill.md",
        ".claude/agents/.backup/old/Skill.MD",
    ],
)
def test_permanent_deny_wins(guard: PathGuard, rel: str) -> None:
    assert _forbidden(guard, rel) == "denied"


def test_deny_wins_even_with_existing_file(guard: PathGuard) -> None:
    registry = guard.root / ".veda" / "projects" / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    assert _forbidden(guard, registry) == "denied"


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.md",
        ".claude/agents/../../../outside.md",
        ".claude/agents/../agents/developer.md",  # .. ห้ามแม้ผลยังอยู่ใน root
        "./CLAUDE.md",
        ".claude//agents/developer.md",
        "",
        "/etc/passwd",
        "\\\\server\\share\\CLAUDE.md",
        "//server/share/CLAUDE.md",
        "\\\\?\\C:\\Windows\\win.ini",
        "\\\\.\\PhysicalDrive0",
        "C:relative.md",
    ],
)
def test_traversal_and_odd_forms_rejected(guard: PathGuard, rel: str) -> None:
    _forbidden(guard, rel)


def test_absolute_outside_root_rejected(guard: PathGuard, tmp_path: Path) -> None:
    assert _forbidden(guard, tmp_path / "elsewhere" / "CLAUDE.md") == "outside_root"
    # prefix เดียวกันแต่คนละโฟลเดอร์ (veda2)
    assert _forbidden(guard, str(guard.root) + "2" + os.sep + "CLAUDE.md") == "outside_root"


def test_absolute_with_dotdot_back_into_root_rejected(guard: PathGuard) -> None:
    sneaky = str(guard.root / ".claude" / "agents") + os.sep + ".." + os.sep + ".." + os.sep + "CLAUDE.md"
    assert _forbidden(guard, sneaky) == "dot_segment"


@pytest.mark.parametrize(
    ("rel", "reason"),
    [
        (".claude/agents/dev.md:hidden", "bad_char"),  # NTFS alternate data stream
        (".claude/agents/dev.md::$DATA", "bad_char"),
        ("CLAUDE.md.", "trailing_dot_space"),  # Windows ตัดจุดท้าย -> CLAUDE.md
        ("CLAUDE.md ", "trailing_dot_space"),
        (".claude/agents/.backup/x/SKILL.md.", "trailing_dot_space"),
        (".claude/agents/CON.md", "device_name"),
        (".claude/agents/nul.md", "device_name"),
        (".claude/agents/com1.md", "device_name"),
        (".claude/agents/DEVELO~1.MD", "short_name"),  # short name 8.3
        (".claude/agents/dev\x00.md", "invalid"),
        (".claude/agents/dev\n.md", "bad_char"),
        (".claude/agents/de*v.md", "bad_char"),
        (".claude/agents/de?v.md", "bad_char"),
        ("_bmad/custom/cu\u017ftomize.toml", "non_ascii"),  # \u017f ตรงกับ S บน NTFS
        (".claude/agents/\u017fKILL.md", "non_ascii"),
        (".claude/agents/dev\u212a.md", "non_ascii"),  # Kelvin sign
    ],
)
def test_windows_path_tricks_rejected(guard: PathGuard, rel: str, reason: str) -> None:
    assert _forbidden(guard, rel) == reason


@pytest.mark.parametrize(
    ("suffix", "reason"),
    [
        ("CLAUDE.md.", "trailing_dot_space"),
        ("CLAUDE.md ", "trailing_dot_space"),
        ("CLAUDE.md/", "dot_segment"),
        (".claude//agents/dev.md", "dot_segment"),
        (".claude./agents/dev.md", "trailing_dot_space"),
        (".claude/agents/dev.md:s", "bad_char"),
        ("", "root"),
    ],
)
def test_absolute_forms_get_same_checks_as_relative(guard: PathGuard, suffix: str, reason: str) -> None:
    for sep in ("/", "\\"):
        path = str(guard.root) + sep + suffix.replace("/", sep)
        assert _forbidden(guard, path) == reason


@pytest.mark.parametrize("value", [123, None, b"CLAUDE.md", ["CLAUDE.md"]])
def test_non_string_input_is_path_forbidden(guard: PathGuard, value: object) -> None:
    assert _forbidden(guard, value) == "invalid"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "rel",
    [
        "README.md",
        ".claude/agents/sub/dev.md",
        ".claude/agents/dev.txt",
        ".claude/agents/.hidden.md",
        ".claude/skills/bmad-build/workflow.md",
        "_bmad/scripts/resolve_config.py",
        "_bmad/custom/sub/x.toml",
        ".veda/projects/demo/requirements.md",
        ".veda/projects/demo/sub/flow.yaml",
        ".veda/projects/.hidden/flow.yaml",
        ".veda/projects/flow.yaml",
        ".claude/agents/.backup",
        ".git/config",
    ],
)
def test_not_allowlisted_rejected(guard: PathGuard, rel: str) -> None:
    assert _forbidden(guard, rel) == "not_allowlisted"


def _make_dir_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_junction_or_dir_symlink_out_of_root_rejected(guard: PathGuard, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.md").write_text("x", encoding="utf-8")
    agents = guard.root / ".claude" / "agents"
    agents.rmdir()
    _make_dir_link(agents, outside)
    assert _forbidden(guard, ".claude/agents/victim.md") == "link"
    assert _forbidden(guard, ".claude/agents/new.md") == "link"


def test_dir_link_inside_root_also_rejected(guard: PathGuard) -> None:
    # .veda/projects/demo2 -> .veda/projects/demo (ยังอยู่ใน root แต่ไม่ยอมเดา)
    _make_dir_link(guard.root / ".veda" / "projects" / "demo2", guard.root / ".veda" / "projects" / "demo")
    assert _forbidden(guard, ".veda/projects/demo2/flow.yaml") == "link"


def test_file_symlink_to_registry_rejected(guard: PathGuard) -> None:
    registry = guard.root / ".veda" / "projects" / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    link = guard.root / ".claude" / "agents" / "evil.md"
    try:
        link.symlink_to(registry)
    except OSError:
        pytest.skip("สร้าง file symlink ไม่ได้บนเครื่องนี้ (ต้องมีสิทธิ์/Developer Mode)")
    assert _forbidden(guard, ".claude/agents/evil.md") in {"link", "denied"}


def test_hard_link_rejected(guard: PathGuard) -> None:
    registry = guard.root / ".veda" / "projects" / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    link = guard.root / ".claude" / "agents" / "evil.md"
    os.link(registry, link)
    assert _forbidden(guard, ".claude/agents/evil.md") == "hard_link"


def test_directory_target_rejected(guard: PathGuard) -> None:
    (guard.root / ".claude" / "agents" / "dir.md").mkdir()
    assert _forbidden(guard, ".claude/agents/dir.md") == "not_regular_file"


def test_root_itself_via_link_is_resolved(tmp_path: Path, veda_root: Path) -> None:
    link_root = tmp_path / "veda-link"
    _make_dir_link(link_root, veda_root)
    guard = PathGuard(link_root)
    assert os.path.normcase(str(guard.root)) == os.path.normcase(os.path.realpath(veda_root))
    assert guard.check_writable("CLAUDE.md").rule == "claude-md"


def test_module_level_helper(veda_root: Path) -> None:
    assert check_writable("CLAUDE.md", veda_root).rule == "claude-md"
    with pytest.raises(StudioError):
        check_writable(".veda/projects/registry.json", veda_root)
