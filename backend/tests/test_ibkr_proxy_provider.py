import io
from app.providers.market_data.ibkr_proxy_provider import IBKRProxyError, IBKRProxyProvider
from urllib.error import HTTPError


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


def test_ibkr_proxy_provider_aggregates_intraday_when_daily_history_is_empty(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")
    calls: list[tuple[str, dict[str, str] | None]] = []

    def fake_request(path: str, params: dict[str, str] | None = None):
        calls.append((path, params))
        if path == "/contracts/search":
            return [{"conid": "873538145", "symbol": "AVEX", "secType": "STK", "companyHeader": "AVEX INC (NASDAQ)"}]
        if path == "/marketdata/history" and params == {
            "conid": "873538145",
            "period": "6m",
            "bar": "1d",
            "outsideRth": "false",
        }:
            return {"data": []}
        if path == "/marketdata/history" and params == {
            "conid": "873538145",
            "period": "1m",
            "bar": "1h",
            "outsideRth": "false",
        }:
            return {
                "data": [
                    {"o": 24.06, "c": 24.4, "h": 24.8, "l": 24.02, "v": 5100, "t": 1776441600000},
                    {"o": 24.4, "c": 24.7, "h": 24.92, "l": 24.18, "v": 5000, "t": 1776445200000},
                    {"o": 24.7, "c": 26.31, "h": 26.55, "l": 24.52, "v": 6605, "t": 1776448800000},
                    {"o": 26.31, "c": 27.01, "h": 27.96, "l": 26.2, "v": 12042, "t": 1776452400000},
                ]
            }
        raise AssertionError(f"Unexpected request: {path} {params}")

    monkeypatch.setattr(provider, "_request_json", fake_request)

    candles = provider.get_history("AVEX", limit=120)

    assert len(candles) == 1
    assert candles[0].timestamp == "2026-04-17"
    assert candles[0].open == 24.06
    assert candles[0].high == 27.96
    assert candles[0].low == 24.02
    assert candles[0].close == 27.01
    assert candles[0].volume == 28747
    assert calls[1] == (
        "/marketdata/history",
        {"conid": "873538145", "period": "6m", "bar": "1d", "outsideRth": "false"},
    )
    assert calls[2] == (
        "/marketdata/history",
        {"conid": "873538145", "period": "1m", "bar": "1h", "outsideRth": "false"},
    )


def test_ibkr_proxy_provider_builds_snapshot_from_intraday_when_daily_history_is_empty(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        if path == "/contracts/search":
            return [{"conid": "873538145", "symbol": "AVEX", "secType": "STK", "companyHeader": "AVEX INC (NASDAQ)"}]
        if path == "/marketdata/history" and params == {
            "conid": "873538145",
            "period": "1y",
            "bar": "1d",
            "outsideRth": "false",
        }:
            return {"data": []}
        if path == "/marketdata/history" and params == {
            "conid": "873538145",
            "period": "1m",
            "bar": "1h",
            "outsideRth": "false",
        }:
            return {
                "data": [
                    {"o": 24.06, "c": 24.4, "h": 24.8, "l": 24.02, "v": 5100, "t": 1776441600000},
                    {"o": 24.4, "c": 24.7, "h": 24.92, "l": 24.18, "v": 5000, "t": 1776445200000},
                    {"o": 24.7, "c": 26.31, "h": 26.55, "l": 24.52, "v": 6605, "t": 1776448800000},
                    {"o": 26.31, "c": 27.01, "h": 27.96, "l": 26.2, "v": 12042, "t": 1776452400000},
                ]
            }
        if path == "/marketdata/snapshot":
            return [{"31": "33.14"}]
        raise AssertionError(f"Unexpected request: {path} {params}")

    monkeypatch.setattr(provider, "_request_json", fake_request)

    snapshot = provider.get_snapshot("AVEX")

    assert snapshot.ticker == "AVEX"
    assert snapshot.price == 33.14
    assert snapshot.sma_20 == 27.01
    assert snapshot.sma_50 == 27.01
    assert snapshot.sma_200 == 27.01
    assert snapshot.rsi_14 == 50.0
    assert snapshot.relative_volume == 1.0
    assert snapshot.atr_14 > 0
    assert snapshot.week_performance == 0.0
    assert snapshot.month_performance == 0.0


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


def test_ibkr_proxy_provider_normalizes_options_sentiment_payload(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        assert path == "/options-sentiment/AAPL"
        assert params == {"secType": "STK"}
        return {
            "symbol": "AAPL",
            "conid": 265598,
            "secType": "STK",
            "companyHeader": "APPLE INC (NASDAQ)",
            "metrics": {
                "lastPrice": 270.75,
                "optionImpliedVolPct": 27.34,
                "putCallRatio": None,
                "putCallVolumeRatio": None,
                "optionVolume": None,
            },
            "availability": {
                "marketDataAvailability": "ZB",
                "requestedFields": ["31", "7283", "7285", "7086", "7089"],
                "returnedFields": ["31", "7283"],
                "putCallRatioAvailable": False,
                "putCallVolumeRatioAvailable": False,
                "optionVolumeAvailable": False,
            },
            "fallback": {
                "reason": "Use the ranking endpoint for operable put/call ratio data.",
                "topByVolumePath": "/options-sentiment/top?basis=volume&direction=high",
            },
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)

    payload = provider.get_options_sentiment("AAPL")

    assert payload["available"] is True
    assert payload["symbol"] == "AAPL"
    assert payload["sec_type"] == "STK"
    assert payload["last_price"] == 270.75
    assert payload["option_implied_vol_pct"] == 27.34
    assert payload["put_call_ratio"] is None
    assert payload["put_call_ratio_available"] is False
    assert payload["returned_fields"] == ["31", "7283"]
    assert payload["fallback_top_by_volume_path"] == "/options-sentiment/top?basis=volume&direction=high"


def test_ibkr_proxy_provider_normalizes_options_sentiment_rankings(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        assert path == "/options-sentiment/top"
        assert params == {
            "basis": "volume",
            "direction": "high",
            "instrument": "STK",
            "location": "STK.US.MAJOR",
            "limit": "3",
        }
        return {
            "basis": "volume",
            "direction": "high",
            "scannerType": "HIGH_OPT_VOLUME_PUT_CALL_RATIO",
            "instrument": "STK",
            "location": "STK.US.MAJOR",
            "scanDataColumnName": "Opt Vol P/C",
            "contracts": [
                {
                    "rank": 1,
                    "symbol": "BKLN",
                    "conid": 319359400,
                    "companyName": "INVESCO SENIOR LOAN ETF",
                    "listingExchange": "ARCA",
                    "secType": "STK",
                    "ratio": 48668.75,
                    "rawRatio": "48668.7500",
                },
                {
                    "rank": 2,
                    "symbol": "SABS",
                    "conid": 675765102,
                    "companyName": "SAB BIOTHERAPEUTICS INC",
                    "listingExchange": "NASDAQ.SCM",
                    "secType": "STK",
                    "ratio": 901.0,
                    "rawRatio": "901.0000",
                },
            ],
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)

    payload = provider.get_options_sentiment_rankings(limit=3)

    assert payload["available"] is True
    assert payload["basis"] == "volume"
    assert payload["direction"] == "high"
    assert payload["scanner_type"] == "HIGH_OPT_VOLUME_PUT_CALL_RATIO"
    assert [contract["symbol"] for contract in payload["contracts"]] == ["BKLN", "SABS"]
    assert payload["contracts"][0]["ratio"] == 48668.75


def test_ibkr_proxy_provider_formats_http_error_payload_error_field() -> None:
    exc = HTTPError(
        url="https://example.test/contracts/search?symbol=SPY",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"Bad Request: no bridge","statusCode":400}'),
    )

    message = IBKRProxyProvider._format_http_error(exc, "/contracts/search")

    assert "no bridge" in message
    assert "HTTP 400" in message


def test_ibkr_proxy_provider_normalizes_market_overview_top_of_book_fields(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        assert path == "/market-overview/SPY"
        assert params == {"secType": "STK"}
        return {
            "symbol": "SPY",
            "providerSource": "ibkr_proxy_market_overview",
            "marketSignals": {
                "symbol": "SPY",
                "conid": 237937002,
                "secType": "STK",
                "companyHeader": "SPDR S&P 500 ETF TRUST",
                "topOfBook": {
                    "lastPrice": 710.14,
                    "bidPrice": 710.1,
                    "askPrice": 710.18,
                    "lastSize": 25,
                },
                "availability": {
                    "marketDataAvailability": "Z",
                    "snapshotUpdatedAt": 1776668907944,
                },
            },
            "optionsSentiment": {},
            "corporateEvents": [],
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)

    payload = provider.get_market_overview("SPY")
    market_signals = payload["market_signals"]

    assert market_signals["last_price"] == 710.14
    assert market_signals["bid"] == 710.1
    assert market_signals["ask"] == 710.18
    assert market_signals["volume"] == 25
    assert market_signals["market_data_availability"] == "Z"
    assert market_signals["snapshot_updated_at"] == 1776668907944


def test_ibkr_proxy_provider_normalizes_market_overview_payload(monkeypatch) -> None:
    provider = IBKRProxyProvider(base_url="https://example.test")

    def fake_request(path: str, params: dict[str, str] | None = None):
        assert path == "/market-overview/AAPL"
        assert params == {"secType": "STK"}
        return {
            "symbol": "AAPL",
            "secType": "STK",
            "marketSignals": {
                "symbol": "AAPL",
                "conid": 265598,
                "secType": "STK",
                "lastPrice": 212.54,
                "bid": 212.5,
                "ask": 212.57,
                "changePercent": "1.23%",
                "marketDataAvailability": "ZB",
            },
            "optionsSentiment": {
                "symbol": "AAPL",
                "secType": "STK",
                "metrics": {
                    "lastPrice": 212.54,
                    "optionImpliedVolPct": 26.7,
                    "putCallRatio": 1.18,
                },
                "availability": {
                    "requestedFields": ["31", "7283", "7285"],
                    "returnedFields": ["31", "7283", "7285"],
                    "putCallRatioAvailable": True,
                },
            },
            "corporateEvents": {
                "next": {
                    "label": "Erng Call",
                    "dateTime": "04/30 Aftr Mkt",
                }
            },
        }

    monkeypatch.setattr(provider, "_request_json", fake_request)

    payload = provider.get_market_overview("AAPL")

    assert payload["available"] is True
    assert payload["provider_source"] == "ibkr_proxy_market_overview"
    assert payload["market_signals"]["last_price"] == 212.54
    assert payload["market_signals"]["change_percent"] == 1.23
    assert payload["options_sentiment"]["put_call_ratio"] == 1.18
    assert payload["options_sentiment"]["option_implied_vol_pct"] == 26.7
    assert payload["corporate_events"][0]["event_type"] == "earnings"
    assert payload["corporate_events"][0]["title"] == "Erng Call"
    assert payload["corporate_events"][0]["event_date"].endswith("-04-30")
