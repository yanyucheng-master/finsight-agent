"""HTTP 层的安全边界：静态文件托管不能跳出 web/ 目录（.env 里有 API Key）。"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from finsight.api.app import WEB_DIR, create_app
from finsight.config import load_settings
from finsight.data.service import load_service

SNAP = load_settings().snapshot_dir
pytestmark = pytest.mark.skipif(not (SNAP / "manifest.jsonl").exists() or not (WEB_DIR / "index.html").exists(),
                                reason="没有数据快照或前端构建产物")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    settings = dataclasses.replace(load_settings(), data_dir=tmp_path_factory.mktemp("data"))
    return TestClient(create_app(settings=settings, service=load_service(SNAP)))


@pytest.mark.parametrize("path", [
    "/..%2fconfig.py",                      # backend/finsight/config.py
    "/%2e%2e/%2e%2e/pyproject.toml",         # backend/pyproject.toml
    "/assets/..%2f..%2f..%2fpyproject.toml",
    "/..%2f..%2f..%2f.env.example",         # 项目根目录（.env 与它同级）
])
def test_static_route_does_not_escape_web_dir(client, path):
    r = client.get(path)
    # 跳出目录的请求一律当作前端路由，返回首页；绝不能返回目录外文件的内容
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert "LLM_PROVIDERS" not in r.text and "[project]" not in r.text and "API_KEY" not in r.text


def test_static_assets_and_spa_fallback_still_work(client):
    index = client.get("/")
    assert index.status_code == 200 and "<!doctype html>" in index.text.lower()
    asset = next((WEB_DIR / "assets").glob("*.css"))
    r = client.get(f"/assets/{asset.name}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    assert client.get("/c/600519").status_code == 200  # 前端路由回落到首页


def test_unknown_api_path_is_404_not_index(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert "<!doctype html>" not in r.text.lower()
