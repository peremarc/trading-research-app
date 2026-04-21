from datetime import date, timedelta
import threading
import time
from types import SimpleNamespace

from app.domains.learning.decisioning import DecisionContextAssemblerService, EntryScoringService
from app.domains.learning.relevance import FeatureRelevanceService
from app.providers.market_data.base import MarketSnapshot, OHLCVCandle


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


class _QuarterlyExpiryCalendarService(_EmptyCalendarService):
    def get_quarterly_expiry_context(self, *, as_of: date | None = None) -> dict:
        del as_of
        return {
            "available": True,
            "source": "stub_quarterly_expiry",
            "quarterly_expiry_date": "2026-06-18",
            "days_to_event": 1,
            "expiration_week": True,
            "pre_expiry_window": True,
            "expiry_day": False,
            "post_expiry_window": False,
            "phase": "tight_pre_expiry_window",
            "risk_penalty": 0.22,
            "reason": "Quarterly expiry is one day away; tighten selectivity.",
        }


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
        market_overviews: dict[str, dict] | None = None,
        options_sentiments: dict[str, dict] | None = None,
        options_rankings: dict[tuple[str, str], dict] | None = None,
    ) -> None:
        self.snapshots = {ticker.upper(): snapshot for ticker, snapshot in snapshots.items()}
        self.histories = {ticker.upper(): list(history) for ticker, history in histories.items()}
        self.market_overviews = {
            ticker.upper(): dict(payload)
            for ticker, payload in (market_overviews or {}).items()
        }
        self.options_sentiments = {
            ticker.upper(): dict(payload)
            for ticker, payload in (options_sentiments or {}).items()
        }
        self.options_rankings = {
            (basis, direction): dict(payload)
            for (basis, direction), payload in (options_rankings or {}).items()
        }

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        return self.snapshots[ticker.upper()]

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        return self.histories[ticker.upper()][-limit:]

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        del sec_type
        return dict(
            self.market_overviews.get(
                ticker.upper(),
                {
                    "available": False,
                    "symbol": ticker.upper(),
                    "market_signals": {},
                    "options_sentiment": {},
                    "corporate_events": [],
                    "provider_error": "missing market overview fixture",
                },
            )
        )

    def get_options_sentiment(self, ticker: str, *, sec_type: str = "STK") -> dict:
        del sec_type
        return dict(
            self.options_sentiments.get(
                ticker.upper(),
                {
                    "available": False,
                    "symbol": ticker.upper(),
                    "provider_error": "missing options sentiment fixture",
                },
            )
        )

    def get_options_sentiment_rankings(
        self,
        *,
        basis: str = "volume",
        direction: str = "high",
        instrument: str = "STK",
        location: str | None = None,
        limit: int = 20,
    ) -> dict:
        del instrument, location, limit
        return dict(
            self.options_rankings.get(
                (basis, direction),
                {
                    "available": False,
                    "basis": basis,
                    "direction": direction,
                    "contracts": [],
                    "provider_error": "missing ranking fixture",
                },
            )
        )


class _ParallelProbe:
    def __init__(self, *, sleep_seconds: float = 0.08) -> None:
        self.sleep_seconds = sleep_seconds
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def wait(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.sleep_seconds)
        finally:
            with self._lock:
                self.active -= 1


class _ParallelProbeNewsService(_EmptyNewsService):
    def __init__(self, probe: _ParallelProbe) -> None:
        self.probe = probe

    def list_news_for_ticker(self, ticker: str, *, max_results: int = 4) -> list:
        del ticker, max_results
        self.probe.wait()
        return []


class _ParallelProbeMarketDataService(_FakeMarketDataService):
    def __init__(self, *, probe: _ParallelProbe, snapshots: dict[str, MarketSnapshot], histories: dict[str, list[OHLCVCandle]]) -> None:
        super().__init__(snapshots=snapshots, histories=histories)
        self.probe = probe

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        del ticker, sec_type
        self.probe.wait()
        return {
            "available": True,
            "symbol": "MSFT",
            "market_signals": {},
            "options_sentiment": {},
            "corporate_events": [],
            "provider_error": None,
        }


def _make_history(*, start: float, step: float, close_bias: float, count: int = 25) -> list[OHLCVCandle]:
    candles: list[OHLCVCandle] = []
    for idx in range(count):
        base = start + (idx * step)
        candles.append(
            OHLCVCandle(
                timestamp=f"2026-03-{idx + 1:02d}",
                open=round(base, 2),
                high=round(base + 1.5, 2),
                low=round(base - 1.0, 2),
                close=round(base + close_bias, 2),
                volume=1_000_000 + (idx * 10_000),
            )
        )
    return candles


def test_decision_context_builds_supportive_airline_intermarket_context(session) -> None:
    market_data_service = _FakeMarketDataService(
        snapshots={
            "AAL": MarketSnapshot("AAL", 18.4, 17.6, 16.9, 15.2, 61.0, 1.7, 0.8, 0.04, 0.09),
            "JETS": MarketSnapshot("JETS", 24.0, 23.1, 22.5, 21.0, 59.0, 1.4, 0.6, 0.03, 0.06),
            "SPY": MarketSnapshot("SPY", 510.0, 507.0, 500.0, 485.0, 57.0, 1.1, 4.2, 0.01, 0.01),
            "USO": MarketSnapshot("USO", 73.0, 75.0, 78.0, 80.0, 43.0, 1.2, 1.4, -0.03, -0.07),
        },
        histories={
            "AAL": _make_history(start=14.0, step=0.18, close_bias=1.35),
            "JETS": _make_history(start=20.0, step=0.12, close_bias=1.2),
            "SPY": _make_history(start=490.0, step=0.4, close_bias=1.0),
            "USO": _make_history(start=82.0, step=-0.25, close_bias=-0.7),
        },
        options_sentiments={
            "AAL": {
                "available": True,
                "symbol": "AAL",
                "put_call_ratio": 1.34,
                "option_implied_vol_pct": 34.2,
                "provider_error": None,
            }
        },
    )
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_EmptyCalendarService(),
        news_service=_EmptyNewsService(),
        market_data_service=market_data_service,
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="AAL",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "combined_score": 0.81,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "relative_volume": 1.7, "risk_reward": 2.2},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.76},
            "risk_reward": 2.2,
        },
        market_context={"sector_tag": "airlines"},
    )

    intermarket = context["intermarket_context"]
    assert intermarket["applicable"] is True
    assert intermarket["available"] is True
    assert intermarket["theme"] == "airline_sector_intermarket"
    assert intermarket["bias"] == "supportive"
    assert intermarket["oil_pressure_state"] == "tailwind"
    assert intermarket["sector_strength_state"] == "strong"
    assert intermarket["ticker_vs_sector_state"] == "leading"
    assert intermarket["score"] > 0.7
    assert "oil_proxy_easing" in intermarket["supportive_signals"]
    assert "options_put_call_fear" in intermarket["supportive_signals"]
    assert intermarket["put_call_state"] == "fearful"
    assert intermarket["put_call_ratio_source"] == "snapshot"


def test_decision_context_parallelizes_independent_io_reads(session) -> None:
    probe = _ParallelProbe()
    market_data_service = _ParallelProbeMarketDataService(
        probe=probe,
        snapshots={
            "MSFT": MarketSnapshot("MSFT", 415.0, 410.0, 402.0, 385.0, 62.0, 1.5, 6.2, 0.03, 0.07),
        },
        histories={
            "MSFT": _make_history(start=380.0, step=1.0, close_bias=0.8),
        },
    )
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_EmptyCalendarService(),
        news_service=_ParallelProbeNewsService(probe),
        market_data_service=market_data_service,
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="MSFT",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "ticker": "MSFT",
            "combined_score": 0.8,
            "decision_confidence": 0.8,
            "quant_summary": {"ticker": "MSFT", "trend": "uptrend", "setup": "breakout", "relative_volume": 1.4},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.72},
        },
        market_context={},
    )

    timing_profile = context["timing_profile"]
    assert probe.max_active >= 2
    assert timing_profile["io_parallelism_enabled"] is True
    assert timing_profile["io_max_workers"] >= 2
    assert "corporate_calendar_context" in timing_profile["stages_ms"]
    assert "macro_calendar_context" in timing_profile["stages_ms"]


def test_decision_context_uses_options_ranking_fallback_when_snapshot_ratio_is_missing(session) -> None:
    market_data_service = _FakeMarketDataService(
        snapshots={
            "AAL": MarketSnapshot("AAL", 18.4, 17.6, 16.9, 15.2, 61.0, 1.7, 0.8, 0.04, 0.09),
            "JETS": MarketSnapshot("JETS", 24.0, 23.1, 22.5, 21.0, 59.0, 1.4, 0.6, 0.03, 0.06),
            "SPY": MarketSnapshot("SPY", 510.0, 507.0, 500.0, 485.0, 57.0, 1.1, 4.2, 0.01, 0.01),
            "USO": MarketSnapshot("USO", 73.0, 75.0, 78.0, 80.0, 43.0, 1.2, 1.4, -0.03, -0.07),
        },
        histories={
            "AAL": _make_history(start=14.0, step=0.18, close_bias=1.35),
            "JETS": _make_history(start=20.0, step=0.12, close_bias=1.2),
            "SPY": _make_history(start=490.0, step=0.4, close_bias=1.0),
            "USO": _make_history(start=82.0, step=-0.25, close_bias=-0.7),
        },
        options_sentiments={
            "AAL": {
                "available": True,
                "symbol": "AAL",
                "put_call_ratio": None,
                "put_call_volume_ratio": None,
                "option_implied_vol_pct": 28.5,
                "fallback_reason": "Use the ranking endpoint for operable put/call ratio data.",
                "provider_error": None,
            }
        },
        options_rankings={
            ("volume", "high"): {
                "available": True,
                "basis": "volume",
                "direction": "high",
                "contracts": [{"rank": 7, "symbol": "AAL", "ratio": 1.91}],
                "provider_error": None,
            },
            ("volume", "low"): {
                "available": True,
                "basis": "volume",
                "direction": "low",
                "contracts": [],
                "provider_error": None,
            },
        },
    )
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_EmptyCalendarService(),
        news_service=_EmptyNewsService(),
        market_data_service=market_data_service,
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="AAL",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "combined_score": 0.81,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "relative_volume": 1.7, "risk_reward": 2.2},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.76},
            "risk_reward": 2.2,
        },
        market_context={"sector_tag": "airlines"},
    )

    intermarket = context["intermarket_context"]
    assert intermarket["put_call_state"] == "fearful"
    assert intermarket["put_call_ratio_source"] == "volume_ranking_high"
    assert intermarket["options_sentiment"]["ranking_rank"] == 7
    assert intermarket["options_sentiment"]["option_implied_vol_pct"] == 28.5


def test_decision_context_prefers_market_overview_for_calendar_and_options(session) -> None:
    earnings_date = date.today() + timedelta(days=2)
    market_data_service = _FakeMarketDataService(
        snapshots={
            "AAL": MarketSnapshot("AAL", 18.4, 17.6, 16.9, 15.2, 61.0, 1.7, 0.8, 0.04, 0.09),
            "JETS": MarketSnapshot("JETS", 24.0, 23.1, 22.5, 21.0, 59.0, 1.4, 0.6, 0.03, 0.06),
            "SPY": MarketSnapshot("SPY", 510.0, 507.0, 500.0, 485.0, 57.0, 1.1, 4.2, 0.01, 0.01),
            "USO": MarketSnapshot("USO", 73.0, 75.0, 78.0, 80.0, 43.0, 1.2, 1.4, -0.03, -0.07),
        },
        histories={
            "AAL": _make_history(start=14.0, step=0.18, close_bias=1.35),
            "JETS": _make_history(start=20.0, step=0.12, close_bias=1.2),
            "SPY": _make_history(start=490.0, step=0.4, close_bias=1.0),
            "USO": _make_history(start=82.0, step=-0.25, close_bias=-0.7),
        },
        market_overviews={
            "AAL": {
                "available": True,
                "symbol": "AAL",
                "provider_source": "ibkr_proxy_market_overview",
                "market_signals": {"available": True, "last_price": 18.4},
                "options_sentiment": {
                    "available": True,
                    "symbol": "AAL",
                    "put_call_ratio": 1.41,
                    "option_implied_vol_pct": 31.5,
                    "provider_error": None,
                },
                "corporate_events": [
                    {
                        "event_type": "earnings",
                        "title": "Erng Call",
                        "event_date": earnings_date.isoformat(),
                        "ticker": "AAL",
                        "source": "ibkr_proxy_market_overview",
                    }
                ],
                "provider_error": None,
            }
        },
    )
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_EmptyCalendarService(),
        news_service=_EmptyNewsService(),
        market_data_service=market_data_service,
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="AAL",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "combined_score": 0.81,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "relative_volume": 1.7, "risk_reward": 2.2},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.76},
            "risk_reward": 2.2,
        },
        market_context={"sector_tag": "airlines"},
    )

    calendar_context = context["calendar_context"]
    intermarket = context["intermarket_context"]

    assert calendar_context["corporate_event_count"] == 1
    assert calendar_context["corporate_events"][0]["title"] == "Erng Call"
    assert calendar_context["near_earnings_days"] == 2
    assert intermarket["put_call_state"] == "fearful"
    assert intermarket["put_call_ratio"] == 1.41
    assert intermarket["options_sentiment"]["option_implied_vol_pct"] == 31.5


def test_decision_context_builds_quarterly_expiry_overlay(session) -> None:
    market_data_service = _FakeMarketDataService(
        snapshots={
            "NVDA": MarketSnapshot("NVDA", 116.0, 112.0, 106.0, 95.0, 63.0, 1.6, 4.2, 0.03, 0.08),
        },
        histories={
            "NVDA": _make_history(start=92.0, step=0.9, close_bias=1.1),
        },
    )
    assembler = DecisionContextAssemblerService(
        macro_context_service=_FakeMacroContextService(),
        calendar_service=_QuarterlyExpiryCalendarService(),
        news_service=_EmptyNewsService(),
        market_data_service=market_data_service,
    )

    context = assembler.build_trade_candidate_context(
        session,
        ticker="NVDA",
        strategy_id=None,
        strategy_version_id=None,
        signal_payload={
            "combined_score": 0.84,
            "quant_summary": {"trend": "uptrend", "setup": "breakout", "relative_volume": 1.9, "risk_reward": 2.4},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.79},
            "risk_reward": 2.4,
        },
        market_context={"sector_tag": "technology"},
    )

    calendar_context = context["calendar_context"]
    candidate_profile = context["risk_budget"]["candidate_profile"]
    assert calendar_context["quarterly_expiry_date"] == "2026-06-18"
    assert calendar_context["days_to_quarterly_expiry"] == 1
    assert calendar_context["pre_expiry_window"] is True
    assert calendar_context["expiration_week"] is True
    assert calendar_context["expiry_context"]["phase"] == "tight_pre_expiry_window"
    assert "quarterly_expiry_window" in candidate_profile["event_risk_flags"]
    assert "quarterly_expiry_tight_window" in candidate_profile["event_risk_flags"]


def test_entry_scoring_downgrades_when_airline_intermarket_context_is_hostile() -> None:
    scoring = EntryScoringService()

    result = scoring.evaluate(
        signal_payload={
            "combined_score": 0.88,
            "quant_summary": {"trend": "uptrend", "setup": "pullback", "risk_reward": 2.4},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.82},
            "risk_reward": 2.4,
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {"score": 0.55, "active_regimes": [], "alignments": [], "conflicts": []},
            "calendar_context": {},
            "news_context": {},
            "intermarket_context": {
                "applicable": True,
                "available": True,
                "score": 0.12,
                "bias": "headwind",
                "risk_flags": ["oil_proxy_rising", "sector_underperforming_spy"],
                "supportive_signals": [],
                "summary": "Airline intermarket context for AAL is headwind.",
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
    assert "intermarket_conflict" in result["guard_results"]["types"]
    assert "intermarket_fit=0.12" in result["summary"]


def test_entry_scoring_blocks_reversal_proxy_against_hostile_trend_context() -> None:
    scoring = EntryScoringService()

    result = scoring.evaluate(
        signal_payload={
            "combined_score": 0.84,
            "quant_summary": {"trend": "downtrend", "setup": "pullback", "risk_reward": 2.1},
            "visual_summary": {"setup_type": "pullback", "visual_score": 0.78},
            "risk_reward": 2.1,
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {"score": 0.55, "active_regimes": [], "alignments": [], "conflicts": []},
            "calendar_context": {},
            "news_context": {},
            "price_action_context": {
                "available": True,
                "primary_signal_code": "failed_breakdown_reversal",
                "signal_count": 2,
                "confirmation_bonus": 0.05,
                "summary": "Daily price action proxy is supportive.",
                "higher_timeframe_bias": "hostile",
                "follow_through_state": "at_risk",
            },
            "intermarket_context": {"applicable": False},
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
    assert "price_action_conflict" in result["guard_results"]["types"]
    assert "clear daily downtrend" in result["summary"]


def test_entry_scoring_penalizes_breakout_during_quarterly_expiry_window() -> None:
    scoring = EntryScoringService()

    result = scoring.evaluate(
        signal_payload={
            "combined_score": 0.86,
            "quant_summary": {"trend": "uptrend", "setup": "breakout", "risk_reward": 2.3},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.8},
            "risk_reward": 2.3,
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {"score": 0.55, "active_regimes": [], "alignments": [], "conflicts": []},
            "calendar_context": {
                "expiry_context": {
                    "available": True,
                    "quarterly_expiry_date": "2026-06-18",
                    "days_to_event": 1,
                    "expiration_week": True,
                    "pre_expiry_window": True,
                    "expiry_day": False,
                    "post_expiry_window": False,
                    "phase": "tight_pre_expiry_window",
                    "reason": "Quarterly expiry is one day away; tighten selectivity.",
                }
            },
            "news_context": {},
            "price_action_context": {"available": False},
            "intermarket_context": {"applicable": False},
            "portfolio": {},
            "risk_budget": {
                "remaining_portfolio_risk_amount": 1000.0,
                "per_trade_risk_amount": 100.0,
                "max_portfolio_risk_amount": 1000.0,
                "candidate_profile": {"event_risk_flags": ["quarterly_expiry_tight_window"]},
            },
            "regime_policy": {
                "entry_allowed": True,
                "risk_multiplier": 1.0,
                "allowed_playbooks": ["breakout_long"],
                "playbook": "breakout_long",
                "max_new_positions": 3,
                "opened_positions_so_far": 0,
            },
            "learned_rule_guard": None,
            "supporting_context_rules": [],
        },
    )

    assert result["score_breakdown"]["calendar_score"] == 0.4
    assert any("expiry" in advisory.lower() for advisory in result["guard_results"]["advisories"])


def test_feature_relevance_extracts_intermarket_features_and_combos() -> None:
    service = FeatureRelevanceService()
    snapshot = SimpleNamespace(
        quant_features={"trend": "uptrend", "setup": "pullback", "relative_volume": 1.8, "risk_reward": 2.3},
        visual_features={"setup_type": "pullback"},
        position_context={
            "decision_context": {
                "calendar_context": {
                    "expiry_context": {
                        "available": True,
                        "days_to_event": 1,
                        "expiration_week": True,
                        "pre_expiry_window": True,
                        "expiry_day": False,
                        "post_expiry_window": False,
                        "phase": "tight_pre_expiry_window",
                    }
                },
                "news_context": {},
                "macro_context": {},
                "price_action_context": {
                    "available": True,
                    "primary_signal_code": "support_reclaim_confirmation",
                    "bias": "supportive",
                    "volume_state": "normal",
                    "close_location_state": "strong_close",
                    "higher_timeframe_bias": "supportive",
                    "follow_through_state": "constructive",
                },
                "intermarket_context": {
                    "applicable": True,
                    "available": True,
                    "bias": "supportive",
                    "oil_pressure_state": "tailwind",
                    "sector_strength_state": "strong",
                    "put_call_state": "fearful",
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

    assert ("intermarket", "airline_bias", "supportive") in features
    assert ("intermarket", "oil_pressure_state", "tailwind") in features
    assert ("intermarket", "sector_strength_state", "strong") in features
    assert ("intermarket", "put_call_state", "fearful") in features
    assert ("calendar", "expiration_week", "true") in features
    assert ("calendar", "pre_expiry_window", "true") in features
    assert ("calendar", "days_to_quarterly_expiry_bucket", "T-1") in features
    assert ("price_action", "higher_timeframe_bias", "supportive") in features
    assert ("price_action", "follow_through_state", "constructive") in features
    assert ("combo", "setup__airline_bias", "pullback|supportive") in features
    assert ("combo", "setup__days_to_quarterly_expiry_bucket", "pullback|T-1") in features
    assert ("combo", "sector_strength__oil_pressure", "strong|tailwind") in features
    assert ("combo", "sector_strength__put_call_state", "strong|fearful") in features
    assert (
        "combo",
        "price_action_primary__higher_timeframe_bias",
        "support_reclaim_confirmation|supportive",
    ) in features
