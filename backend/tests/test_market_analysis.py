def test_market_history_returns_ohlcv(client) -> None:
    response = client.get("/api/v1/market-data/NVDA/history")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 120
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= payload[0].keys()


def test_fused_analysis_returns_quant_and_visual_layers(client) -> None:
    response = client.get("/api/v1/market-data/NVDA/analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] in {"paper_enter", "watch", "discard"}
    assert "quant_summary" in payload
    assert "visual_summary" in payload
    assert payload["quant_summary"]["risk_reward"] > 0
    assert payload["visual_summary"]["setup_type"] in {"breakout", "pullback", "consolidation", "range"}


def test_standard_chart_endpoint_returns_svg(client) -> None:
    response = client.get("/api/v1/market-data/NVDA/chart")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text
