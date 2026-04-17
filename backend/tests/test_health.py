from fastapi.testclient import TestClient

from app.main import app


def test_healthcheck() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_frontend() -> None:
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Strategy Lab Console" in response.text


def test_frontend_asset_serves_javascript() -> None:
    client = TestClient(app)
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "renderPipelineDetail" in response.text
