"""``bmad-studio`` command line.

- ``serve`` รัน server ที่ 127.0.0.1:5024 เท่านั้น
- ``openapi`` export OpenAPI (JSON) และ/หรือ TypeScript types ให้ web
- ``flow schema`` export JSON Schema ของ flow.yaml (``docs/flow-schema.json``)
- ``flow resolve <project> [--track full|quick] [--json]`` ขั้นถัดไปของโปรเจกต์ (อ่านอย่างเดียว, REQ-048)
- ``flow validate [<project>] [--file PATH]`` ตรวจ flow (default/overlay) ตามกฎเดียวกับ resolver

exit code ของ ``flow``: 0 = สำเร็จ, 2 = flow ผิด/หาโปรเจกต์ไม่เจอ/อ่าน state ไม่ได้ (ไม่เดา) — code:
ดูรายการ code ใน docs/flow.md (ส่วน Resolver)
ไม่ต้องรัน server และไม่ import FastAPI; VEDA_ROOT มาจาก ``.env`` หรือ ``--veda-root``
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
    flow = sub.add_parser("flow", help="flow ต่อโปรเจกต์ (อ่านอย่างเดียว)")
    flow_sub = flow.add_subparsers(dest="flow_command", required=True)
    schema = flow_sub.add_parser("schema", help="export JSON Schema ของ flow.yaml")
    schema.add_argument("--out", type=Path, help="เขียน JSON Schema ไปที่ path นี้ (ไม่ระบุ = stdout)")
    resolve = flow_sub.add_parser("resolve", help="ขั้นถัดไปของโปรเจกต์ (อ่านอย่างเดียว)")
    resolve.add_argument("project")
    resolve.add_argument("--track", choices=["full", "quick"], default="full")
    resolve.add_argument("--json", action="store_true", help="ผลเป็น JSON")
    resolve.add_argument("--veda-root", type=Path, help="แทนค่า VEDA_ROOT ใน .env")
    validate = flow_sub.add_parser("validate", help="ตรวจ flow ตามกฎ (default หรือของโปรเจกต์)")
    validate.add_argument("project", nargs="?")
    validate.add_argument("--file", type=Path, help="ตรวจไฟล์นี้แทน (flow เต็ม หรือ overlay ที่มี extends)")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--veda-root", type=Path)
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
    if args.command == "flow":
        return _flow(args)
    return 2


def _stdout_utf8(text: str) -> None:
    """เขียน UTF-8 เสมอ (console Windows อาจเป็น cp1252 ซึ่งเขียนภาษาไทยไม่ได้)."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _flow(args: argparse.Namespace) -> int:
    if args.flow_command == "schema":
        from bmad_studio.core.flow.schema import json_schema_text

        text = json_schema_text()
        if args.out:
            args.out.write_text(text, encoding="utf-8", newline="\n")
        else:
            _stdout_utf8(text)
        return 0
    if args.flow_command in {"resolve", "validate"}:
        return _flow_read(args)
    return 2


def _veda_paths(override: Path | None):
    from bmad_studio.core.paths import VedaPaths
    from bmad_studio.core.settings import Settings

    try:
        root = override or Settings().veda_root
    except Exception:  # noqa: BLE001 — .env ผิดรูป = ไม่มี VEDA_ROOT ที่เชื่อได้ (exit 2 ไม่ใช่ traceback)
        return None
    if root is None or not (Path(root) / ".veda").is_dir():
        return None
    return VedaPaths(Path(root))


def _fail(as_json: bool, code: str, message: str, issues: list | None = None, source: str | None = None) -> int:
    if as_json:
        payload = {"ok": False, "code": code, "error": message}
        if source is not None:
            payload["source"] = source
        if issues:
            payload["issues"] = issues
        _stdout_utf8(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        lines = [f"ผิดพลาด ({code}): {message}" + (f" [{source}]" if source else "")]
        for item in issues or []:
            where = "/".join(str(item[k]) for k in ("track", "stage") if item.get(k))
            lines.append(f"  - {item['code']}" + (f" {where}" if where else "") + f": {item['error']}")
        sys.stderr.flush()
        buffer = getattr(sys.stderr, "buffer", None)
        text = "\n".join(lines) + "\n"
        if buffer is None:
            sys.stderr.write(text)
        else:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
    return 2


def _flow_read(args: argparse.Namespace) -> int:
    from bmad_studio.core.errors import StudioError
    from bmad_studio.core.flow.merge import FlowInvalid, effective_for
    from bmad_studio.core.flow.resolve import render_text, resolve_project
    from bmad_studio.core.flow.validate import Catalog, validate_flow_bytes

    paths = _veda_paths(args.veda_root)
    if paths is None:
        return _fail(args.json, "veda_root_missing", "ไม่พบ VEDA_ROOT ที่มีโฟลเดอร์ .veda (ตั้งใน .env หรือส่ง --veda-root)")
    if args.flow_command == "validate" and args.file is not None and args.project is not None:
        return _fail(args.json, "invalid_arguments", "ระบุได้อย่างใดอย่างหนึ่ง: <project> หรือ --file")
    try:
        if args.flow_command == "resolve":
            result = resolve_project(paths, args.project, args.track)
            if args.json:
                _stdout_utf8(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2) + "\n")
            else:
                _stdout_utf8(render_text(result))
            return 0
        catalog = Catalog.scan(paths)
        if args.file is not None:
            from bmad_studio.adapters import fs
            from bmad_studio.core.flow.loader import MAX_FLOW_BYTES, FlowYamlError, load_yaml
            from bmad_studio.core.flow.merge import validate_overlay_bytes
            from bmad_studio.core.flow.validate import yaml_issue

            raw = fs.read_bytes(args.file, MAX_FLOW_BYTES)
            if raw is None:
                return _fail(args.json, "read_failed", "ไม่พบไฟล์ที่ระบุ")
            try:
                data = load_yaml(raw)
            except FlowYamlError as exc:
                issues = [yaml_issue(exc)]
            else:
                # overlay = มี key extends ที่ระดับบนสุด (ตัดสินจากโครงสร้าง ไม่ใช่ข้อความ) — ทางเดียวกับ write pipeline
                if isinstance(data, dict) and "extends" in data:
                    issues = validate_overlay_bytes(raw, paths, catalog)
                else:
                    issues = validate_flow_bytes(raw, catalog)[1]
            if issues:
                return _fail(args.json, "flow_invalid", "flow ผิดกฎ", issues, str(args.file))
        else:
            if args.project is not None:
                from bmad_studio.core.flow.status import find_project

                find_project(paths, args.project)
            effective_for(paths, args.project, catalog)
        message = "flow ผ่านการตรวจ"
        _stdout_utf8(json.dumps({"ok": True, "message": message}, ensure_ascii=False) + "\n" if args.json else message + "\n")
        return 0
    except FlowInvalid as exc:
        return _fail(args.json, "flow_invalid", "flow ผิดกฎ — resolver ไม่เดาผล", exc.issues, exc.source)
    except StudioError as exc:
        return _fail(args.json, exc.code, exc.message_th)
    except OSError:
        return _fail(args.json, "read_failed", "อ่านไฟล์ไม่ได้")


if __name__ == "__main__":
    raise SystemExit(main())
