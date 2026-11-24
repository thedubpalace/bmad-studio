"""Story 1.4 — FastAPI เสิร์ฟ SPA shell, deep link, static assets, OpenAPI → TS types."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmad_studio.api.app import create_app
from bmad_studio.cli.openapi_ts import render_ts
from bmad_studio.core.settings import REPO_ROOT, Settings

INDEX = '<!doctype html><html lang="th"><body><div id="root"></div><script type="module" src="/assets/app.js"></script></body></html>'


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('x')", encoding="utf-8")
    return root


@pytest.fixture
def client(settings: Settings, dist: Path) -> TestClient:
    return TestClient(create_app(settings.model_copy(update={"web_dist": dist})))


@pytest.mark.parametrize("path", ["/", "/login", "/p/bmad-studio", "/p/x/flow", "/agents/new", "/bmad/skills/bmad-build", "/changes"])
def test_shell_served_for_root_and_deep_links_without_session(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_static_assets_are_public(client: TestClient) -> None:
    response = client.get("/assets/app.js")
    assert response.status_code == 200 and "console.log" in response.text


@pytest.mark.parametrize("path", ["/assets/%2e%2e/%2e%2e/pyproject.toml", "/assets/..%2f..%2fpyproject.toml",
                                  "/assets/..%5c..%5cpyproject.toml"])
def test_asset_traversal_never_leaves_dist_assets(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert "[project]" not in response.text
    assert response.status_code in {200, 403, 404}  # 200 only when it fell back to the shell
    if response.status_code == 200:
        assert response.text == INDEX


def test_api_paths_never_get_the_shell(client: TestClient) -> None:
    for path in ("/api/unknown", "/api/agents/x"):
        response = client.get(path)
        assert response.status_code == 403 and response.json()["code"] == "not_allowed"


def test_health_still_answers_json(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}


def test_shell_without_build_explains_in_thai(settings: Settings, tmp_path: Path) -> None:
    app_client = TestClient(create_app(settings.model_copy(update={"web_dist": tmp_path / "nothing"})))
    response = app_client.get("/")
    assert response.status_code == 503 and "build" in response.text and 'lang="th"' in response.text


def test_session_exposes_review_url(settings: Settings, client: TestClient) -> None:
    from .conftest import PASSWORD, USERNAME

    data = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD}).json()["data"]
    assert data["review_url"] == settings.review_url
    assert "review_url" not in client.post("/api/logout", headers={"X-CSRF-Token": data["csrf"]}).json()["data"]


def test_generated_ts_types_are_current() -> None:
    schema = create_app(Settings(_env_file=None), check=False).openapi()
    generated = render_ts(schema)
    committed = (REPO_ROOT / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == generated, "run: npm --prefix web run gen:api"
    assert "export interface SessionData" in generated and "review_url?: string | null;" in generated


def test_render_ts_handles_common_shapes() -> None:
    ts = render_ts({"components": {"schemas": {
        "Thing": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string"}, "n": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}},
            "kind": {"enum": ["a", "b"]}, "ref": {"$ref": "#/components/schemas/Other"},
            "maybe": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
            "map": {"type": "object", "additionalProperties": {"type": "number"}},
        }},
        "Other": {"type": "string"},
    }}})
    assert "id: string;" in ts and "n?: number;" in ts and "tags?: Array<string>;" in ts
    assert 'kind?: "a" | "b";' in ts and "ref?: Other;" in ts and "maybe?: boolean | null;" in ts
    assert "map?: Record<string, number>;" in ts and "export type Other = string;" in ts
