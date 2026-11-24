"""Story 1.3 — deny-by-default access guard, CSRF/Origin, role, mutation audit, security headers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from bmad_studio.api import access_policy
from bmad_studio.api.access_guard import SECURITY_HEADERS
from bmad_studio.api.app import create_app
from bmad_studio.api.envelope import ok
from bmad_studio.core.settings import Settings

from .conftest import PASSWORD, USERNAME

WRITE_PATH = "/api/_test/write"
READ_PATH = "/api/_test/read"


def keep_shell_last(app: FastAPI) -> None:
    """test routes are added after the SPA catch-all; move the catch-all back to the end."""
    app.router.routes.sort(key=lambda route: getattr(route, "path", "") == "/{path:path}")


def _walk(routes: list, prefix: str = ""):
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix, route
        nested = getattr(route, "original_router", None)  # FastAPI's _IncludedRouter wrapper
        if nested is not None:
            sub_prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from _walk(nested.routes, prefix + sub_prefix)


def registered_routes(app: FastAPI) -> list[tuple[str, str]]:
    pairs = []
    for prefix, route in _walk(app.router.routes):
        for method in sorted(route.methods - {"HEAD"}):
            pairs.append((method, prefix + route.path))
    return pairs


def test_every_registered_route_has_a_rule(settings: Settings) -> None:
    app = create_app(settings)
    pairs = registered_routes(app)
    # the walker must see routes inside included routers, otherwise this test proves nothing
    assert {("POST", "/api/login"), ("GET", "/api/session"), ("POST", "/api/logout"),
            ("GET", "/health"), ("GET", "/{path:path}")} <= set(pairs)
    templates = {(rule.method, rule.template) for rule in access_policy.RULES}
    missing = [pair for pair in pairs if pair not in templates]
    assert missing == [], f"routes without an access rule: {missing}"


def test_route_check_detects_a_route_without_rule(settings: Settings) -> None:
    app = create_app(settings)
    app.add_api_route("/api/unlisted", lambda: {"x": 1}, methods=["GET"])
    templates = {(rule.method, rule.template) for rule in access_policy.RULES}
    assert ("GET", "/api/unlisted") in [p for p in registered_routes(app) if p not in templates]


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "target.txt"
    path.write_text("original", encoding="utf-8")
    return path


@pytest.fixture
def app(settings: Settings, target: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = create_app(settings)
    calls: list[str] = []
    app.state.test_calls = calls

    def write() -> object:
        calls.append("write")
        target.write_text("changed", encoding="utf-8")
        return ok({"written": True})

    def read() -> object:
        calls.append("read")
        return ok({"value": target.read_text(encoding="utf-8")})

    app.add_api_route(WRITE_PATH, write, methods=["POST"])
    app.add_api_route(READ_PATH, read, methods=["GET"])
    app.add_api_route("/api/_test/unlisted", write, methods=["POST"])
    keep_shell_last(app)
    monkeypatch.setattr(access_policy, "RULES", [
        *access_policy.RULES,
        access_policy.Rule("POST", WRITE_PATH, access_policy.STUDIO_WRITE),
        access_policy.Rule("GET", READ_PATH, access_policy.STUDIO_READ),
    ])
    return app


def _login(client: TestClient) -> str:
    response = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["data"]["csrf"]


def _security(settings: Settings) -> list[dict]:
    path = settings.runtime_dir / "security.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _audit(settings: Settings) -> list[dict]:
    path = settings.runtime_dir / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_unlisted_route_is_403_even_for_admin(app: FastAPI, target: Path) -> None:
    client = TestClient(app)
    csrf = _login(client)
    response = client.post("/api/_test/unlisted", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403 and response.json()["code"] == "not_allowed"
    assert target.read_text(encoding="utf-8") == "original"
    assert client.get("/api/does-not-exist").json()["code"] == "not_allowed"


def test_no_session_is_401(app: FastAPI, settings: Settings) -> None:
    client = TestClient(app)
    for method, path in (("GET", READ_PATH), ("POST", WRITE_PATH), ("POST", "/api/logout")):
        response = client.request(method, path)
        assert response.status_code == 401, path
        assert response.json() == {"ok": False, "code": "unauthenticated", "error": "กรุณาเข้าสู่ระบบก่อน"}
    assert app.state.test_calls == []
    assert {line["status"] for line in _security(settings)} == {401}


def test_stale_session_after_password_change_is_401(app: FastAPI, credentials_file: Path) -> None:
    client = TestClient(app)
    _login(client)
    credentials_file.write_text(f"VEDA_DASHBOARD_USERNAME={USERNAME}\nVEDA_DASHBOARD_PASSWORD=changed-now\n",
                                encoding="utf-8")
    response = client.get(READ_PATH)
    assert response.status_code == 401 and response.json()["code"] == "session_expired"


def test_viewer_is_forbidden_for_read_and_write(settings: Settings, target: Path, monkeypatch) -> None:
    viewer_settings = settings.model_copy(update={"admin_allowlist": "someone-else"})
    app = create_app(viewer_settings)
    app.add_api_route(WRITE_PATH, lambda: target.write_text("changed", encoding="utf-8"), methods=["POST"])
    app.add_api_route(READ_PATH, lambda: {"v": 1}, methods=["GET"])
    keep_shell_last(app)
    monkeypatch.setattr(access_policy, "RULES", [
        *access_policy.RULES,
        access_policy.Rule("POST", WRITE_PATH, access_policy.STUDIO_WRITE),
        access_policy.Rule("GET", READ_PATH, access_policy.STUDIO_READ),
    ])
    client = TestClient(app)
    csrf = _login(client)
    assert client.get(READ_PATH).json()["code"] == "forbidden"
    response = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert target.read_text(encoding="utf-8") == "original"
    # viewer may still sign out
    assert client.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200


@pytest.mark.parametrize("headers, code", [
    ({}, "csrf_invalid"),
    ({"X-CSRF-Token": "wrong"}, "csrf_invalid"),
    ({"X-CSRF-Token": "<csrf>", "Origin": "https://evil.example"}, "origin_mismatch"),
    ({"X-CSRF-Token": "<csrf>", "Origin": "null"}, "origin_mismatch"),
])
def test_mutation_without_valid_csrf_or_origin_is_403_and_handler_not_called(
        app: FastAPI, target: Path, settings: Settings, headers: dict, code: str) -> None:
    client = TestClient(app)
    csrf = _login(client)
    headers = {k: (csrf if v == "<csrf>" else v) for k, v in headers.items()}
    response = client.post(WRITE_PATH, headers=headers)
    assert response.status_code == 403 and response.json()["code"] == code
    assert app.state.test_calls == []
    assert target.read_text(encoding="utf-8") == "original"
    assert any(line["code"] == code and line["status"] == 403 for line in _security(settings))


def test_same_origin_and_forwarded_host_from_trusted_proxy(settings: Settings, target: Path, monkeypatch) -> None:
    trusted = settings.model_copy(update={"trusted_proxies": "testclient"})
    app = create_app(trusted)
    app.add_api_route(WRITE_PATH, lambda: ok({"w": target.write_text("changed", encoding="utf-8")}), methods=["POST"])
    monkeypatch.setattr(access_policy, "RULES", [
        *access_policy.RULES, access_policy.Rule("POST", WRITE_PATH, access_policy.STUDIO_WRITE)])
    client = TestClient(app)
    csrf = _login(client)
    same = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"})
    assert same.status_code == 200
    proxied = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf, "Origin": "https://studio.example",
                                               "X-Forwarded-Host": "studio.example"})
    assert proxied.status_code == 200


def test_forwarded_host_ignored_from_untrusted_peer(app: FastAPI) -> None:
    client = TestClient(app)
    csrf = _login(client)
    response = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example",
                                                "X-Forwarded-Host": "evil.example"})
    assert response.json()["code"] == "origin_mismatch"


def test_valid_mutation_is_audited_requested_then_result(app: FastAPI, target: Path, settings: Settings) -> None:
    client = TestClient(app)
    csrf = _login(client)
    response = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"})
    assert response.status_code == 200 and response.json() == {"ok": True, "data": {"written": True}}
    assert target.read_text(encoding="utf-8") == "changed"
    phases = [line for line in _audit(settings) if line.get("event") == "mutation"]
    assert [line["phase"] for line in phases] == ["requested", "result"]
    assert phases[0]["id"] == phases[1]["id"]
    assert phases[1]["status"] == 200
    assert phases[0]["actor"] == USERNAME and phases[0]["role"] == "admin" and phases[0]["path"] == WRITE_PATH
    assert csrf not in json.dumps(phases)


def test_audit_unwritable_blocks_mutation_with_503(app: FastAPI, target: Path, settings: Settings) -> None:
    client = TestClient(app)
    csrf = _login(client)
    audit_path = settings.runtime_dir / "audit.jsonl"
    audit_path.unlink()
    audit_path.mkdir()  # now appending to it fails
    response = client.post(WRITE_PATH, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
    assert response.json() == {"ok": False, "code": "audit_unavailable", "error": "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง"}
    assert app.state.test_calls == []
    assert target.read_text(encoding="utf-8") == "original"


def test_reads_are_not_mutation_audited(app: FastAPI, settings: Settings) -> None:
    client = TestClient(app)
    _login(client)
    assert client.get(READ_PATH).status_code == 200
    assert not [line for line in _audit(settings) if line.get("event") == "mutation"]


def test_rate_limited_login_is_in_security_log(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    for _ in range(6):
        client.post("/api/login", json={"username": USERNAME, "password": "bad"})
    statuses = [line["status"] for line in _security(settings)]
    assert statuses.count(401) == 5 and statuses.count(429) == 1


def test_security_headers_on_every_response(app: FastAPI) -> None:
    client = TestClient(app)
    for response in (client.get("/health"), client.get(READ_PATH), client.get("/api/nope"),
                     client.post("/api/login", json={"username": "x", "password": "y"})):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers.get(name) == value


def test_envelope_on_denials(app: FastAPI) -> None:
    client = TestClient(app)
    for response in (client.get(READ_PATH), client.get("/api/nope")):
        body = response.json()
        assert set(body) == {"ok", "code", "error"} and body["ok"] is False


def test_head_follows_get_rule(app: FastAPI) -> None:
    client = TestClient(app)
    # the guard treats HEAD as GET (public /health passes the guard; FastAPI itself may answer 405)
    assert client.head("/health").status_code != 403
    assert client.head(READ_PATH).status_code == 401


def test_viewer_role_permissions_are_minimal() -> None:
    assert access_policy.ROLE_PERMISSIONS["viewer"] == {access_policy.SESSION_SELF}
    assert not access_policy.allowed("nobody", access_policy.SESSION_SELF)
