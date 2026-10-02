"""Serving the built console under /app without exposing anything outside the build."""

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from conftest import FakeQueue


@pytest.fixture
def build(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>console</title>")
    (root / "assets" / "app.js").write_text("console.log('synthetic')")
    (tmp_path / "secret.txt").write_text("outside the build")
    return root


async def test_console_routes_assets_and_traversal(tenants, tmp_path, build):
    app = create_app(Settings(data_dir=tmp_path, web_dir=build), queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        for path in ("/app", "/app/", "/app/platform/orgs", "/app/setup-password"):
            response = await api.get(path)
            assert response.status_code == 200 and "<title>console</title>" in response.text
            assert "default-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["referrer-policy"] == "no-referrer"
        asset = await api.get("/app/assets/app.js")
        assert asset.text == "console.log('synthetic')"
        for path in (
            "/app/..%2Fsecret.txt",
            "/app/%2E%2E/secret.txt",
            "/app/assets/../../secret.txt",
        ):
            assert "outside the build" not in (await api.get(path)).text
        assert (await api.get("/health")).json()["ok"] is True


def test_console_requires_a_build(tenants, tmp_path):
    with pytest.raises(RuntimeError):
        create_app(Settings(data_dir=tmp_path, web_dir=tmp_path / "missing"), queue=FakeQueue())
