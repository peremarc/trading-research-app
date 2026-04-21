from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

from app.core.config import Settings
from app.db.models.signal import TradeSignal
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.market import api as market_api
from app.domains.market.analysis import FusedAnalysisService, PriceActionProxyService
from app.domains.market.services import CalendarService, MarketDataService, NewsService, WorkQueueService
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError
from app.providers.market_data.base import OHLCVCandle
from app.providers.market_data.ibkr_proxy_provider import IBKRProxyError
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


class _CoolingDownIBKRProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_snapshot(self, ticker: str):
        self.calls += 1
        raise IBKRProxyError(
            "IBKR proxy request failed for /contracts/search: HTTP 503 {'message': 'IBKR upstream is cooling down "
            "after recent failures', 'gateway_url': 'https://ibkr-gateway:5000', 'policy': 'reference', "
            "'retry_after_seconds': 2.612}"
        )

    def get_history(self, ticker: str, limit: int = 120):
        self.calls += 1
        raise IBKRProxyError(
            "IBKR proxy request failed for /contracts/search: HTTP 503 {'message': 'IBKR upstream is cooling down "
            "after recent failures', 'gateway_url': 'https://ibkr-gateway:5000', 'policy': 'reference', "
            "'retry_after_seconds': 2.612}"
        )

    def get_history(self, ticker: str, limit: int = 120):
        self.calls += 1
        raise TwelveDataError(
            "Twelve Data returned no values for SPY: You have run out of API credits for the current minute. "
            "Wait for the next minute"
        )


class _NoBridgeIBKRProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_snapshot(self, ticker: str):
        self.calls += 1
        raise IBKRProxyError(
            "IBKR proxy request failed for /contracts/search: HTTP 400 Bad Request "
            "{'error':'Bad Request: no bridge','statusCode':400}"
        )


class _SlowOverviewProvider:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = Lock()

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        del sec_type
        with self._lock:
            self.calls += 1
        time.sleep(0.05)
        return {
            "available": True,
            "symbol": ticker.upper(),
            "provider_source": "slow_test_provider",
            "market_signals": {},
            "options_sentiment": {},
            "corporate_events": [],
            "provider_error": None,
        }


class _ConcurrencyTrackingOverviewProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = Lock()

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        del sec_type
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return {
                "available": True,
                "symbol": ticker.upper(),
                "provider_source": "concurrency_test_provider",
                "market_signals": {},
                "options_sentiment": {},
                "corporate_events": [],
                "provider_error": None,
            }
        finally:
            with self._lock:
                self.active -= 1


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
    assert "price_action_context" in payload
    assert payload["quant_summary"]["risk_reward"] > 0
    assert payload["visual_summary"]["setup_type"] in {"breakout", "pullback", "consolidation", "range"}


def test_price_action_proxy_service_detects_daily_reversal_signals() -> None:
    service = PriceActionProxyService()
    candles = [
        OHLCVCandle(
            timestamp=f"2026-03-{day:02d}",
            open=101.2 + (idx % 2) * 0.1,
            high=103.2 + (idx % 3) * 0.1,
            low=100.0 + (idx % 2) * 0.2,
            close=101.0 + (idx % 3) * 0.15,
            volume=1_000 + idx * 25,
        )
        for idx, day in enumerate(range(1, 12), start=1)
    ]
    candles.append(
        OHLCVCandle(
            timestamp="2026-03-12",
            open=100.8,
            high=102.2,
            low=99.2,
            close=101.9,
            volume=5_000,
        )
    )

    context = service.analyze(candles=candles, relative_volume=2.1, atr_14=2.0)

    assert context["available"] is True
    assert context["method"] == "ohlcv_price_action_proxies_v1"
    assert context["primary_signal_code"] == "failed_breakdown_reversal"
    assert "rejection_wick_at_support" in context["triggered_signal_codes"]
    assert "high_relative_volume_reversal" in context["triggered_signal_codes"]
    assert context["reversal_context"]["breakdown_failed"] is True
    assert context["reversal_context"]["higher_timeframe_bias"] in {"supportive", "neutral", "hostile"}
    assert context["confirmation_bonus"] > 0


def test_price_action_proxy_service_detects_support_reclaim_confirmation() -> None:
    service = PriceActionProxyService()
    candles = [
        OHLCVCandle(
            timestamp=f"2026-03-{day:02d}",
            open=101.0 + (idx % 2) * 0.1,
            high=102.6 + (idx % 3) * 0.05,
            low=100.0 + (idx % 2) * 0.15,
            close=101.2 + (idx % 3) * 0.1,
            volume=1_000 + idx * 20,
        )
        for idx, day in enumerate(range(1, 11), start=1)
    ]
    candles.append(
        OHLCVCandle(
            timestamp="2026-03-11",
            open=100.9,
            high=101.1,
            low=99.1,
            close=99.4,
            volume=2_200,
        )
    )
    candles.append(
        OHLCVCandle(
            timestamp="2026-03-12",
            open=99.5,
            high=101.8,
            low=99.3,
            close=101.5,
            volume=3_500,
        )
    )

    context = service.analyze(candles=candles, relative_volume=1.4, atr_14=1.9)

    assert "support_reclaim_confirmation" in context["triggered_signal_codes"]
    assert context["reversal_context"]["support_reclaimed"] is True
    assert context["reversal_context"]["reclaim_level"] > 0
    assert context["follow_through_state"] in {"constructive", "uncertain"}


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


def test_fused_analysis_handles_sparse_intraday_derived_history_without_crashing() -> None:
    class _SparseHistoryService:
        def get_history(self, ticker: str, limit: int = 120):
            if ticker.upper() == "AVEX":
                return [
                    OHLCVCandle(
                        timestamp="2026-04-17",
                        open=23.01,
                        high=27.96,
                        low=23.0,
                        close=27.01,
                        volume=13_518_100.0,
                    )
                ]
            return [
                OHLCVCandle(
                    timestamp=f"2026-03-{day:02d}",
                    open=500.0 + idx,
                    high=501.5 + idx,
                    low=498.5 + idx,
                    close=500.8 + idx,
                    volume=1_000_000 + idx * 5000,
                )
                for idx, day in enumerate(range(1, 61), start=1)
            ]

    payload = FusedAnalysisService(market_data_service=_SparseHistoryService()).analyze_ticker("AVEX")

    assert payload["ticker"] == "AVEX"
    assert payload["decision"] in {"watch", "discard"}
    assert payload["decision"] != "paper_enter"
    assert payload["quant_summary"]["history_quality"] == "sparse"
    assert payload["quant_summary"]["history_bars"] == 1
    assert "History quality=sparse" in payload["rationale"]


def test_market_data_service_degrades_to_fallback_during_twelve_data_rate_limit() -> None:
    service = MarketDataService(raise_on_provider_error=True)
    service.provider_name = "twelve_data"
    service.provider = _RateLimitedProvider()

    first = service.get_snapshot("SPY")
    second = service.get_snapshot("QQQ")

    assert first.ticker == "SPY"
    assert second.ticker == "QQQ"
    assert service.provider.calls == 1


def test_market_data_service_degrades_to_fallback_during_ibkr_proxy_cooldown() -> None:
    service = MarketDataService(raise_on_provider_error=True)
    service.provider_name = "ibkr_proxy"
    service.provider = _CoolingDownIBKRProvider()

    first = service.get_snapshot("TOVX")
    second = service.get_snapshot("AAPL")

    assert first.ticker == "TOVX"
    assert second.ticker == "AAPL"
    assert service.provider.calls == 1
    assert service._provider_cooldown_until is not None


def test_market_data_service_degrades_to_fallback_when_ibkr_proxy_has_no_bridge() -> None:
    service = MarketDataService(raise_on_provider_error=True)
    service.provider_name = "ibkr_proxy"
    service.provider = _NoBridgeIBKRProvider()

    first = service.get_snapshot("SPY")
    second = service.get_snapshot("QQQ")

    assert first.ticker == "SPY"
    assert second.ticker == "QQQ"
    assert service.provider.calls == 1
    assert service._provider_cooldown_until is not None


def test_market_data_service_coalesces_concurrent_market_overview_requests() -> None:
    service = MarketDataService()
    service.provider = _SlowOverviewProvider()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.get_market_overview("MSFT"), range(4)))

    assert len(results) == 4
    assert all(result["symbol"] == "MSFT" for result in results)
    assert service.provider.calls == 1


def test_market_data_service_backpressure_limits_concurrent_requests_across_instances() -> None:
    settings = Settings(
        market_data_provider="ibkr_proxy",
        ibkr_proxy_base_url="https://shared-market-data-backpressure.test",
        market_data_max_concurrent_requests=1,
    )
    provider = _ConcurrencyTrackingOverviewProvider()
    first_service = MarketDataService(settings)
    second_service = MarketDataService(settings)
    first_service.provider = provider
    second_service.provider = provider

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_service.get_market_overview, "NVDA")
        second = executor.submit(second_service.get_market_overview, "MSFT")
        results = [first.result(), second.result()]

    assert [result["symbol"] for result in results] == ["NVDA", "MSFT"]
    assert provider.calls == 2
    assert provider.max_active == 1


def test_market_data_service_shared_cooldown_skips_repeated_failures_across_instances() -> None:
    settings = Settings(
        market_data_provider="twelve_data",
        twelve_data_api_key="shared-cooldown-key",
        market_data_max_concurrent_requests=1,
    )
    provider = _RateLimitedProvider()
    first_service = MarketDataService(settings, raise_on_provider_error=True)
    second_service = MarketDataService(settings, raise_on_provider_error=True)
    first_service.provider = provider
    second_service.provider = provider
    first_service.provider_name = "twelve_data"
    second_service.provider_name = "twelve_data"

    first = first_service.get_snapshot("SPY")
    second = second_service.get_snapshot("QQQ")

    assert first.ticker == "SPY"
    assert second.ticker == "QQQ"
    assert provider.calls == 1


def test_work_queue_surfaces_due_reanalysis_items_and_runtime_summary(client, session) -> None:
    now = datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc)
    watchlist = Watchlist(
        code="perf_queue",
        name="Performance Queue",
        hypothesis="Track due and deferred watchlist work.",
        status="active",
    )
    due_item = WatchlistItem(
        watchlist=watchlist,
        ticker="NVDA",
        state="watching",
        key_metrics={},
    )
    deferred_item = WatchlistItem(
        watchlist=watchlist,
        ticker="MSFT",
        state="watching",
        key_metrics={
            "reanalysis_runtime": {
                "version": "watchlist_reanalysis_runtime_v1",
                "last_evaluated_at": (now - timedelta(minutes=5)).isoformat(),
                "next_reanalysis_at": (now + timedelta(minutes=15)).isoformat(),
                "check_interval_seconds": 1200,
                "last_gate_reason": "awaiting_reanalysis_trigger",
                "market_session_label": "regular_open",
            }
        },
    )
    signal_one = TradeSignal(
        ticker="NVDA",
        timeframe="1D",
        signal_type="breakout_long",
        signal_time=now - timedelta(minutes=10),
        signal_context={
            "timing_profile": {
                "version": "ticker_analysis_timing_v1",
                "total_ms": 6400.0,
                "stages_ms": {
                    "decision_context": 5200.0,
                    "reanalysis_gate": 900.0,
                },
                "decision_context_timing": {
                    "stages_ms": {
                        "calendar_context": 4100.0,
                        "market_overview": 700.0,
                    }
                },
            }
        },
        status="watch",
    )
    signal_two = TradeSignal(
        ticker="MSFT",
        timeframe="1D",
        signal_type="breakout_long",
        signal_time=now - timedelta(minutes=4),
        signal_context={
            "timing_profile": {
                "version": "ticker_analysis_timing_v1",
                "total_ms": 8000.0,
                "stages_ms": {
                    "decision_context": 6100.0,
                    "reanalysis_gate": 1500.0,
                },
                "decision_context_timing": {
                    "stages_ms": {
                        "calendar_context": 4800.0,
                        "market_overview": 900.0,
                    }
                },
            }
        },
        status="watch",
    )

    session.add_all([watchlist, due_item, deferred_item, signal_one, signal_two])
    session.commit()

    response = client.get("/api/v1/work-queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["due_reanalysis_items"] == 1
    assert payload["summary"]["deferred_reanalysis_items"] == 1
    assert payload["summary"]["runtime_aware_watchlist_items"] == 1
    assert payload["summary"]["next_reanalysis_ticker"] == "MSFT"
    assert payload["summary"]["timing_samples"] == 2
    assert payload["summary"]["avg_total_ms"] == 7200.0
    assert payload["summary"]["avg_decision_context_ms"] == 5650.0
    assert payload["summary"]["avg_reanalysis_gate_ms"] == 1200.0
    assert payload["summary"]["dominant_decision_context_stage"] == "calendar_context"
    due_queue_items = [item for item in payload["items"] if item["item_type"] == "watchlist_reanalysis_due"]
    assert len(due_queue_items) == 1
    assert due_queue_items[0]["title"] == "Reanalyze NVDA"
    assert due_queue_items[0]["context"]["gate_reason"] == "first_review"


def test_work_queue_timing_summary_ignores_signals_without_timing_profile(client, session) -> None:
    session.add(
        TradeSignal(
            ticker="SHOP",
            timeframe="1D",
            signal_type="breakout_long",
            signal_time=datetime(2026, 4, 20, 11, 0, 0, tzinfo=timezone.utc),
            signal_context={"decision": "watch"},
            status="watch",
        )
    )
    session.commit()

    response = client.get("/api/v1/work-queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["timing_samples"] == 0
    assert payload["summary"]["avg_total_ms"] is None
    assert payload["summary"]["dominant_decision_context_stage"] is None


def test_work_queue_summary_exposes_calendar_cooldown_state(client) -> None:
    class RateLimitedCorporateProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_ticker_events(self, *, symbol: str, sec_type: str = "STK") -> list[object]:
            del symbol, sec_type
            self.calls += 1
            raise CalendarProviderError("IBKR proxy corporate events request failed: HTTP Error 429: Too Many Requests")

    provider = RateLimitedCorporateProvider()
    calendar_service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            ibkr_proxy_base_url="https://queue-cooldown-ui.test",
            calendar_ticker_events_cache_ttl_seconds=0,
            calendar_corporate_max_concurrent_requests=2,
        ),
        corporate_provider=provider,
        earnings_provider=None,
    )
    original = market_api.work_queue_service
    market_api.work_queue_service = WorkQueueService(calendar_service=calendar_service)
    try:
        calendar_service.get_ticker_event_context("NVDA", days_ahead=21)
        response = client.get("/api/v1/work-queue")
    finally:
        market_api.work_queue_service = original

    assert response.status_code == 200
    payload = response.json()
    corporate = payload["summary"]["calendar_provider_status"]["corporate"]
    assert corporate["configured"] is True
    assert corporate["cooling_down"] is True
    assert corporate["cooldown_remaining_seconds"] > 0
    assert corporate["concurrency_limit"] == 2
    assert provider.calls == 1


def test_work_queue_summary_exposes_market_data_cooldown_state(client) -> None:
    provider = _RateLimitedProvider()
    market_data_service = MarketDataService(
        Settings(
            market_data_provider="twelve_data",
            twelve_data_api_key="ui-cooldown-key",
            market_data_max_concurrent_requests=2,
        ),
        raise_on_provider_error=True,
    )
    market_data_service.provider = provider
    market_data_service.provider_name = "twelve_data"
    original = market_api.work_queue_service
    market_api.work_queue_service = WorkQueueService(market_data_service=market_data_service)
    try:
        market_data_service.get_snapshot("SPY")
        response = client.get("/api/v1/work-queue")
    finally:
        market_api.work_queue_service = original

    assert response.status_code == 200
    payload = response.json()
    market_data = payload["summary"]["market_data_provider_status"]["market_data"]
    assert market_data["configured"] is True
    assert market_data["cooling_down"] is True
    assert market_data["cooldown_remaining_seconds"] > 0
    assert market_data["concurrency_limit"] == 2
    assert provider.calls == 1


def test_work_queue_summary_exposes_news_cooldown_state(client) -> None:
    class RateLimitedNewsProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, max_results: int | None = None) -> list[object]:
            del query, max_results
            self.calls += 1
            raise NewsProviderError("GNews request failed: HTTP Error 429: Too Many Requests")

    provider = RateLimitedNewsProvider()
    news_service = NewsService(
        Settings(
            gnews_api_key="dummy",
            gnews_base_url="https://queue-news-cooldown-ui.test",
            gnews_cache_ttl_seconds=0,
            gnews_max_concurrent_requests=2,
        )
    )
    news_service.provider = provider
    original = market_api.work_queue_service
    market_api.work_queue_service = WorkQueueService(news_service=news_service)
    try:
        try:
            news_service.list_news("NVDA", max_results=3)
        except NewsProviderError:
            pass
        response = client.get("/api/v1/work-queue")
    finally:
        market_api.work_queue_service = original

    assert response.status_code == 200
    payload = response.json()
    gnews = payload["summary"]["news_provider_status"]["gnews"]
    assert gnews["configured"] is True
    assert gnews["cooling_down"] is True
    assert gnews["cooldown_remaining_seconds"] > 0
    assert gnews["concurrency_limit"] == 2
    assert provider.calls == 1
