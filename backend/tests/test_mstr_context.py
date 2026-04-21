from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from app.domains.learning.decisioning import (
    DecisionContextAssemblerService,
    EntryScoringService,
    PositionSizingService,
)
from app.domains.learning.relevance import FeatureRelevanceService
from app.domains.market.services import MSTRContextService
from app.providers.market_data.base import MarketSnapshot, OHLCVCandle
from app.providers.strategy_company import StrategyCompanyProvider, StrategyCompanyProviderError


class _FakeMacroContextResult:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"active_regimes": [], "tracked_tickers": []}

    def model_dump(self, mode: str = "json") -> dict:
        del mode
        return dict(self.payload)


class _FakeMacroContextService:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"active_regimes": [], "tracked_tickers": []}

    def get_context(self, session, limit: int = 6):
        del session, limit
        return _FakeMacroContextResult(self.payload)


class _EmptyCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 14) -> list:
        del ticker, days_ahead
        return []

    def list_macro_events(self, *, days_ahead: int = 7) -> list:
        del days_ahead
        return []


class _EmptyNewsService:
    def list_news_for_ticker(self, ticker: str, *, max_results: int = 4) -> list:
        del ticker, max_results
        return []


class _FakeMarketDataService:
    def __init__(
        self,
        *,
        snapshots: dict[str, MarketSnapshot],
        histories: dict[str, list[OHLCVCandle]],
    ) -> None:
        self.snapshots = {ticker.upper(): snapshot for ticker, snapshot in snapshots.items()}
        self.histories = {ticker.upper(): list(history) for ticker, history in histories.items()}

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        return self.snapshots[ticker.upper()]

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        return self.histories[ticker.upper()][-limit:]


class _StaticStrategyCompanyProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = dict(payload)

    def get_mstr_metrics(self) -> dict:
        return dict(self.payload)


class _StaticMSTRContextService:
    def __init__(self, payload: dict) -> None:
        self.payload = dict(payload)

    def build_context(self, *, ticker: str, market_context: dict | None = None, signal_payload: dict | None = None) -> dict:
        del ticker, market_context, signal_payload
        return dict(self.payload)


class _FixtureStrategyCompanyProvider(StrategyCompanyProvider):
    def __init__(
        self,
        fixtures: dict[str, str],
        *,
        cache_path: Path,
        cache_ttl_seconds: int = 3600,
        fail: bool = False,
    ) -> None:
        super().__init__(cache_path=cache_path, cache_ttl_seconds=cache_ttl_seconds)
        self.fixtures = dict(fixtures)
        self.fail = fail

    def _request_text(self, url: str) -> str:
        if self.fail:
            raise StrategyCompanyProviderError("forced upstream failure")
        return self.fixtures[url]


def _make_history(*, start: float, step: float, count: int = 90) -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    current_date = date.today() - timedelta(days=count)
    for idx in range(count):
        base = start + (idx * step)
        candles.append(
            OHLCVCandle(
                timestamp=current_date.isoformat(),
                open=round(base - 3, 2),
                high=round(base + 4, 2),
                low=round(base - 6, 2),
                close=round(base, 2),
                volume=1_000_000 + (idx * 10_000),
            )
        )
        current_date += timedelta(days=1)
    return candles


def _strategy_payload() -> dict:
    last_purchase = date.today() - timedelta(days=6)
    previous_purchase = date.today() - timedelta(days=13)
    prior_purchase = date.today() - timedelta(days=27)
    latest_shares = date.today() - timedelta(days=7)
    previous_shares = date.today() - timedelta(days=19)
    older_shares = date.today() - timedelta(days=110)
    return {
        "available": True,
        "source": "strategy.com_next_data_v1",
        "as_of": last_purchase.isoformat(),
        "stale": False,
        "used_fallback": False,
        "provider_error": None,
        "cache": {"hit": "live"},
        "stats": {
            "as_of_date": last_purchase.isoformat(),
            "btc_holdings": 780897,
            "basic_shares_outstanding": 346819000,
            "cash": 2250000000,
            "debt": 8253923000,
            "pref": 11354730700,
            "btc_yield_ytd": 5.6,
            "btc_gain_ytd": 2736,
        },
        "latest_purchase": {
            "date_of_purchase": last_purchase.isoformat(),
            "btc_holdings": 780897,
            "assumed_diluted_shares_outstanding": 379423000,
            "basic_shares_outstanding": 346823000,
            "btc_yield_ytd": 5.6,
            "btc_gain_ytd": 2736,
            "btc_reserve_millions": 57229,
            "btc_count_change": 13927,
        },
        "latest_shares": {
            "date": latest_shares.isoformat(),
            "assumed_diluted_shares_outstanding": 379423000,
            "basic_shares_outstanding": 346823000,
            "total_bitcoin_holdings": 780897,
        },
        "purchases_history": [
            {
                "date_of_purchase": prior_purchase.isoformat(),
                "btc_holdings": 762099,
                "assumed_diluted_shares_outstanding": 377847000,
                "basic_shares_outstanding": 345594000,
                "btc_yield_ytd": 3.4,
                "btc_gain_ytd": 1624,
                "btc_reserve_millions": 53476,
                "btc_count_change": 1031,
            },
            {
                "date_of_purchase": previous_purchase.isoformat(),
                "btc_holdings": 766970,
                "assumed_diluted_shares_outstanding": 379425000,
                "basic_shares_outstanding": 346819000,
                "btc_yield_ytd": 3.7,
                "btc_gain_ytd": 1650,
                "btc_reserve_millions": 51295,
                "btc_count_change": 4871,
            },
            {
                "date_of_purchase": last_purchase.isoformat(),
                "btc_holdings": 780897,
                "assumed_diluted_shares_outstanding": 379423000,
                "basic_shares_outstanding": 346823000,
                "btc_yield_ytd": 5.6,
                "btc_gain_ytd": 2736,
                "btc_reserve_millions": 57229,
                "btc_count_change": 13927,
            },
        ],
        "shares_history": [
            {
                "date": older_shares.isoformat(),
                "assumed_diluted_shares_outstanding": 344897000,
                "basic_shares_outstanding": 312062000,
                "total_bitcoin_holdings": 672500,
            },
            {
                "date": previous_shares.isoformat(),
                "assumed_diluted_shares_outstanding": 378834000,
                "basic_shares_outstanding": 346223000,
                "total_bitcoin_holdings": 762099,
            },
            {
                "date": latest_shares.isoformat(),
                "assumed_diluted_shares_outstanding": 379423000,
                "basic_shares_outstanding": 346823000,
                "total_bitcoin_holdings": 780897,
            },
        ],
    }


def _wrap_next_data(page_props: dict) -> str:
    payload = {
        "props": {
            "pageProps": page_props,
        }
    }
    return (
        "<html><head></head><body>"
        f"<script id=\"__NEXT_DATA__\" type=\"application/json\">{json.dumps(payload)}</script>"
        "</body></html>"
    )


def test_strategy_company_provider_normalizes_payload_and_uses_cache_fallback(tmp_path) -> None:
    fixtures = {
        "https://www.strategy.com/btc": _wrap_next_data(
            {
                "btcTrackerData": [
                    {
                        "as_of_date": "2026-04-13",
                        "btc_holdings": 780897,
                        "basic_shares_outstanding": 346819000,
                        "cash": 2250000000,
                        "debt": 8253923000,
                        "pref": 11354730700,
                        "btc_yield_ytd": 5.6,
                        "btc_gain_ytd": 2736,
                        "strk_metrics": {"shares": 14020744, "cumulative_notional": 13701920000, "dividend": 8},
                    }
                ]
            }
        ),
        "https://www.strategy.com/purchases": _wrap_next_data(
            {
                "bitcoinData": [
                    {
                        "date_of_purchase": "2026-04-06",
                        "btc_holdings": 766970,
                        "assumed_diluted_shares_outstanding": 379425000,
                        "basic_shares_outstanding": 346819000,
                        "btc_yield_ytd": 3.7,
                        "btc_gain_ytd": 1650,
                        "btc_nav": 51295,
                        "count": 4871,
                    },
                    {
                        "date_of_purchase": "2026-04-13",
                        "btc_holdings": 780897,
                        "assumed_diluted_shares_outstanding": 379423000,
                        "basic_shares_outstanding": 346823000,
                        "btc_yield_ytd": 5.6,
                        "btc_gain_ytd": 2736,
                        "btc_nav": 57229,
                        "count": 13927,
                    },
                ]
            }
        ),
        "https://www.strategy.com/shares": _wrap_next_data(
            {
                "shares": [
                    {
                        "date": "2026-03-31",
                        "assumed_diluted_shares_outstanding": 378834,
                        "basic_shares_outstanding": 346223,
                        "total_bitcoin_holdings": 762099,
                    },
                    {
                        "date": "2026-04-12",
                        "assumed_diluted_shares_outstanding": 379423,
                        "basic_shares_outstanding": 346823,
                        "total_bitcoin_holdings": 780897,
                    },
                ]
            }
        ),
    }

    cache_path = tmp_path / "strategy_company.json"
    live_provider = _FixtureStrategyCompanyProvider(fixtures, cache_path=cache_path, cache_ttl_seconds=0)
    live_payload = live_provider.get_mstr_metrics()

    assert live_payload["available"] is True
    assert live_payload["stats"]["btc_holdings"] == 780897
    assert live_payload["shares_history"][-1]["assumed_diluted_shares_outstanding"] == 379423000
    assert live_payload["latest_purchase"]["btc_reserve_millions"] == 57229

    fallback_provider = _FixtureStrategyCompanyProvider(
        fixtures,
        cache_path=cache_path,
        cache_ttl_seconds=0,
        fail=True,
    )
    fallback_payload = fallback_provider.get_mstr_metrics()

    assert fallback_payload["available"] is True
    assert fallback_payload["used_fallback"] is True
    assert fallback_payload["provider_error"] == "forced upstream failure"


def test_mstr_context_builds_supportive_overlay() -> None:
    service = MSTRContextService(
        market_data_service=_FakeMarketDataService(
            snapshots={
                "MSTR": MarketSnapshot("MSTR", 350.0, 332.0, 318.0, 260.0, 66.0, 1.9, 14.0, 0.07, 0.18),
                "IBIT": MarketSnapshot("IBIT", 63.0, 60.0, 58.0, 49.0, 64.0, 1.5, 1.8, 0.05, 0.12),
            },
            histories={
                "MSTR": _make_history(start=260.0, step=1.2),
                "IBIT": _make_history(start=45.0, step=0.22),
            },
        ),
        strategy_company_provider=_StaticStrategyCompanyProvider(_strategy_payload()),
        btc_proxy_symbol="IBIT",
    )

    context = service.build_context(ticker="MSTR")

    assert context["applicable"] is True
    assert context["available"] is True
    assert context["btc_proxy_state"] == "strong"
    assert context["atm_risk_context"] == "low"
    assert context["mnav_bucket"] == "2_0_to_2_5"
    assert context["recent_btc_purchase"] is True
    assert context["exposure_preference"] == "prefer_mstr_over_btc_proxy"
    assert context["score"] >= 0.7


def test_decision_context_wires_mstr_overlay_into_candidate_budget(session) -> None:
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_EmptyCalendarService(),
        news_service=_EmptyNewsService(),
        market_data_service=_FakeMarketDataService(
            snapshots={"MSTR": MarketSnapshot("MSTR", 350.0, 332.0, 318.0, 260.0, 66.0, 1.9, 14.0, 0.07, 0.18)},
            histories={"MSTR": _make_history(start=260.0, step=1.2)},
        ),
        mstr_context_service=_StaticMSTRContextService(
            {
                "applicable": True,
                "available": True,
                "score": 0.76,
                "bias": "supportive",
                "mnav_bucket": "2_0_to_2_5",
                "atm_risk_context": "low",
                "btc_proxy_state": "strong",
                "summary": "Supportive MSTR context.",
            }
        ),
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="MSTR",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "combined_score": 0.84,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "relative_volume": 1.7, "risk_reward": 2.2},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.78},
            "risk_reward": 2.2,
        },
        market_context={"sector_tag": "technology"},
    )

    assert context["mstr_context"]["applicable"] is True
    assert context["mstr_context"]["atm_risk_context"] == "low"
    assert context["risk_budget"]["candidate_profile"]["mstr_context"]["mnav_bucket"] == "2_0_to_2_5"


def test_entry_scoring_blocks_mstr_when_high_mnav_meets_weak_btc_proxy() -> None:
    scoring = EntryScoringService()

    result = scoring.evaluate(
        signal_payload={
            "combined_score": 0.86,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "risk_reward": 2.3},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.8},
            "risk_reward": 2.3,
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {"score": 0.55, "active_regimes": [], "alignments": [], "conflicts": []},
            "calendar_context": {},
            "news_context": {},
            "price_action_context": {"available": False},
            "intermarket_context": {"applicable": False},
            "mstr_context": {
                "applicable": True,
                "available": True,
                "score": 0.18,
                "bias": "headwind",
                "atm_risk_context": "high",
                "btc_proxy_state": "weak",
                "summary": "BTC proxy is weak while MSTR carries elevated ATM/dilution risk.",
                "risk_flags": ["btc_weak_with_high_mnav"],
            },
            "portfolio": {},
            "risk_budget": {
                "remaining_portfolio_risk_amount": 1000.0,
                "per_trade_risk_amount": 100.0,
                "max_portfolio_risk_amount": 1000.0,
            },
            "regime_policy": {
                "entry_allowed": True,
                "risk_multiplier": 1.0,
                "allowed_playbooks": ["pullback_long"],
                "playbook": "pullback_long",
                "max_new_positions": 3,
                "opened_positions_so_far": 0,
            },
            "learned_rule_guard": None,
            "supporting_context_rules": [],
        },
    )

    assert result["recommended_action"] == "watch"
    assert result["guard_results"]["blocked"] is True
    assert "mstr_context_conflict" in result["guard_results"]["types"]
    assert "mstr_fit=0.18" in result["summary"]


def test_position_sizing_reduces_mstr_risk_when_atm_context_is_hostile() -> None:
    sizing_service = PositionSizingService()

    result = sizing_service.size_trade_candidate(
        signal_payload={
            "entry_price": 350.0,
            "stop_price": 328.0,
            "risk_reward": 2.4,
            "decision_confidence": 0.78,
            "combined_score": 0.78,
            "quant_summary": {"atr_14": 14.0},
        },
        decision_context={
            "strategy_rules": {},
            "portfolio": {},
            "risk_budget": {
                "capital_base": 100000.0,
                "per_trade_risk_amount": 1000.0,
                "remaining_portfolio_risk_amount": 1000.0,
                "max_notional_fraction_per_trade": 0.2,
                "candidate_profile": {
                    "event_risk_flags": [],
                    "mstr_context": {
                        "applicable": True,
                        "available": True,
                        "atm_risk_context": "high",
                        "btc_proxy_state": "weak",
                    },
                },
            },
            "regime_policy": {"risk_multiplier": 1.0, "entry_allowed": True},
        },
    )

    assert result["blocked"] is False
    assert result["position_sizing"]["specialized_context_multiplier"] == 0.68
    assert "size reduced by specialized context risk" in result["position_sizing"]["reasons"]


def test_feature_relevance_extracts_mstr_features_and_combos() -> None:
    service = FeatureRelevanceService()
    snapshot = SimpleNamespace(
        quant_features={"trend": "uptrend", "setup": "pullback", "relative_volume": 1.8, "risk_reward": 2.3},
        visual_features={"setup_type": "pullback"},
        position_context={
            "decision_context": {
                "calendar_context": {},
                "news_context": {},
                "macro_context": {},
                "price_action_context": {"available": False},
                "intermarket_context": {"applicable": False},
                "mstr_context": {
                    "applicable": True,
                    "available": True,
                    "mnav_bucket": "2_0_to_2_5",
                    "atm_risk_context": "low",
                    "recent_btc_purchase": True,
                    "bps_trend": "rising",
                    "share_dilution_accelerating": False,
                    "btc_proxy_state": "strong",
                },
            }
        },
        calendar_context={},
        news_context={},
        web_context={},
        macro_context={},
        ai_context={},
        execution_outcome="watch",
    )

    features = {
        (feature.scope, feature.key, feature.value)
        for feature in service._extract_features(snapshot)
    }

    assert ("mstr", "context_available", "true") in features
    assert ("mstr", "mnav_bucket", "2_0_to_2_5") in features
    assert ("mstr", "atm_risk_context", "low") in features
    assert ("mstr", "recent_btc_purchase", "true") in features
    assert ("mstr", "bps_trend", "rising") in features
    assert ("mstr", "btc_proxy_state", "strong") in features
    assert ("combo", "setup__mnav_bucket", "pullback|2_0_to_2_5") in features
    assert ("combo", "mstr_atm_risk__btc_proxy_state", "low|strong") in features
