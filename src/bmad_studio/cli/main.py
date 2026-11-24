"""``bmad-studio`` command line.

- ``serve`` รัน server ที่ 127.0.0.1:5024 เท่านั้น
- ``openapi`` export OpenAPI (JSON) และ/หรือ TypeScript types ให้ web
(คำสั่ง flow resolve/validate มาใน epic 3)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bmad_studio.core.settings import DEFAULT_HOST, DEFAULT_PORT


def _openapi() -> dict:
    from bmad_studio.api.app import create_app
    from bmad_studio.core.settings import Settings

    # schema ไม่ขึ้นกับการตั้งค่าจริง: ใช้ค่าว่างและไม่ตรวจ startup
    return create_app(Settings(_env_file=None), check=False).openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmad-studio")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="รัน web server (bind 127.0.0.1 เท่านั้น)")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    export = sub.add_parser("openapi", help="export OpenAPI / TypeScript types")
    export.add_argument("--json", type=Path, help="เขียน OpenAPI JSON ไปที่ path นี้")
    export.add_argument("--ts", type=Path, help="เขียน TypeScript types ไปที่ path นี้")
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from bmad_studio.api.app import build_checked_app

        uvicorn.run(build_checked_app(), host=DEFAULT_HOST, port=args.port, workers=1)
        return 0
    if args.command == "openapi":
        from bmad_studio.cli.openapi_ts import render_ts

        schema = _openapi()
        if args.json:
            args.json.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        if args.ts:
            args.ts.write_text(render_ts(schema), encoding="utf-8", newline="\n")
        if not args.json and not args.ts:
            sys.stdout.write(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
