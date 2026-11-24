"""State ของแอปเองใต้ ``.runtime/`` (ไม่ใช่ไฟล์ภายนอก — ไม่ผ่าน path guard ของ AD-2).

- ``previews/<preview_id>.json`` + ``.bin`` — preview ที่รอ confirm (proposed bytes)
- ``previews/<preview_id>.result.json`` — ผล commit (idempotency ของ AD-5 ข้อ 4)
- ``backups/<change_id>/`` — ``original.bin`` (ถ้าเดิมมีไฟล์) + ``meta.json``
- ``changes.jsonl`` — changelog แบบ append-only

id ทุกตัวต้องเป็น uuid4 hex (32 ตัว) — ตรวจก่อนประกอบ path.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_ID = re.compile(r"[0-9a-f]{32}")


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class RuntimeState:
    def __init__(self, runtime_dir: Path | str) -> None:
        self.root = Path(runtime_dir)
        self.previews = self.root / "previews"
        self.backups = self.root / "backups"
        self.changes_path = self.root / "changes.jsonl"
        self._append_lock = threading.Lock()

    # -- previews ------------------------------------------------------------
    def _preview_path(self, preview_id: str, suffix: str) -> Path:
        if not valid_id(preview_id):
            raise ValueError("invalid preview id")
        return self.previews / f"{preview_id}{suffix}"

    def save_preview(self, preview_id: str, meta: dict[str, Any], proposed: bytes) -> None:
        _atomic(self._preview_path(preview_id, ".bin"), proposed)
        try:
            _atomic(self._preview_path(preview_id, ".json"), _json(meta))
        except BaseException:
            try:
                self._preview_path(preview_id, ".bin").unlink()
            except OSError:
                pass
            raise

    def load_preview(self, preview_id: str) -> tuple[dict[str, Any], bytes] | None:
        if not valid_id(preview_id):
            return None
        try:
            meta = json.loads(self._preview_path(preview_id, ".json").read_bytes())
            proposed = self._preview_path(preview_id, ".bin").read_bytes()
        except (OSError, ValueError):
            return None
        return (meta, proposed) if isinstance(meta, dict) else None

    def save_result(self, preview_id: str, result: dict[str, Any]) -> None:
        _atomic(self._preview_path(preview_id, ".result.json"), _json(result))

    def load_result(self, preview_id: str) -> dict[str, Any] | None:
        if not valid_id(preview_id):
            return None
        try:
            result = json.loads(self._preview_path(preview_id, ".result.json").read_bytes())
        except (OSError, ValueError):
            return None
        return result if isinstance(result, dict) else None

    def sweep_previews(self, max_age_seconds: float, now: float | None = None) -> None:
        """ลบไฟล์ใน ``previews/`` ที่เก่ากว่า ``max_age_seconds`` (รวม .bin/.result/.tmp ที่ค้าง).

        ไม่ลบ preview ที่ยังไม่หมดอายุเพื่อจำกัดจำนวน — ผู้เรียกปฏิเสธ preview ใหม่แทน (กันการไล่
        preview ของคนอื่นทิ้ง).
        """
        now = time.time() if now is None else now
        try:
            entries = list(self.previews.iterdir())
        except OSError:
            return
        for path in entries:
            try:
                if path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
            except OSError:
                continue

    def count_previews(self) -> int:
        try:
            return sum(1 for _ in self.previews.glob("*.json")) - sum(1 for _ in self.previews.glob("*.result.json"))
        except OSError:
            return 0

    # -- backups / changelog -----------------------------------------------
    def backup_dir(self, change_id: str) -> Path:
        if not valid_id(change_id):
            raise ValueError("invalid change id")
        return self.backups / change_id

    def write_backup(self, change_id: str, original: bytes | None, meta: dict[str, Any]) -> Path:
        directory = self.backup_dir(change_id)
        directory.mkdir(parents=True, exist_ok=False)
        try:
            if original is not None:
                _atomic(directory / "original.bin", original)
            _atomic(directory / "meta.json", _json(meta))
        except BaseException:
            for child in directory.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                directory.rmdir()
            except OSError:
                pass
            raise
        return directory

    def read_backup(self, change_id: str) -> tuple[dict[str, Any], bytes | None] | None:
        try:
            directory = self.backup_dir(change_id)
            meta = json.loads((directory / "meta.json").read_bytes())
        except (OSError, ValueError):
            return None
        original_path = directory / "original.bin"
        try:
            original = original_path.read_bytes() if original_path.is_file() else None
        except OSError:
            return None
        if not isinstance(meta, dict):
            return None
        return meta, original

    def append_change(self, record: dict[str, Any]) -> None:
        line = _json(record) + b"\n"
        with self._append_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.changes_path, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def read_changes(self) -> list[dict[str, Any]]:
        try:
            raw = self.changes_path.read_bytes()
        except OSError:
            return []
        items: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                items.append(item)
        return items
