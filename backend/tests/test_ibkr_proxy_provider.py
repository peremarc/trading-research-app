from app.providers.market_data.ibkr_proxy_provider import IBKRProxyError, IBKRProxyProvider


def _build_history_payload(count: int = 260, *, base_price: float = 100.0) -> dict:
    data = []
    base_timestamp = 1_700_000_000_000
    for idx in range(count):
        close = round(base_price + idx * 0.8, 2)
        open_price = round(close - 0.4, 2)
        high = round(close + 1.2, 2)
        low = round(close - 1.5, 2)
        volume = 1000 + idx * 15
        data.append(
            {
                "o": open_price,
                "c": close,
                "h": high,
                "l": low,
                "v": volume,
                "t": base_timestamp + idx * 86_400_000,
            }
        )
    return {"volumeFactor": 100, "data": data}


def test_ibkr_proxy_provider_prefers_us_stock_contract_and_scales_history_volume(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")
    calls: list[tuple[str, dict[str, str] | None]] = []

    def fake_request(path: str, params: dict[str, str] | None = None):
        calls.append((path, params))
        if path == "/contracts/search":
            return [
                {"conid": "273982664", "symbol": "AAPL", "secType": "STK", "companyHeader": "APPLE INC (EBS)"},
                {"conid": "265598", "symbol": "AAPL", "secType": "STK", "companyHeader": "APPLE INC (NASDAQ)"},
                {"conid": "-1", "symbol": None, "secType": "BOND", "companyHeader": "Apple Inc"},
            ]
        if path == "/marketdata/history":
            return _build_history_payload(count=40)
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(provider, "_request_json", fake_request)

    candles = provider.get_history("AAPL", limit=22)

    assert len(candles) == 22
    assert calls[0] == (
        "/contracts/search",
        {"symbol": "AAPL", "name": "true", "secType": "STK"},
    )
    assert calls[1][0] == "/marketdata/history"
    assert calls[1][1]["conid"] == "265598"
    assert calls[1][1]["period"] == "1m"
    assert candles[-1].volume == (1000 + 39 * 15) * 100


def test_ibkr_proxy_provider_builds_snapshot_with_live_price(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        if path == "/contracts/search":
            return [{"conid": "265598", "symbol": "AAPL", "secType": "STK", "companyHeader": "APPLE INC (NASDAQ)"}]
        if path == "/marketdata/history":
            return _build_history_payload(count=260, base_price=80.0)
        if path == "/marketdata/snapshot":
            return [{"31": "333.33"}]
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(provider, "_request_json", fake_request)

    snapshot = provider.get_snapshot("AAPL")

    assert snapshot.ticker == "AAPL"
    assert snapshot.price == 333.33
    assert snapshot.sma_20 > 0
    assert snapshot.sma_50 > 0
    assert snapshot.sma_200 > 0
    assert snapshot.rsi_14 > 0
    assert snapshot.atr_14 > 0
    assert snapshot.week_performance > 0
    assert snapshot.month_performance > 0


def test_ibkr_proxy_provider_raises_when_contract_cannot_be_resolved(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        if path == "/contracts/search":
            return [{"conid": "-1", "symbol": None, "secType": "BOND", "companyHeader": "Apple Inc"}]
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(provider, "_request_json", fake_request)

    try:
        provider.get_history("AAPL", limit=22)
    except IBKRProxyError as exc:
        assert "could not resolve" in str(exc)
    else:
        raise AssertionError("Expected IBKRProxyError when no valid contract is returned")


def test_ibkr_proxy_provider_builds_scanner_universe_and_filters_non_standard_symbols(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")
    calls: list[tuple[str, dict]] = []

    def fake_request_post(path: str, payload: dict):
        calls.append((path, payload))
        if payload["type"] == "MOST_ACTIVE":
            return {
                "contracts": [
                    {"symbol": "LAKE", "sec_type": "STK"},
                    {"symbol": "JACS RT", "sec_type": "STK"},
                    {"symbol": "RPAY", "sec_type": "STK"},
                ]
            }
        if payload["type"] == "TOP_PERC_GAIN":
            return {
                "contracts": [
                    {"symbol": "RPAY", "sec_type": "STK"},
                    {"symbol": "BRK.B", "sec_type": "STK"},
                    {"symbol": "ES", "sec_type": "FUT"},
                ]
            }
        raise AssertionError(f"Unexpected scanner payload: {payload}")

    monkeypatch.setattr(provider, "_request_json_post", fake_request_post)

    universe = provider.get_scanner_universe(
        ["MOST_ACTIVE", "TOP_PERC_GAIN"],
        instrument="STK",
        location="STK.US.MAJOR",
        filters=[{"code": "priceAbove", "value": 5}],
        limit=5,
    )

    assert universe == ["LAKE", "RPAY", "BRK.B"]
    assert calls == [
        (
            "/scanner/run",
            {
                "instrument": "STK",
                "location": "STK.US.MAJOR",
                "type": "MOST_ACTIVE",
                "filter": [{"code": "priceAbove", "value": 5}],
            },
        ),
        (
            "/scanner/run",
            {
                "instrument": "STK",
                "location": "STK.US.MAJOR",
                "type": "TOP_PERC_GAIN",
                "filter": [{"code": "priceAbove", "value": 5}],
            },
        ),
    ]
