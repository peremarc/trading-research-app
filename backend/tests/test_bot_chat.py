from fastapi.testclient import TestClient


def test_bot_chat_summarizes_latest_operations(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "chat_ops_strategy",
            "name": "Chat Ops Strategy",
            "description": "Strategy for chat operations summary.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Chat summary hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()

    signal = client.post(
        "/api/v1/signals",
        json={
            "ticker": "NVDA",
            "strategy_id": strategy["id"],
            "strategy_version_id": strategy["current_version_id"],
            "signal_type": "breakout",
            "thesis": "Breakout signal",
            "entry_zone": {"price": 100},
            "stop_zone": {"price": 95},
            "target_zone": {"price": 110},
            "signal_context": {"source": "chat-test"},
            "quality_score": 0.81,
        },
    ).json()

    position = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "signal_id": signal["id"],
            "strategy_version_id": strategy["current_version_id"],
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "size": 1,
            "thesis": "Breakout signal",
            "entry_context": {"source": "chat-test"},
        },
    ).json()

    client.post(
        f"/api/v1/positions/{position['id']}/close",
        json={
            "exit_price": 108,
            "exit_reason": "target_hit",
            "max_drawdown_pct": -1.2,
            "max_runup_pct": 8.0,
        },
    )

    response = client.post("/api/v1/chat", json={"message": "resumen de las ultimas operaciones"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["topic"] == "operations"
    assert "NVDA" in payload["reply"]
    assert payload["context"]["closed_positions"] == 1
    assert payload["context"]["wins"] == 1


def test_bot_chat_surfaces_missing_tools_from_current_stack(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"message": "que herramientas te faltan para mejorar resultados"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["topic"] == "tools"
    assert "paper trading" in payload["reply"]
    assert payload["context"]["ai_enabled"] is False
    assert payload["context"]["using_stub_market_data"] is True
    assert payload["context"]["has_twelve_data_key"] is False


def test_bot_chat_exposes_latest_market_state_snapshot(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    planned = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-18", "market_context": {}})
    assert planned.status_code == 201
    snapshot = planned.json()["market_state_snapshot"]

    status_response = client.post("/api/v1/chat", json={"message": "que estas haciendo ahora"})
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["topic"] == "status"
    assert status_payload["context"]["market_state"]["snapshot_id"] == snapshot["id"]
    assert status_payload["context"]["market_state"]["regime_label"] == snapshot["regime_label"]
    assert "market state" in status_payload["reply"].lower()

    macro_response = client.post("/api/v1/chat", json={"message": "cual es el contexto macro actual"})
    assert macro_response.status_code == 200
    macro_payload = macro_response.json()
    assert macro_payload["topic"] == "macro"
    assert macro_payload["context"]["market_state"]["snapshot_id"] == snapshot["id"]
    assert "market state snapshot" in macro_payload["reply"].lower()
