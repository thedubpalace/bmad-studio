"""ข้อผิดพลาดของ core ที่ API แปลงเป็น envelope ตาม AD-12."""
from __future__ import annotations


class StudioError(Exception):
    """Error ที่ผู้ใช้เห็นได้: ``code`` snake_case, ``message_th`` ภาษาไทยไม่มี path/secret."""

    def __init__(self, code: str, message_th: str, status: int = 400, details: object | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message_th = message_th
        self.status = status
        self.details = details


class StartupError(Exception):
    """การตั้งค่าไม่พร้อม: process ต้องจบด้วย exit code != 0 ก่อน bind.

    ``problems`` เป็นรายการข้อความที่บอกว่าขาดอะไร — ห้ามใส่ค่า secret.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
