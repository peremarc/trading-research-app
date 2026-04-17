from fastapi.testclient import TestClient


def test_seed_bootstrap_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/bootstrap/seed")
    second = client.post("/api/v1/bootstrap/seed")

    assert first.status_code == 201
    assert first.json() == {
        "strategies": 3,
        "screeners": 2,
        "watchlists": 2,
        "watchlist_items": 6,
    }
    assert second.status_code == 201
    assert second.json() == {
        "strategies": 0,
        "screeners": 0,
        "watchlists": 0,
        "watchlist_items": 0,
    }
