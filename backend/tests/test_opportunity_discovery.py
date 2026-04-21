from types import SimpleNamespace

from app.domains.market.discovery import OpportunityDiscoveryService
from app.domains.strategy.schemas import WatchlistCreate
from app.domains.strategy.services import WatchlistService


class _FakeSignalService:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = {ticker.upper(): score for ticker, score in scores.items()}

    def analyze_ticker(self, ticker: str) -> dict:
        score = self.scores.get(ticker.upper(), 0.7)
        return {
            "combined_score": score,
            "quant_summary": {
                "alpha_gap_pct_20": round(score * 10, 2),
                "relative_volume": round(1.0 + score, 2),
            },
            "risk_reward": round(1.0 + score, 2),
        }


class _FakeMarketDataService:
    def __init__(self, provider) -> None:
        self.provider = provider

    def get_snapshot(self, ticker: str):
        return SimpleNamespace(month_performance=0.12 if ticker.upper() == "SPY" else 0.18)


class _FakeScannerProvider:
    def __init__(self, tickers: list[str]) -> None:
        self.tickers = tickers
        self.calls: list[dict] = []

    def get_scanner_universe(self, scan_types: list[str], *, instrument: str, location: str, filters: list[dict], limit: int) -> list[str]:
        self.calls.append(
            {
                "scan_types": scan_types,
                "instrument": instrument,
                "location": location,
                "filters": filters,
                "limit": limit,
            }
        )
        return self.tickers[:limit]


class _FailingScannerProvider:
    def get_scanner_universe(self, scan_types: list[str], *, instrument: str, location: str, filters: list[dict], limit: int) -> list[str]:
        raise RuntimeError("scanner unavailable")


class _FakeNewsService:
    def __init__(self, titles_by_ticker: dict[str, list[str]] | None = None) -> None:
        self.titles_by_ticker = {ticker.upper(): list(titles) for ticker, titles in (titles_by_ticker or {}).items()}

    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None):
        titles = self.titles_by_ticker.get(ticker.upper(), [])
        limit = max_results or len(titles)
        return [SimpleNamespace(title=title) for title in titles[:limit]]


class _FakeCalendarService:
    def __init__(self, events_by_ticker: dict[str, list[tuple[str, str]]] | None = None) -> None:
        self.events_by_ticker = {ticker.upper(): list(events) for ticker, events in (events_by_ticker or {}).items()}

    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21):
        events = self.events_by_ticker.get(ticker.upper(), [])
        return [
            SimpleNamespace(event_type=event_type, event_date=event_date)
            for event_type, event_date in events
        ]


class _FakeMacroContextService:
    def __init__(self, tracked_tickers: list[str] | None = None) -> None:
        self.tracked_tickers = tracked_tickers or []

    def get_context(self, session, limit: int = 6):
        del session, limit
        return SimpleNamespace(tracked_tickers=list(self.tracked_tickers))


def _create_watchlist(session, code: str) -> None:
    WatchlistService().create_watchlist(
        session,
        WatchlistCreate(
            code=code,
            name=code.replace("_", " ").title(),
            hypothesis="Discovery test watchlist.",
            status="active",
        ),
        event_source="test",
    )


def test_opportunity_discovery_uses_dynamic_ibkr_scanner_universe(session) -> None:
    _create_watchlist(session, "dynamic_universe_watchlist")
    provider = _FakeScannerProvider(["ANAB", "RPAY"])
    discovery_service = OpportunityDiscoveryService(
        market_data_service=_FakeMarketDataService(provider),
        signal_service=_FakeSignalService({"ANAB": 0.92, "RPAY": 0.84}),
        watchlist_service=WatchlistService(),
    )
    discovery_service.settings = discovery_service.settings.model_copy(deep=True)
    discovery_service.settings.opportunity_discovery_universe_source = "ibkr_scanner"
    discovery_service.settings.opportunity_discovery_scanner_types = "MOST_ACTIVE,TOP_PERC_GAIN"
    discovery_service.settings.opportunity_discovery_scanner_location = "STK.US.MAJOR"
    discovery_service.settings.opportunity_discovery_scanner_instrument = "STK"
    discovery_service.settings.opportunity_discovery_scanner_filters_json = '[{"code":"priceAbove","value":5}]'
    discovery_service.settings.opportunity_discovery_universe_limit = 10
    discovery_service.settings.opportunity_discovery_per_watchlist = 2
    discovery_service.settings.opportunity_discovery_min_score = 0.65

    result = discovery_service.refresh_active_watchlists(session)
    watchlist = WatchlistService().list_watchlists(session)[0]

    assert result["universe_source"] == "ibkr_scanner"
    assert result["scanner_types_used"] == ["MOST_ACTIVE", "TOP_PERC_GAIN"]
    assert result["universe_size"] == 2
    assert result["discovered_items"] == 2
    assert {item.ticker for item in watchlist.items} == {"ANAB", "RPAY"}
    assert provider.calls == [
        {
            "scan_types": ["MOST_ACTIVE", "TOP_PERC_GAIN"],
            "instrument": "STK",
            "location": "STK.US.MAJOR",
            "filters": [{"code": "priceAbove", "value": 5}],
            "limit": 10,
        }
    ]


def test_opportunity_discovery_falls_back_to_configured_universe_when_scanner_fails(session) -> None:
    _create_watchlist(session, "fallback_universe_watchlist")
    discovery_service = OpportunityDiscoveryService(
        market_data_service=_FakeMarketDataService(_FailingScannerProvider()),
        signal_service=_FakeSignalService({"AAPL": 0.9, "MSFT": 0.88}),
        watchlist_service=WatchlistService(),
    )
    discovery_service.settings = discovery_service.settings.model_copy(deep=True)
    discovery_service.settings.opportunity_discovery_universe_source = "ibkr_scanner"
    discovery_service.settings.opportunity_discovery_scanner_types = "MOST_ACTIVE"
    discovery_service.settings.opportunity_discovery_universe = "AAPL,MSFT"
    discovery_service.settings.opportunity_discovery_per_watchlist = 2
    discovery_service.settings.opportunity_discovery_min_score = 0.65

    result = discovery_service.refresh_active_watchlists(session)
    watchlist = WatchlistService().list_watchlists(session)[0]

    assert result["universe_source"] == "configured_list_fallback"
    assert result["scanner_types_used"] == []
    assert result["universe_size"] == 2
    assert result["discovered_items"] == 2
    assert {item.ticker for item in watchlist.items} == {"AAPL", "MSFT"}


def test_opportunity_discovery_uses_news_calendar_and_macro_context_to_rank_candidates(session) -> None:
    _create_watchlist(session, "context_rank_watchlist")
    provider = _FakeScannerProvider(["AAPL", "MSFT"])
    discovery_service = OpportunityDiscoveryService(
        market_data_service=_FakeMarketDataService(provider),
        signal_service=_FakeSignalService({"AAPL": 0.67, "MSFT": 0.72}),
        watchlist_service=WatchlistService(),
        news_service=_FakeNewsService({"AAPL": ["Apple raises guidance on strong demand"]}),
        calendar_service=_FakeCalendarService({"AAPL": [("earnings", "2026-04-30")]}),
        macro_context_service=_FakeMacroContextService(["AAPL"]),
    )
    discovery_service.settings = discovery_service.settings.model_copy(deep=True)
    discovery_service.settings.opportunity_discovery_universe_source = "ibkr_scanner"
    discovery_service.settings.opportunity_discovery_per_watchlist = 1
    discovery_service.settings.opportunity_discovery_min_score = 0.65

    result = discovery_service.refresh_active_watchlists(session)
    watchlist = WatchlistService().list_watchlists(session)[0]
    added = watchlist.items[0]

    assert result["discovered_items"] == 1
    assert added.ticker == "AAPL"
    assert added.score > 0.72
    assert added.key_metrics["base_combined_score"] == 0.67
    assert added.key_metrics["macro_tracked"] is True
    assert added.key_metrics["news_titles"] == ["Apple raises guidance on strong demand"]
    assert added.key_metrics["calendar_events"] == ["earnings:2026-04-30"]
    assert "macro_theme_alignment" in added.key_metrics["contextual_reasons"]
    assert result["top_candidates"][0]["ticker"] == "AAPL"
    assert result["top_candidates"][0]["base_score"] == 0.67


def test_opportunity_discovery_keeps_technical_floor_even_with_contextual_catalysts(session) -> None:
    _create_watchlist(session, "context_floor_watchlist")
    provider = _FakeScannerProvider(["PLTR"])
    discovery_service = OpportunityDiscoveryService(
        market_data_service=_FakeMarketDataService(provider),
        signal_service=_FakeSignalService({"PLTR": 0.5}),
        watchlist_service=WatchlistService(),
        news_service=_FakeNewsService({"PLTR": ["Palantir wins new contract"]}),
        calendar_service=_FakeCalendarService({"PLTR": [("earnings", "2026-04-30")]}),
        macro_context_service=_FakeMacroContextService(["PLTR"]),
    )
    discovery_service.settings = discovery_service.settings.model_copy(deep=True)
    discovery_service.settings.opportunity_discovery_universe_source = "ibkr_scanner"
    discovery_service.settings.opportunity_discovery_per_watchlist = 1
    discovery_service.settings.opportunity_discovery_min_score = 0.65

    result = discovery_service.refresh_active_watchlists(session)
    watchlist = WatchlistService().list_watchlists(session)[0]

    assert result["discovered_items"] == 0
    assert watchlist.items == []
