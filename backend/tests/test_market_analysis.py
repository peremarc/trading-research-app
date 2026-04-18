from app.domains.market.services import MarketDataService
from app.providers.market_data.twelve_data_provider import TwelveDataError


class _RateLimitedProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_snapshot(self, ticker: str):
        self.calls += 1
        raise TwelveDataError(
            "Twelve Data returned no values for SPY: You have run out of API credits for the current minute. "
            "Wait for the next minute"
        )

    def get_history(self, ticker: str, limit: int = 120):
        self.calls += 1
        raise TwelveDataError(
            "Twelve Data returned no values for SPY: You have run out of API credits for the current minute. "
            "Wait for the next minute"
        )


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


def test_chart_endpoint_accepts_extended_timeframes(client) -> None:
    response = client.get("/api/v1/market-data/NVDA/chart?timeframe=1y")

    assert response.status_code == 200
    assert "1Y Chart" in response.text


def test_chart_pack_returns_requested_timeframes(client) -> None:
    response = client.get("/api/v1/market-data/NVDA/chart-pack?timeframes=1m,3m,6m,1y,5y")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "NVDA"
    assert [item["timeframe"] for item in payload["charts"]] == ["1M", "3M", "6M", "1Y", "5Y"]
    assert all("<svg" in item["chart_svg"] for item in payload["charts"])


def test_market_data_service_degrades_to_fallback_during_twelve_data_rate_limit() -> None:
    service = MarketDataService(raise_on_provider_error=True)
    service.provider_name = "twelve_data"
    service.provider = _RateLimitedProvider()

    first = service.get_snapshot("SPY")
    second = service.get_snapshot("QQQ")

    assert first.ticker == "SPY"
    assert second.ticker == "QQQ"
    assert service.provider.calls == 1
