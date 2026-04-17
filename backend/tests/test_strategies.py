from fastapi.testclient import TestClient


def test_create_strategy_and_reject_duplicate_code(client: TestClient) -> None:
    payload = {
        "code": "trend_following",
        "name": "Trend Following",
        "description": "Momentum setup for trend continuation.",
        "horizon": "days_weeks",
        "bias": "long",
        "status": "paper",
        "initial_version": {
            "hypothesis": "Strong trends tend to persist after orderly pullbacks.",
            "general_rules": {"price_above_sma50": True},
            "parameters": {"max_risk_per_trade_r": 1.0},
            "state": "approved",
            "is_baseline": True,
        },
    }

    created = client.post("/api/v1/strategies", json=payload)
    duplicate = client.post("/api/v1/strategies", json=payload)
    listed = client.get("/api/v1/strategies")

    assert created.status_code == 201
    assert created.json()["code"] == payload["code"]
    assert created.json()["current_version_id"] is not None

    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Strategy with code 'trend_following' already exists"}

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["versions"][0]["version"] == 1
