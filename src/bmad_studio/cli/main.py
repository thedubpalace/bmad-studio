"""``bmad-studio`` command line.

``serve`` รัน server ที่ 127.0.0.1:5024 เท่านั้น (คำสั่ง flow resolve/validate มาใน epic 3).
"""
from __future__ import annotations

import argparse

from bmad_studio.core.settings import DEFAULT_HOST, DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmad-studio")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="รัน web server (bind 127.0.0.1 เท่านั้น)")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from bmad_studio.api.app import build_checked_app

        uvicorn.run(build_checked_app(), host=DEFAULT_HOST, port=args.port, workers=1)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
