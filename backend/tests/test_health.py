from pathlib import Path


FRONTEND_DIR = Path(__file__).resolve().parents[1] / "app" / "frontend"


def test_healthcheck(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_frontend() -> None:
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "Strategy Lab Console" in index_html


def test_frontend_asset_serves_javascript() -> None:
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "renderPipelineDetail" in app_js
