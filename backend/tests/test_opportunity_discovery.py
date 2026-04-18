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
            "quant_summary": {"alpha_gap_pct_20": round(score * 10, 2)},
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
