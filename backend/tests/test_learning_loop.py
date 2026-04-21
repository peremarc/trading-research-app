from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.watchlist import WatchlistItem
from app.db.models.decision_context import StrategyContextRule
from app.db.models.knowledge_claim import KnowledgeClaim, KnowledgeClaimEvidence
from app.domains.learning.agent import AIDecisionError
from app.domains.learning import api as learning_api
from app.providers.calendar import CalendarEvent
from app.providers.market_data.base import MarketSnapshot
from app.providers.news import NewsArticle
from app.providers.web_research import WebPage, WebSearchResult


class _FixedMarketSession:
    def __init__(self, *, is_regular_session_open: bool, session_label: str) -> None:
        self.is_regular_session_open = is_regular_session_open
        self.session_label = session_label
        self.next_regular_open = "2026-04-20T13:30:00+00:00"
        self.next_regular_close = None

    def to_payload(self) -> dict:
        return {
            "market": "us_equities",
            "timezone": "America/New_York",
            "session_label": self.session_label,
            "is_weekend": self.session_label == "weekend",
            "is_trading_day": self.session_label != "weekend",
            "is_regular_session_open": self.is_regular_session_open,
            "is_extended_hours": not self.is_regular_session_open and self.session_label in {"pre_market", "after_hours"},
            "now_utc": "2026-04-18T12:00:00+00:00",
            "now_local": "2026-04-18T08:00:00-04:00",
            "next_regular_open": "2026-04-20T13:30:00+00:00",
            "next_regular_close": None,
        }


class _FixedMarketHoursService:
    def __init__(self, *, is_regular_session_open: bool, session_label: str) -> None:
        self.session = _FixedMarketSession(
            is_regular_session_open=is_regular_session_open,
            session_label=session_label,
        )

    def get_session_state(self):
        return self.session


class _StableMarketDataService:
    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=ticker,
            price=104.0,
            sma_20=103.0,
            sma_50=100.0,
            sma_200=95.0,
            rsi_14=62.0,
            relative_volume=1.8,
            atr_14=2.0,
            week_performance=0.04,
            month_performance=0.1,
        )


class _QuietNewsService:
    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None):
        del ticker, max_results
        return []


class _QuietCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        del ticker, days_ahead
        return []

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        del days_ahead
        return []


class NearTermCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {ticker}",
                event_date="2026-04-20",
                ticker=ticker,
                source="stub",
            )
        ]

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        return []


class _MacroCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        del ticker, days_ahead
        return []

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        del days_ahead
        return [
            CalendarEvent(
                event_type="macro",
                title="Fed rate decision",
                event_date="2026-04-20",
                country="US",
                impact="high",
                estimate="4.50%",
                previous="4.75%",
                source="stub",
            )
        ]


class _IdleResearchNewsService:
    class _Article:
        def __init__(self, title: str) -> None:
            self.title = title

    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None):
        return [self._Article(f"{ticker} catalyst update")]


class _MacroResearchNewsService:
    def list_news(self, query: str, *, max_results: int | None = None):
        del max_results
        return [
            NewsArticle(
                title=f"Macro headline for {query}",
                description="Synthetic macro headline for testing.",
                url="https://www.reuters.com/world/test-macro-story",
                source_name="Reuters",
                published_at="2026-04-19T08:00:00Z",
            )
        ]


class _MacroResearchWebService:
    def search(self, query: str, *, max_results: int | None = None, domains: list[str] | None = None):
        del max_results, domains
        return [
            WebSearchResult(
                title=f"Analysis page for {query}",
                url="https://www.reuters.com/markets/test-macro-analysis",
                snippet="Synthetic article snippet",
                source="duckduckgo",
            )
        ]

    def fetch_article(self, url: str, *, max_chars: int | None = None):
        del max_chars
        return WebPage(
            url=url,
            title="Synthetic macro article",
            text="A macro catalyst is likely to reprice rates-sensitive assets and defensive rotation proxies.",
            source="http_fetch",
        )


def test_orchestrator_do_persists_signals(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    response = client.post("/api/v1/orchestrator/do")
    assert response.status_code == 200

    payload = response.json()
    assert payload["metrics"]["generated_signals"] >= 6
    assert payload["metrics"]["discovered_items"] >= 1
    assert len(payload["candidates"]) >= 6
    assert all(candidate["signal_id"] is not None for candidate in payload["candidates"])
    assert all(candidate["trade_signal_id"] == candidate["signal_id"] for candidate in payload["candidates"])

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    payloads = signals.json()
    assert len(payloads) >= 6
    signal_context = payloads[0]["signal_context"]
    assert signal_context["research_plan"]["tool_budget"]["max_research_steps"] >= 9
    assert signal_context["decision_trace"]["initial_hypothesis"]
    assert signal_context["decision_trace"]["decision_source"] in {
        "deterministic_scoring",
        "deterministic_pre_ai",
        "ai_overlay",
        "execution_guard",
    }
    assert signal_context["reanalysis_policy"]["policy_version"] == "event_driven_v1"
    assert signal_context["reanalysis_policy"]["criteria_summary"]
    assert signal_context["price_action_context"]["method"] == "ohlcv_price_action_proxies_v1"
    assert signal_context["skill_context"]["catalog_version"] == "skills_v1"
    assert signal_context["skill_context"]["routing_mode"] == "deterministic_v1"
    assert signal_context["decision_context"]["skill_context"]["primary_skill_code"] in {
        "analyze_ticker_post_news",
        "evaluate_daily_breakout",
        "evaluate_support_reclaim_reversal",
        "detect_risk_off_conditions",
        None,
    }
    assert isinstance(signal_context["decision_context"]["price_action_context"], dict)
    assert signal_context["timing_profile"]["version"] == "ticker_analysis_timing_v1"
    assert signal_context["timing_profile"]["total_ms"] >= 0
    assert isinstance(signal_context["timing_profile"]["stages_ms"], dict)
    assert "signal_analysis" in signal_context["timing_profile"]["stages_ms"]
    assert "decision_context" in signal_context["timing_profile"]["stages_ms"]
    assert signal_context["decision_context"]["timing_profile"]["version"] == "decision_context_timing_v1"


def test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_market_data = learning_api.orchestrator_service.market_data_service
    original_idle_enabled = learning_api.orchestrator_service.settings.idle_research_enabled
    analyze_calls = {"count": 0}
    market_data_calls = {"count": 0}

    def counting_analyze_ticker(ticker: str) -> dict:
        analyze_calls["count"] += 1
        return {
            "ticker": ticker,
            "quant_summary": {
                "ticker": ticker,
                "price": 104.0,
                "sma_20": 103.0,
                "sma_50": 100.0,
                "sma_200": 95.0,
                "rsi_14": 62.0,
                "relative_volume": 1.8,
                "atr_14": 2.0,
                "week_performance": 0.04,
                "month_performance": 0.1,
            },
            "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
            "combined_score": 0.86,
            "decision": "paper_enter",
            "entry_price": 104.0,
            "stop_price": 101.0,
            "target_price": 110.0,
            "risk_reward": 2.0,
            "decision_confidence": 0.86,
            "alpha_gap_pct": 4.2,
            "rationale": f"Stable deterministic signal for {ticker}.",
        }

    class _CountingStableMarketDataService(_StableMarketDataService):
        def get_snapshot(self, ticker: str) -> MarketSnapshot:
            market_data_calls["count"] += 1
            return super().get_snapshot(ticker)

    learning_api.orchestrator_service.signal_service.analyze_ticker = counting_analyze_ticker
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    learning_api.orchestrator_service.market_data_service = _CountingStableMarketDataService()
    learning_api.orchestrator_service.settings.idle_research_enabled = False
    try:
        first = client.post("/api/v1/orchestrator/do")
        assert first.status_code == 200
        first_payload = first.json()
        first_call_count = analyze_calls["count"]
        first_market_data_call_count = market_data_calls["count"]
        assert first_call_count >= 6
        assert first_payload["generated_analyses"] >= 6
        assert first_market_data_call_count >= 6

        watchlists = client.get("/api/v1/watchlists")
        assert watchlists.status_code == 200
        watchlist_items = [
            item
            for watchlist in watchlists.json()
            for item in watchlist["items"]
        ]
        assert len(watchlist_items) >= 6
        assert all(
            item["key_metrics"]["reanalysis_runtime"]["version"] == "watchlist_reanalysis_runtime_v1"
            for item in watchlist_items
        )
        assert all(
            item["key_metrics"]["reanalysis_runtime"]["next_reanalysis_at"]
            for item in watchlist_items
        )

        second = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_data_service = original_market_data
        learning_api.orchestrator_service.settings.idle_research_enabled = original_idle_enabled

    assert second.status_code == 200
    second_payload = second.json()
    assert analyze_calls["count"] == first_call_count
    assert market_data_calls["count"] == first_market_data_call_count
    assert second_payload["generated_analyses"] == 0
    assert second_payload["metrics"]["generated_signals"] == 0
    assert second_payload["metrics"]["deferred_reanalysis_entries"] >= 5


def test_orchestrator_do_defers_first_review_while_market_is_closed(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_scan_when_closed = learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed
    original_discovery_when_closed = learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed

    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: (_ for _ in ()).throw(
        AssertionError("analyze_ticker should not run while first reviews are deferred in a closed market")
    )
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: (_ for _ in ()).throw(
        AssertionError("discovery should not run while the market is closed")
    )
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=False,
        session_label="weekend",
    )
    learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed = False
    learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed = False
    try:
        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed = original_scan_when_closed
        learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed = original_discovery_when_closed

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_analyses"] == 0
    assert payload["metrics"]["generated_signals"] == 0
    assert payload["metrics"]["deferred_market_closed_entries"] >= 6
    assert payload["metrics"]["discovered_items"] == 0


def test_orchestrator_do_continues_closed_market_research_but_defers_entries(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_scan_when_closed = learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed
    original_discovery_when_closed = learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed
    original_entry_when_closed = learning_api.orchestrator_service.settings.paper_entry_when_market_closed

    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
        },
        "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84, "visual_score": 0.8},
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "target_price": 112.0,
        "risk_reward": 2.4,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": f"Closed-market research signal for {ticker}.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 2,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=False,
        session_label="weekend",
    )
    learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed = True
    learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed = True
    learning_api.orchestrator_service.settings.paper_entry_when_market_closed = False
    try:
        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.settings.orchestrator_scan_when_market_closed = original_scan_when_closed
        learning_api.orchestrator_service.settings.opportunity_discovery_run_when_market_closed = original_discovery_when_closed
        learning_api.orchestrator_service.settings.paper_entry_when_market_closed = original_entry_when_closed

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_analyses"] >= 6
    assert payload["metrics"]["generated_signals"] >= 6
    assert payload["opened_positions"] == 0
    assert payload["metrics"]["market_closed_entry_deferred_entries"] >= 6
    assert payload["metrics"]["deferred_market_closed_entries"] == 0
    assert all(candidate["decision"] == "watch" for candidate in payload["candidates"])

    positions = client.get("/api/v1/positions")
    assert positions.status_code == 200
    assert positions.json() == []

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    assert any(
        signal["signal_context"].get("market_closed_execution_policy", {}).get("reason") == "market_closed_execution_policy"
        for signal in signals.json()
    )


def test_reanalysis_policy_uses_live_snapshot_for_technical_state(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_market_data = learning_api.orchestrator_service.market_data_service
    original_idle_enabled = learning_api.orchestrator_service.settings.idle_research_enabled
    analyze_calls = {"count": 0}

    def fused_style_signal(ticker: str) -> dict:
        analyze_calls["count"] += 1
        return {
            "ticker": ticker,
            "quant_summary": {
                "ticker": ticker,
                "trend": "uptrend",
                "setup": "breakout",
                "momentum_pct_20": 8.0,
                "relative_volume": 1.7,
                "atr_14": 2.0,
            },
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.75},
            "combined_score": 0.82,
            "decision": "watch",
            "entry_price": 104.0,
            "stop_price": 101.0,
            "target_price": 110.0,
            "risk_reward": 2.0,
            "decision_confidence": 0.82,
            "alpha_gap_pct": 4.2,
            "rationale": f"Stable fused-style signal for {ticker}.",
        }

    learning_api.orchestrator_service.signal_service.analyze_ticker = fused_style_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    learning_api.orchestrator_service.market_data_service = _StableMarketDataService()
    learning_api.orchestrator_service.settings.idle_research_enabled = False
    try:
        first = client.post("/api/v1/orchestrator/do")
        assert first.status_code == 200
        first_payload = first.json()
        first_call_count = analyze_calls["count"]
        assert first_call_count >= 6
        assert first_payload["generated_analyses"] >= 6

        second = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_data_service = original_market_data
        learning_api.orchestrator_service.settings.idle_research_enabled = original_idle_enabled

    assert second.status_code == 200
    second_payload = second.json()
    assert analyze_calls["count"] == first_call_count
    assert second_payload["generated_analyses"] == 0
    assert second_payload["metrics"]["deferred_reanalysis_entries"] >= 5


def test_assess_reanalysis_need_reschedules_expired_watchlist_item_without_trigger(
    client: TestClient,
    session: Session,
) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    orchestrator = learning_api.orchestrator_service
    original_analyze_ticker = orchestrator.signal_service.analyze_ticker
    original_discovery = orchestrator.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = orchestrator.market_hours_service
    original_market_data = orchestrator.market_data_service
    original_news_service = orchestrator.agent_tool_gateway_service.news_service
    original_calendar_service = orchestrator.agent_tool_gateway_service.calendar_service
    original_idle_enabled = orchestrator.settings.idle_research_enabled

    orchestrator.signal_service.analyze_ticker = lambda ticker: {
        "ticker": ticker,
        "quant_summary": {
            "ticker": ticker,
            "price": 104.0,
            "sma_20": 103.0,
            "sma_50": 100.0,
            "sma_200": 95.0,
            "rsi_14": 62.0,
            "relative_volume": 1.8,
            "atr_14": 2.0,
            "week_performance": 0.04,
            "month_performance": 0.1,
        },
        "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
        "combined_score": 0.86,
        "decision": "watch",
        "entry_price": 104.0,
        "stop_price": 101.0,
        "target_price": 110.0,
        "risk_reward": 2.0,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": f"Stable deterministic signal for {ticker}.",
    }
    orchestrator.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    orchestrator.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    orchestrator.market_data_service = _StableMarketDataService()
    orchestrator.agent_tool_gateway_service.news_service = _QuietNewsService()
    orchestrator.agent_tool_gateway_service.calendar_service = _QuietCalendarService()
    orchestrator.settings.idle_research_enabled = False
    try:
        first = client.post("/api/v1/orchestrator/do")
        assert first.status_code == 200

        item = session.scalars(select(WatchlistItem).order_by(WatchlistItem.id.asc())).first()
        assert item is not None
        runtime_state = dict(item.key_metrics["reanalysis_runtime"])
        expired_due_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        runtime_state["next_reanalysis_at"] = expired_due_at
        item.key_metrics = {
            **dict(item.key_metrics or {}),
            "reanalysis_runtime": runtime_state,
        }
        session.add(item)
        session.commit()
        session.refresh(item)

        market_state_snapshot = orchestrator.market_state_service.capture_snapshot(
            session,
            trigger="test_reanalysis_runtime",
            pdca_phase="do",
            source_context={"test_case": "expired_watchlist_item"},
        )
        result = orchestrator._assess_reanalysis_need(
            session,
            item=item,
            market_state_snapshot=market_state_snapshot,
        )
        assert result["due"] is False
        assert result["reason"] == "awaiting_reanalysis_trigger"
        assert result["runtime_updated"] is True

        session.add(item)
        session.commit()
        session.refresh(item)
    finally:
        orchestrator.signal_service.analyze_ticker = original_analyze_ticker
        orchestrator.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        orchestrator.market_hours_service = original_market_hours
        orchestrator.market_data_service = original_market_data
        orchestrator.agent_tool_gateway_service.news_service = original_news_service
        orchestrator.agent_tool_gateway_service.calendar_service = original_calendar_service
        orchestrator.settings.idle_research_enabled = original_idle_enabled

    updated_runtime_state = item.key_metrics["reanalysis_runtime"]
    assert updated_runtime_state["last_gate_reason"] == "awaiting_reanalysis_trigger"
    assert updated_runtime_state["next_reanalysis_at"] != expired_due_at
    assert (
        orchestrator._parse_iso_datetime(updated_runtime_state["next_reanalysis_at"])
        > datetime.now(timezone.utc)
    )


def test_orchestrator_do_runtime_budget_defers_scheduled_reanalysis_backlog(
    client: TestClient,
    session: Session,
) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    orchestrator = learning_api.orchestrator_service
    original_analyze_ticker = orchestrator.signal_service.analyze_ticker
    original_discovery = orchestrator.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = orchestrator.market_hours_service
    original_market_data = orchestrator.market_data_service
    original_news_service = orchestrator.agent_tool_gateway_service.news_service
    original_calendar_service = orchestrator.agent_tool_gateway_service.calendar_service
    original_idle_enabled = orchestrator.settings.idle_research_enabled
    original_max_checks = orchestrator.settings.orchestrator_scheduled_reanalysis_max_checks_per_cycle
    original_budget_seconds = orchestrator.settings.orchestrator_scheduled_reanalysis_budget_seconds
    original_deferral_seconds = orchestrator.settings.orchestrator_scheduled_reanalysis_budget_deferral_seconds
    original_spacing_seconds = orchestrator.settings.orchestrator_scheduled_reanalysis_budget_spacing_seconds
    analyze_calls = {"count": 0}
    market_data_calls = {"count": 0}

    def deterministic_signal(ticker: str) -> dict:
        analyze_calls["count"] += 1
        return {
            "ticker": ticker,
            "quant_summary": {
                "ticker": ticker,
                "price": 104.0,
                "sma_20": 103.0,
                "sma_50": 100.0,
                "sma_200": 95.0,
                "rsi_14": 62.0,
                "relative_volume": 1.8,
                "atr_14": 2.0,
                "week_performance": 0.04,
                "month_performance": 0.1,
            },
            "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
            "combined_score": 0.86,
            "decision": "watch",
            "entry_price": 104.0,
            "stop_price": 101.0,
            "target_price": 110.0,
            "risk_reward": 2.0,
            "decision_confidence": 0.86,
            "alpha_gap_pct": 4.2,
            "rationale": f"Stable deterministic signal for {ticker}.",
        }

    class _CountingStableMarketDataService(_StableMarketDataService):
        def get_snapshot(self, ticker: str) -> MarketSnapshot:
            market_data_calls["count"] += 1
            return super().get_snapshot(ticker)

    orchestrator.signal_service.analyze_ticker = deterministic_signal
    orchestrator.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    orchestrator.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    orchestrator.market_data_service = _CountingStableMarketDataService()
    orchestrator.agent_tool_gateway_service.news_service = _QuietNewsService()
    orchestrator.agent_tool_gateway_service.calendar_service = _QuietCalendarService()
    orchestrator.settings.idle_research_enabled = False
    orchestrator.settings.orchestrator_scheduled_reanalysis_max_checks_per_cycle = 1
    orchestrator.settings.orchestrator_scheduled_reanalysis_budget_seconds = 300
    orchestrator.settings.orchestrator_scheduled_reanalysis_budget_deferral_seconds = 60
    orchestrator.settings.orchestrator_scheduled_reanalysis_budget_spacing_seconds = 5
    try:
        first = client.post("/api/v1/orchestrator/do")
        assert first.status_code == 200
        first_call_count = analyze_calls["count"]
        first_market_data_call_count = market_data_calls["count"]
        assert first_call_count >= 6

        items = list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.id.asc())).all())
        assert len(items) >= 6
        expired_due_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for item in items:
            runtime_state = dict(item.key_metrics["reanalysis_runtime"])
            runtime_state["next_reanalysis_at"] = expired_due_at
            item.key_metrics = {
                **dict(item.key_metrics or {}),
                "reanalysis_runtime": runtime_state,
            }
            session.add(item)
        session.commit()

        second = client.post("/api/v1/orchestrator/do")
        assert second.status_code == 200
        second_payload = second.json()
        refreshed_items = list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.id.asc())).all())
    finally:
        orchestrator.signal_service.analyze_ticker = original_analyze_ticker
        orchestrator.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        orchestrator.market_hours_service = original_market_hours
        orchestrator.market_data_service = original_market_data
        orchestrator.agent_tool_gateway_service.news_service = original_news_service
        orchestrator.agent_tool_gateway_service.calendar_service = original_calendar_service
        orchestrator.settings.idle_research_enabled = original_idle_enabled
        orchestrator.settings.orchestrator_scheduled_reanalysis_max_checks_per_cycle = original_max_checks
        orchestrator.settings.orchestrator_scheduled_reanalysis_budget_seconds = original_budget_seconds
        orchestrator.settings.orchestrator_scheduled_reanalysis_budget_deferral_seconds = original_deferral_seconds
        orchestrator.settings.orchestrator_scheduled_reanalysis_budget_spacing_seconds = original_spacing_seconds

    assert second_payload["generated_analyses"] == 0
    assert analyze_calls["count"] == first_call_count
    assert market_data_calls["count"] - first_market_data_call_count == 1
    assert second_payload["metrics"]["scheduled_reanalysis_checks_started"] == 1
    active_items = second_payload["metrics"]["watchlist_items"]
    assert second_payload["metrics"]["runtime_budget_deferred_entries"] >= active_items - 1
    assert second_payload["metrics"]["scheduled_reanalysis_checks_deferred"] >= active_items - 1
    deferred_runtime_states = [
        dict(item.key_metrics["reanalysis_runtime"])
        for item in refreshed_items
        if item.key_metrics.get("reanalysis_runtime", {}).get("last_gate_reason") == "runtime_budget_deferred"
    ]
    assert len(deferred_runtime_states) >= active_items - 1
    assert all(
        orchestrator._parse_iso_datetime(runtime_state["next_reanalysis_at"]) > datetime.now(timezone.utc)
        for runtime_state in deferred_runtime_states
    )


def test_schedule_watchlist_reanalysis_applies_deterministic_stagger(
    client: TestClient,
    session: Session,
) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    orchestrator = learning_api.orchestrator_service
    original_market_hours = orchestrator.market_hours_service
    original_jitter = orchestrator.settings.orchestrator_scheduled_reanalysis_jitter_seconds
    orchestrator.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    orchestrator.settings.orchestrator_scheduled_reanalysis_jitter_seconds = 120
    try:
        items = list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.id.asc())).all())
        assert len(items) >= 2
        evaluated_at = datetime(2026, 4, 20, 11, 45, 0, tzinfo=timezone.utc)
        policy = {
            "policy_version": "event_driven_v1",
            "price_move_threshold_pct": 0.03,
            "market_state_regime": "trend_up",
        }

        first_state = orchestrator._schedule_watchlist_reanalysis(
            items[0],
            policy=policy,
            scheduled_reason="awaiting_reanalysis_trigger",
            evaluated_at=evaluated_at,
        )
        second_state = orchestrator._schedule_watchlist_reanalysis(
            items[1],
            policy=policy,
            scheduled_reason="awaiting_reanalysis_trigger",
            evaluated_at=evaluated_at,
        )
        repeated_first_state = orchestrator._schedule_watchlist_reanalysis(
            items[0],
            policy=policy,
            scheduled_reason="awaiting_reanalysis_trigger",
            evaluated_at=evaluated_at,
        )
    finally:
        orchestrator.market_hours_service = original_market_hours
        orchestrator.settings.orchestrator_scheduled_reanalysis_jitter_seconds = original_jitter

    assert first_state["base_interval_seconds"] == orchestrator.REANALYSIS_OPEN_MEDIUM_INTERVAL_SECONDS
    assert second_state["base_interval_seconds"] == orchestrator.REANALYSIS_OPEN_MEDIUM_INTERVAL_SECONDS
    assert first_state["schedule_jitter_seconds"] != second_state["schedule_jitter_seconds"]
    assert first_state["next_reanalysis_at"] != second_state["next_reanalysis_at"]
    assert repeated_first_state["schedule_jitter_seconds"] == first_state["schedule_jitter_seconds"]
    assert repeated_first_state["next_reanalysis_at"] == first_state["next_reanalysis_at"]


def test_orchestrator_do_opens_idle_market_scouting_tasks_when_watchlist_is_fully_deferred(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    original_assess_reanalysis = learning_api.orchestrator_service._assess_reanalysis_need
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_candidate_universe = learning_api.orchestrator_service.opportunity_discovery_service.get_candidate_universe
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_market_data = learning_api.orchestrator_service.market_data_service
    original_news_service = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_calendar_service = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_idle_enabled = learning_api.orchestrator_service.settings.idle_research_enabled
    original_idle_per_cycle = learning_api.orchestrator_service.settings.idle_research_per_cycle
    original_idle_scan_limit = learning_api.orchestrator_service.settings.idle_research_scan_limit
    original_idle_max_open_tasks = learning_api.orchestrator_service.settings.idle_research_max_open_tasks
    analyzed_tickers: list[str] = []
    candidate_scores = {
        "PLTR": 0.66,
        "SNOW": 0.62,
        "ROKU": 0.51,
    }

    learning_api.orchestrator_service._assess_reanalysis_need = lambda session, item, market_state_snapshot: {
        "due": False,
        "reason": "awaiting_reanalysis_trigger",
        "details": "No explicit reanalysis trigger fired.",
    }
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: (
        analyzed_tickers.append(ticker)
        or {
            "ticker": ticker,
            "quant_summary": {
                "ticker": ticker,
                "setup": "breakout",
                "relative_volume": 1.6,
                "atr_14": 2.0,
            },
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.7},
            "combined_score": candidate_scores[ticker],
            "decision": "watch",
            "entry_price": 104.0,
            "stop_price": 100.0,
            "target_price": 112.0,
            "risk_reward": 2.0,
            "decision_confidence": candidate_scores[ticker],
            "alpha_gap_pct": 2.8,
            "rationale": f"Idle scouting signal for {ticker}.",
        }
    )
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 2,
        "universe_size": 3,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.get_candidate_universe = lambda: {
        "universe": ["PLTR", "SNOW", "ROKU"],
        "universe_source": "test_scanner",
        "scanner_types_used": ["MOST_ACTIVE"],
    }
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    learning_api.orchestrator_service.market_data_service = _StableMarketDataService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = _IdleResearchNewsService()
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = NearTermCalendarService()
    learning_api.orchestrator_service.settings.idle_research_enabled = True
    learning_api.orchestrator_service.settings.idle_research_per_cycle = 2
    learning_api.orchestrator_service.settings.idle_research_scan_limit = 3
    learning_api.orchestrator_service.settings.idle_research_max_open_tasks = 2
    try:
        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service._assess_reanalysis_need = original_assess_reanalysis
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.opportunity_discovery_service.get_candidate_universe = original_candidate_universe
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_data_service = original_market_data
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_news_service
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_calendar_service
        learning_api.orchestrator_service.settings.idle_research_enabled = original_idle_enabled
        learning_api.orchestrator_service.settings.idle_research_per_cycle = original_idle_per_cycle
        learning_api.orchestrator_service.settings.idle_research_scan_limit = original_idle_scan_limit
        learning_api.orchestrator_service.settings.idle_research_max_open_tasks = original_idle_max_open_tasks

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_analyses"] == 0
    assert payload["metrics"]["deferred_reanalysis_entries"] >= 5
    assert payload["metrics"]["idle_research_triggered"] is True
    assert payload["metrics"]["idle_research_candidates_reviewed"] == 3
    assert payload["metrics"]["idle_research_tasks_opened"] == 2
    assert payload["metrics"]["idle_research_focus_tickers"] == ["PLTR", "SNOW"]
    assert analyzed_tickers == ["PLTR", "SNOW", "ROKU"]

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    scouting_titles = [
        task["title"]
        for task in research_tasks.json()
        if task["task_type"] == "market_scouting"
    ]
    assert "Scout ticker PLTR for watchlist expansion" in scouting_titles
    assert "Scout ticker SNOW for watchlist expansion" in scouting_titles


def test_orchestrator_do_records_macro_research_signals_and_tasks(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    orchestrator = learning_api.orchestrator_service
    original_assess_reanalysis = orchestrator._assess_reanalysis_need
    original_discovery = orchestrator.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = orchestrator.market_hours_service
    original_news_service = orchestrator.agent_tool_gateway_service.news_service
    original_calendar_service = orchestrator.agent_tool_gateway_service.calendar_service
    original_web_service = orchestrator.agent_tool_gateway_service.web_research_service
    original_macro_synthesis = orchestrator.trading_agent_service.synthesize_macro_research
    original_idle_enabled = orchestrator.settings.idle_research_enabled
    original_macro_enabled = orchestrator.settings.macro_research_enabled
    original_macro_per_cycle = orchestrator.settings.macro_research_per_cycle
    original_macro_task_limit = orchestrator.settings.macro_research_max_open_tasks
    original_macro_topics = orchestrator.__class__.MACRO_RESEARCH_TOPICS

    orchestrator.__class__.MACRO_RESEARCH_TOPICS = (original_macro_topics[0],)
    orchestrator._assess_reanalysis_need = lambda session, item, market_state_snapshot: {
        "due": False,
        "reason": "awaiting_reanalysis_trigger",
        "details": "No explicit reanalysis trigger fired.",
    }
    orchestrator.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 2,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    orchestrator.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    orchestrator.agent_tool_gateway_service.news_service = _MacroResearchNewsService()
    orchestrator.agent_tool_gateway_service.calendar_service = _MacroCalendarService()
    orchestrator.agent_tool_gateway_service.web_research_service = _MacroResearchWebService()
    orchestrator.trading_agent_service.synthesize_macro_research = lambda **kwargs: {
        "summary": "Fed repricing could move duration and growth proxies higher if the path turns softer.",
        "regime": "macro_uncertainty",
        "relevance": "cross_asset",
        "timeframe": "1D-1M",
        "scenario": "Fed rate decision with softer inflation path",
        "importance": 0.82,
        "impact_hypothesis": "Softer rates expectations would likely help QQQ and TLT while weakening the dollar.",
        "affected_assets": ["QQQ", "TLT", "UUP"],
        "asset_impacts": [
            {"ticker": "QQQ", "bias": "bullish", "reason": "Growth duration benefits from lower yields."},
            {"ticker": "TLT", "bias": "bullish", "reason": "Duration should benefit if yields fall."},
            {"ticker": "UUP", "bias": "bearish", "reason": "A softer Fed path can weaken the dollar."},
        ],
        "strategy_ideas": [
            "wait for confirmation in QQQ and TLT after the event instead of front-running the release",
            "track a relative rotation basket long QQQ versus UUP if yields reprice lower",
        ],
        "risk_flags": ["hawkish surprise would invalidate the thesis quickly"],
        "evidence_points": ["Fed rate decision", "Macro headline"],
        "analysis_mode": "ai",
        "provider": "stub_ai",
        "model": "stub_macro_model",
    }
    orchestrator.settings.idle_research_enabled = False
    orchestrator.settings.macro_research_enabled = True
    orchestrator.settings.macro_research_per_cycle = 1
    orchestrator.settings.macro_research_max_open_tasks = 2
    try:
        response = client.post("/api/v1/orchestrator/do")
    finally:
        orchestrator.__class__.MACRO_RESEARCH_TOPICS = original_macro_topics
        orchestrator._assess_reanalysis_need = original_assess_reanalysis
        orchestrator.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        orchestrator.market_hours_service = original_market_hours
        orchestrator.agent_tool_gateway_service.news_service = original_news_service
        orchestrator.agent_tool_gateway_service.calendar_service = original_calendar_service
        orchestrator.agent_tool_gateway_service.web_research_service = original_web_service
        orchestrator.trading_agent_service.synthesize_macro_research = original_macro_synthesis
        orchestrator.settings.idle_research_enabled = original_idle_enabled
        orchestrator.settings.macro_research_enabled = original_macro_enabled
        orchestrator.settings.macro_research_per_cycle = original_macro_per_cycle
        orchestrator.settings.macro_research_max_open_tasks = original_macro_task_limit

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_analyses"] == 0
    assert payload["metrics"]["macro_research_triggered"] is True
    assert payload["metrics"]["macro_research_topics_reviewed"] == 1
    assert payload["metrics"]["macro_research_signals_recorded"] == 1
    assert payload["metrics"]["macro_research_tasks_opened"] == 1
    assert payload["metrics"]["macro_research_watchlists_created"] == 1
    assert payload["metrics"]["macro_research_watchlists_refreshed"] == 1
    assert payload["metrics"]["macro_research_watchlist_codes"] == ["macro_us_rates_inflation"]
    assert payload["metrics"]["macro_research_focus_themes"] == ["us_rates_inflation"]
    assert "QQQ" in payload["metrics"]["macro_research_focus_assets"]

    macro_signals = client.get("/api/v1/macro/signals")
    assert macro_signals.status_code == 200
    created_signal = next(
        signal
        for signal in macro_signals.json()
        if signal["key"].startswith("auto_macro:us_rates_inflation:")
    )
    assert created_signal["meta"]["source"] == "macro_research_lane"
    assert created_signal["meta"]["tickers"] == ["QQQ", "TLT", "UUP"]
    assert created_signal["meta"]["evidence"]["strategy_ideas"]

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    macro_titles = [
        task["title"]
        for task in research_tasks.json()
        if task["task_type"] == "macro_strategy_research"
    ]
    assert "Exploit macro theme us_rates_inflation" in macro_titles
    macro_task = next(
        task
        for task in research_tasks.json()
        if task["task_type"] == "macro_strategy_research"
    )
    assert macro_task["scope"]["linked_watchlist_code"] == "macro_us_rates_inflation"

    watchlists = client.get("/api/v1/watchlists")
    assert watchlists.status_code == 200
    macro_watchlist = next(
        watchlist
        for watchlist in watchlists.json()
        if watchlist["code"] == "macro_us_rates_inflation"
    )
    assert [item["ticker"] for item in macro_watchlist["items"]] == ["QQQ", "TLT", "UUP"]
    assert all(item["key_metrics"]["source"] == "macro_research_lane" for item in macro_watchlist["items"])


def test_orchestrator_do_deduplicates_macro_research_when_evidence_does_not_change(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    orchestrator = learning_api.orchestrator_service
    original_assess_reanalysis = orchestrator._assess_reanalysis_need
    original_discovery = orchestrator.opportunity_discovery_service.refresh_active_watchlists
    original_market_hours = orchestrator.market_hours_service
    original_news_service = orchestrator.agent_tool_gateway_service.news_service
    original_calendar_service = orchestrator.agent_tool_gateway_service.calendar_service
    original_web_service = orchestrator.agent_tool_gateway_service.web_research_service
    original_macro_synthesis = orchestrator.trading_agent_service.synthesize_macro_research
    original_idle_enabled = orchestrator.settings.idle_research_enabled
    original_macro_enabled = orchestrator.settings.macro_research_enabled
    original_macro_per_cycle = orchestrator.settings.macro_research_per_cycle
    original_macro_task_limit = orchestrator.settings.macro_research_max_open_tasks
    original_macro_topics = orchestrator.__class__.MACRO_RESEARCH_TOPICS

    orchestrator.__class__.MACRO_RESEARCH_TOPICS = (original_macro_topics[0],)
    orchestrator._assess_reanalysis_need = lambda session, item, market_state_snapshot: {
        "due": False,
        "reason": "awaiting_reanalysis_trigger",
        "details": "No explicit reanalysis trigger fired.",
    }
    orchestrator.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 2,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    orchestrator.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    orchestrator.agent_tool_gateway_service.news_service = _MacroResearchNewsService()
    orchestrator.agent_tool_gateway_service.calendar_service = _MacroCalendarService()
    orchestrator.agent_tool_gateway_service.web_research_service = _MacroResearchWebService()
    orchestrator.trading_agent_service.synthesize_macro_research = lambda **kwargs: {
        "summary": "Fed repricing could move duration and growth proxies higher if the path turns softer.",
        "regime": "macro_uncertainty",
        "relevance": "cross_asset",
        "timeframe": "1D-1M",
        "scenario": "Fed rate decision with softer inflation path",
        "importance": 0.82,
        "impact_hypothesis": "Softer rates expectations would likely help QQQ and TLT while weakening the dollar.",
        "affected_assets": ["QQQ", "TLT", "UUP"],
        "asset_impacts": [{"ticker": "QQQ", "bias": "bullish", "reason": "Growth duration benefits from lower yields."}],
        "strategy_ideas": ["wait for confirmation in QQQ and TLT after the event instead of front-running the release"],
        "risk_flags": ["hawkish surprise would invalidate the thesis quickly"],
        "evidence_points": ["Fed rate decision", "Macro headline"],
        "analysis_mode": "ai",
        "provider": "stub_ai",
        "model": "stub_macro_model",
    }
    orchestrator.settings.idle_research_enabled = False
    orchestrator.settings.macro_research_enabled = True
    orchestrator.settings.macro_research_per_cycle = 1
    orchestrator.settings.macro_research_max_open_tasks = 2
    try:
        first = client.post("/api/v1/orchestrator/do")
        second = client.post("/api/v1/orchestrator/do")
    finally:
        orchestrator.__class__.MACRO_RESEARCH_TOPICS = original_macro_topics
        orchestrator._assess_reanalysis_need = original_assess_reanalysis
        orchestrator.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        orchestrator.market_hours_service = original_market_hours
        orchestrator.agent_tool_gateway_service.news_service = original_news_service
        orchestrator.agent_tool_gateway_service.calendar_service = original_calendar_service
        orchestrator.agent_tool_gateway_service.web_research_service = original_web_service
        orchestrator.trading_agent_service.synthesize_macro_research = original_macro_synthesis
        orchestrator.settings.idle_research_enabled = original_idle_enabled
        orchestrator.settings.macro_research_enabled = original_macro_enabled
        orchestrator.settings.macro_research_per_cycle = original_macro_per_cycle
        orchestrator.settings.macro_research_max_open_tasks = original_macro_task_limit

    assert first.status_code == 200
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["metrics"]["macro_research_triggered"] is True
    assert second_payload["metrics"]["macro_research_topics_reviewed"] == 1
    assert second_payload["metrics"]["macro_research_signals_recorded"] == 0
    assert second_payload["metrics"]["macro_research_tasks_opened"] == 0
    assert second_payload["metrics"]["macro_research_watchlists_created"] == 0
    assert second_payload["metrics"]["macro_research_watchlists_refreshed"] == 0

    macro_signals = client.get("/api/v1/macro/signals")
    assert macro_signals.status_code == 200
    auto_signals = [
        signal
        for signal in macro_signals.json()
        if signal["key"].startswith("auto_macro:us_rates_inflation:")
    ]
    assert len(auto_signals) == 1

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    macro_tasks = [
        task
        for task in research_tasks.json()
        if task["task_type"] == "macro_strategy_research"
    ]
    assert len(macro_tasks) == 1

    watchlists = client.get("/api/v1/watchlists")
    assert watchlists.status_code == 200
    macro_watchlists = [
        watchlist
        for watchlist in watchlists.json()
        if watchlist["code"] == "macro_us_rates_inflation"
    ]
    assert len(macro_watchlists) == 1
    assert [item["ticker"] for item in macro_watchlists[0]["items"]] == ["QQQ", "TLT", "UUP"]


def test_orchestrator_persists_market_state_snapshots_and_exposes_latest(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    plan = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-18", "market_context": {}})
    assert plan.status_code == 201
    plan_payload = plan.json()
    assert plan_payload["market_state_snapshot"]["pdca_phase"] == "plan"
    assert plan_payload["market_state_snapshot"]["snapshot_payload"]["market_regime"]["label"]

    latest_after_plan = client.get("/api/v1/macro/state-snapshots/latest")
    assert latest_after_plan.status_code == 200
    assert latest_after_plan.json()["id"] == plan_payload["market_state_snapshot"]["id"]

    do_phase = client.post("/api/v1/orchestrator/do")
    assert do_phase.status_code == 200
    do_payload = do_phase.json()
    assert do_payload["market_state_snapshot"]["pdca_phase"] == "do"
    assert do_payload["market_state_snapshot"]["snapshot_payload"]["market_state_snapshot"]["portfolio_state"]["benchmark_ticker"] == "SPY"

    latest_do = client.get("/api/v1/macro/state-snapshots/latest?pdca_phase=do")
    assert latest_do.status_code == 200
    assert latest_do.json()["id"] == do_payload["market_state_snapshot"]["id"]


def test_orchestrator_do_keeps_entries_on_watchlist_when_calendar_risk_is_near(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
        },
        "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 110.0,
        "risk_reward": 2.5,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": "Deterministic test signal.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = NearTermCalendarService()
    learning_api.orchestrator_service.market_hours_service = _FixedMarketHoursService(
        is_regular_session_open=True,
        session_label="regular",
    )
    try:
        strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "calendar_guard_strategy",
                "name": "Calendar Guard Strategy",
                "description": "Strategy used to test calendar-aware entry blocking.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Avoid entering right before earnings.",
                    "general_rules": {},
                    "parameters": {},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "calendar_guard_watchlist",
                "name": "Calendar Guard Watchlist",
                "strategy_id": strategy["id"],
                "hypothesis": "Only enter when the calendar is clear.",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Calendar guard candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_calendar
        learning_api.orchestrator_service.market_hours_service = original_market_hours

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened_positions"] == 0
    assert payload["metrics"]["calendar_blocked_entries"] == 1
    assert payload["candidates"][0]["decision"] == "watch"

    positions = client.get("/api/v1/positions")
    assert positions.status_code == 200
    assert positions.json() == []

    journal = client.get("/api/v1/journal")
    assert journal.status_code == 200
    assert any(entry["decision"] == "skip_calendar_risk" for entry in journal.json())


def test_orchestrator_do_degrades_when_ai_is_unavailable(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_ai = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
            "trend": "uptrend",
            "setup": "breakout",
            "risk_reward": 2.5,
        },
        "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 110.0,
        "risk_reward": 2.5,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": "Deterministic signal before AI overlay.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }

    def failing_ai(*args, **kwargs):
        raise AIDecisionError("provider chain unavailable in test")

    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = failing_ai
    try:
        seeded = client.post("/api/v1/bootstrap/seed")
        assert seeded.status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_ai

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["ai_decisions"] == 0
    assert payload["metrics"]["ai_unavailable_entries"] >= 1
    assert payload["generated_analyses"] >= 6

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    degraded_signal = next(
        payload["signal_context"]
        for payload in signals.json()
        if isinstance(payload.get("signal_context", {}).get("ai_overlay"), dict)
        and payload["signal_context"]["ai_overlay"].get("status") == "unavailable"
    )
    assert degraded_signal["ai_overlay"]["status"] == "unavailable"
    assert degraded_signal["decision_trace"]["decision_source"] != "ai_overlay"


def test_orchestrator_do_blocks_entries_when_strategy_rules_conflict(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
            "trend": "uptrend",
            "setup": "breakout",
            "risk_reward": 2.5,
        },
        "visual_summary": {
            "setup_type": "breakout",
            "setup_quality": 0.84,
            "visual_score": 0.81,
        },
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 110.0,
        "risk_reward": 2.5,
        "decision_confidence": 0.86,
        "rationale": f"Deterministic conflict signal for {ticker}.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    try:
        strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "strategy_rule_guard",
                "name": "Strategy Rule Guard",
                "description": "Strategy used to test deterministic rule blocking.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Only pullback entries are valid.",
                    "general_rules": {"allowed_setups": ["pullback"]},
                    "parameters": {},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "strategy_rule_guard_watchlist",
                "name": "Strategy Rule Guard Watchlist",
                "strategy_id": strategy["id"],
                "hypothesis": "Only enter when the setup matches the strategy contract.",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Rule conflict candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened_positions"] == 0
    assert payload["metrics"]["decision_layer_blocked_entries"] == 1
    assert payload["candidates"][0]["decision"] == "watch"

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    signal_context = signals.json()[0]["signal_context"]
    assert signal_context["guard_results"]["blocked"] is True
    assert "allowed strategy setups" in signal_context["guard_results"]["reasons"][0]


def test_check_phase_generates_strategy_health_and_research_tasks(client: TestClient) -> None:
    assert client.post("/api/v1/bootstrap/seed").status_code == 201
    assert client.post("/api/v1/orchestrator/do").status_code == 200

    check = client.post("/api/v1/orchestrator/check")
    assert check.status_code == 200

    scorecards = client.get("/api/v1/strategy-health")
    assert scorecards.status_code == 200
    assert len(scorecards.json()) == 3
    assert any("fitness_score" in item for item in scorecards.json())

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    assert any(task["task_type"] == "improve_signal_flow" for task in research_tasks.json())

    plan = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-16", "market_context": {}})
    assert plan.status_code == 201
    assert plan.json()["work_queue"]["total_items"] >= 1

    pipelines = client.get("/api/v1/strategy-health/pipelines")
    assert pipelines.status_code == 200
    assert len(pipelines.json()) >= 1


def test_trade_review_supports_structured_learning_fields(client: TestClient, session: Session) -> None:
    strategy_payload = {
        "code": "review_strategy",
        "name": "Review Strategy",
        "description": "Strategy for review test.",
        "horizon": "days_weeks",
        "bias": "long",
        "status": "paper",
        "initial_version": {
            "hypothesis": "Test hypothesis.",
            "general_rules": {"price_above_sma50": True},
            "parameters": {"max_risk_per_trade_r": 1.0},
            "state": "approved",
            "is_baseline": True,
        },
    }
    strategy = client.post("/api/v1/strategies", json=strategy_payload)
    assert strategy.status_code == 201
    strategy_version_id = strategy.json()["current_version_id"]

    signal = client.post(
        "/api/v1/signals",
        json={
            "ticker": "NVDA",
            "strategy_id": strategy.json()["id"],
            "strategy_version_id": strategy_version_id,
            "signal_type": "breakout",
            "thesis": "Breakout signal",
            "entry_zone": {"price": 100},
            "stop_zone": {"price": 95},
            "target_zone": {"price": 110},
            "signal_context": {"source": "test"},
            "quality_score": 0.84,
        },
    )
    assert signal.status_code == 201

    position = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "signal_id": signal.json()["id"],
            "strategy_version_id": strategy_version_id,
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "size": 1,
            "thesis": "Breakout signal",
            "entry_context": {"source": "test"},
        },
    )
    assert position.status_code == 201

    closed = client.post(
        f"/api/v1/positions/{position.json()['id']}/close",
        json={
            "exit_price": 94,
            "exit_reason": "false_breakout",
            "max_drawdown_pct": -6.0,
            "max_runup_pct": 1.5,
            "close_context": {"failed_level": "entry_day_high"},
        },
    )
    assert closed.status_code == 200

    review = client.post(
        f"/api/v1/trade-reviews/positions/{position.json()['id']}",
        json={
            "outcome_label": "loss",
            "outcome": "loss",
            "cause_category": "false_breakout",
            "failure_mode": "false_breakout",
            "observations": {"volume_confirmation": False},
            "root_cause": "Breakout lacked confirmation.",
            "root_causes": ["Breakout lacked confirmation.", "Entry was too extended."],
            "lesson_learned": "Demand stronger confirmation.",
            "proposed_strategy_change": "Raise minimum relative volume.",
            "recommended_changes": ["Raise minimum relative volume.", "Avoid extended entries."],
            "confidence": 0.72,
            "review_priority": "high",
            "should_modify_strategy": True,
            "needs_strategy_update": True,
            "strategy_update_reason": "Repeated false breakout profile.",
        },
    )
    assert review.status_code == 201

    payload = review.json()
    assert payload["failure_mode"] == "false_breakout"
    assert payload["root_causes"] == ["Breakout lacked confirmation.", "Entry was too extended."]
    assert payload["recommended_changes"] == ["Raise minimum relative volume.", "Avoid extended entries."]
    assert payload["needs_strategy_update"] is True

    memory = client.get("/api/v1/memory")
    assert memory.status_code == 200
    memory_payload = memory.json()
    skill_candidates = [item for item in memory_payload if item["memory_type"] == "skill_candidate"]
    skill_gaps = [item for item in memory_payload if item["memory_type"] == "skill_gap"]
    assert len(skill_candidates) == 1
    assert skill_candidates[0]["meta"]["candidate_status"] == "draft"
    assert skill_candidates[0]["meta"]["source_type"] == "trade_review"
    assert {item["meta"]["gap_type"] for item in skill_gaps} == {
        "missing_catalog_skill",
        "missing_entry_skill_context",
    }

    journal = client.get("/api/v1/journal")
    assert journal.status_code == 200
    journal_entries = journal.json()
    assert any(entry["entry_type"] == "skill_candidate_proposed" for entry in journal_entries)
    assert sum(1 for entry in journal_entries if entry["entry_type"] == "skill_gap_detected") == 2

    skill_dashboard = client.get("/api/v1/skills/dashboard")
    assert skill_dashboard.status_code == 200
    dashboard_payload = skill_dashboard.json()
    assert {item["gap_type"] for item in dashboard_payload["gaps"]} == {
        "missing_catalog_skill",
        "missing_entry_skill_context",
    }

    claims = session.query(KnowledgeClaim).all()
    review_claim = next(item for item in claims if item.claim_type == "review_improvement")
    assert review_claim.linked_ticker == "NVDA"
    assert review_claim.status in {"supported", "validated"}
    evidence = session.query(KnowledgeClaimEvidence).filter(KnowledgeClaimEvidence.claim_id == review_claim.id).all()
    assert len(evidence) == 1
    assert evidence[0].source_type == "trade_review"


def test_check_phase_tracks_failure_patterns(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "pattern_strategy",
            "name": "Pattern Strategy",
            "description": "Failure pattern test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Pattern hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    strategy_version_id = strategy.json()["current_version_id"]

    for ticker in ["AAPL", "MSFT"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy.json()["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Breakout signal",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.8,
            },
        )
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal.json()["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        )
        client.post(
            f"/api/v1/positions/{position.json()['id']}/close",
            json={
                "exit_price": 94,
                "exit_reason": "false_breakout",
                "max_drawdown_pct": -6.0,
                "max_runup_pct": 1.2,
            },
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position.json()['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"volume_confirmation": False, "pnl_pct": -6.0},
                "root_cause": "Breakout lacked confirmation.",
                "root_causes": ["Breakout lacked confirmation."],
                "lesson_learned": "Demand stronger confirmation.",
                "recommended_changes": ["Raise minimum relative volume."],
                "proposed_strategy_change": "Raise minimum relative volume.",
                "should_modify_strategy": False,
                "needs_strategy_update": True,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    check = client.post("/api/v1/orchestrator/check")
    assert check.status_code == 200
    assert check.json()["metrics"]["failure_patterns_tracked"] >= 1

    patterns = client.get(f"/api/v1/failure-patterns/{strategy.json()['id']}")
    assert patterns.status_code == 200
    assert patterns.json()[0]["failure_mode"] == "false_breakout"
    assert patterns.json()[0]["occurrences"] >= 2


def test_act_phase_degrades_strategy_after_repeated_failures(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "degrade_strategy",
            "name": "Degrade Strategy",
            "description": "Degradation test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Weak setup.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    strategy_version_id = strategy["current_version_id"]

    for ticker in ["SHOP", "SQ"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Weak breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.2,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["degraded_strategies"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    degraded = next(item for item in strategies if item["id"] == strategy["id"])
    assert degraded["status"] == "degraded"
    degraded_version = next(version for version in degraded["versions"] if version["id"] == strategy_version_id)
    assert degraded_version["lifecycle_stage"] == "degraded"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"] is None
    assert any(version["id"] == strategy_version_id for version in payload["degraded_versions"])


def test_act_phase_forks_candidate_variant_from_failure_pattern(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "fork_strategy",
            "name": "Fork Strategy",
            "description": "Failure fork test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    original_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": original_version_id,
                "signal_type": "breakout",
                "thesis": "Fork breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": original_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["forked_variants"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    forked = next(item for item in strategies if item["id"] == strategy["id"])
    assert forked["current_version_id"] == original_version_id
    assert len(forked["versions"]) >= 2
    original_version = next(version for version in forked["versions"] if version["id"] == original_version_id)
    assert original_version["lifecycle_stage"] in {"active", "degraded"}
    candidate_versions = [version for version in forked["versions"] if version["id"] != original_version_id]
    assert candidate_versions[0]["state"] == "draft"
    assert candidate_versions[0]["lifecycle_stage"] == "candidate"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert any(version["id"] == candidate_versions[0]["id"] for version in payload["candidate_versions"])
    if forked["status"] == "degraded":
        assert payload["active_version"] is None
        assert any(version["id"] == original_version_id for version in payload["degraded_versions"])
    else:
        assert payload["active_version"]["id"] == original_version_id

    plan = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-16", "market_context": {}})
    assert plan.status_code == 201
    plan_payload = plan.json()
    queue_items = plan_payload["work_queue"]["items"]
    candidate_validation = next(
        item for item in queue_items if item["item_type"] == "degraded_candidate_validation"
    )
    assert candidate_validation["priority"] == "P3"
    assert candidate_validation["context"]["strategy_id"] == strategy["id"]
    assert plan_payload["market_context"]["degraded_candidate_backlog"] >= 1


def test_act_phase_archives_strategy_when_failures_persist_without_activity(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "archive_strategy",
            "name": "Archive Strategy",
            "description": "Archive test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "research",
            "initial_version": {
                "hypothesis": "Weak inactive setup.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    strategy_version_id = strategy["current_version_id"]

    for ticker in ["U", "SNAP", "PINS"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Weak inactive breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.1,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 93, "exit_reason": "false_breakout", "max_drawdown_pct": -7, "max_runup_pct": 0.5},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -7.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": True,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act1 = client.post("/api/v1/orchestrator/act")
    assert act1.status_code == 200
    act2 = client.post("/api/v1/orchestrator/act")
    assert act2.status_code == 200
    assert (
        act1.json()["metrics"]["archived_strategies"] >= 1
        or act2.json()["metrics"]["archived_strategies"] >= 1
    )

    strategies = client.get("/api/v1/strategies").json()
    archived = next(item for item in strategies if item["id"] == strategy["id"])
    assert archived["status"] == "archived"
    assert all(version["lifecycle_stage"] == "archived" for version in archived["versions"])

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"] is None
    assert len(payload["archived_versions"]) == payload["total_versions"]


def test_act_phase_promotes_candidate_variant_after_positive_results(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "promote_candidate_strategy",
            "name": "Promote Candidate Strategy",
            "description": "Candidate promotion test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["NET", "CRWD"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Candidate seed breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.2,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    fork_act = client.post("/api/v1/orchestrator/act")
    assert fork_act.status_code == 200
    assert fork_act.json()["metrics"]["forked_variants"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    strategy_after_fork = next(item for item in strategies if item["id"] == strategy["id"])
    candidate = next(version for version in strategy_after_fork["versions"] if version["id"] != base_version_id)

    for ticker in ["NOW", "SNOW", "PLTR", "AMD"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": candidate["id"],
                "signal_type": "candidate_breakout",
                "thesis": "Candidate breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.85,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": candidate["id"],
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
                "entry_context": {"execution_mode": "candidate_validation"},
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 105, "exit_reason": "trend_follow_through", "max_drawdown_pct": -1, "max_runup_pct": 6},
        )

    promote_act = client.post("/api/v1/orchestrator/act")
    assert promote_act.status_code == 200
    assert promote_act.json()["metrics"]["promoted_candidates"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    promoted = next(item for item in strategies if item["id"] == strategy["id"])
    assert promoted["current_version_id"] == candidate["id"]
    promoted_version = next(version for version in promoted["versions"] if version["id"] == candidate["id"])
    assert promoted_version["state"] == "approved"
    assert promoted_version["lifecycle_stage"] == "active"
    base_version = next(version for version in promoted["versions"] if version["id"] == base_version_id)
    assert base_version["lifecycle_stage"] == "approved"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"]["id"] == candidate["id"]
    assert payload["total_versions"] >= 2
    assert len(payload["candidate_versions"]) == 0
    assert any(version["id"] == base_version_id for version in payload["approved_versions"])

    validation_summaries = client.get("/api/v1/strategy-evolution/candidate-validations")
    assert validation_summaries.status_code == 200
    promoted_summary = next(
        item for item in validation_summaries.json() if item["candidate_version_id"] == candidate["id"]
    )
    assert promoted_summary["evaluation_status"] == "promote"
    assert promoted_summary["trade_count"] == 4
    assert promoted_summary["rolling_pass_rate"] >= 0.5
    assert promoted_summary["replay_score"] >= 0.58
    assert promoted_summary["profit_factor"] is not None

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    assert any(
        item["candidate_version_id"] == candidate["id"]
        for item in pipeline.json()["latest_candidate_validations"]
    )


def test_act_phase_rejects_candidate_variant_after_negative_validation(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "reject_candidate_strategy",
            "name": "Reject Candidate Strategy",
            "description": "Candidate rejection test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Seed failure",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    fork_act = client.post("/api/v1/orchestrator/act")
    assert fork_act.status_code == 200

    strategy_after_fork = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    candidate = next(version for version in strategy_after_fork["versions"] if version["id"] != base_version_id)

    for ticker in ["SNAP", "PINS", "ROKU", "SQ"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": candidate["id"],
                "signal_type": "candidate_breakout",
                "thesis": "Candidate validation loss",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.55,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": candidate["id"],
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
                "entry_context": {"execution_mode": "candidate_validation"},
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 97, "exit_reason": "failed_follow_through", "max_drawdown_pct": -4, "max_runup_pct": 1},
        )

    reject_act = client.post("/api/v1/orchestrator/act")
    assert reject_act.status_code == 200
    assert reject_act.json()["metrics"]["rejected_candidates"] >= 1

    rejected_strategy = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    rejected_candidate = next(version for version in rejected_strategy["versions"] if version["id"] == candidate["id"])
    assert rejected_candidate["state"] == "rejected"
    assert rejected_candidate["lifecycle_stage"] == "archived"

    validation_summaries = client.get("/api/v1/strategy-evolution/candidate-validations")
    assert validation_summaries.status_code == 200
    rejected_summary = next(
        item for item in validation_summaries.json() if item["candidate_version_id"] == candidate["id"]
    )
    assert rejected_summary["evaluation_status"] == "reject"
    assert rejected_summary["trade_count"] == 4
    assert rejected_summary["rolling_pass_rate"] is not None
    assert rejected_summary["replay_score"] is not None


def test_do_phase_prioritizes_degraded_candidate_versions(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "candidate_do_strategy",
            "name": "Candidate DO Strategy",
            "description": "DO prioritization test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Seed failure",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200

    strategy_after_act = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    assert strategy_after_act["status"] == "degraded"
    candidate_version = next(
        version for version in strategy_after_act["versions"] if version["lifecycle_stage"] == "candidate"
    )

    watchlist = client.post(
        "/api/v1/watchlists",
        json={
            "code": "candidate_do_watchlist",
            "name": "Candidate DO Watchlist",
            "strategy_id": strategy["id"],
            "hypothesis": "Validate candidate first.",
            "status": "active",
        },
    )
    assert watchlist.status_code == 201

    for ticker in ["NVDA", "AAPL"]:
        item = client.post(
            f"/api/v1/watchlists/{watchlist.json()['id']}/items",
            json={"ticker": ticker, "state": "watching"},
        )
        assert item.status_code == 201

    do_phase = client.post("/api/v1/orchestrator/do")
    assert do_phase.status_code == 200
    assert do_phase.json()["metrics"]["prioritized_candidate_items"] >= 2

    signals = client.get("/api/v1/signals").json()
    candidate_signals = [
        signal
        for signal in signals
        if signal["strategy_version_id"] == candidate_version["id"]
        and signal["ticker"] in {"NVDA", "AAPL"}
    ]
    assert len(candidate_signals) == 2
    assert all(signal["signal_context"]["execution_mode"] == "candidate_validation" for signal in candidate_signals)


def test_act_phase_opens_research_after_repeated_candidate_rejections(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "candidate_research_strategy",
            "name": "Candidate Research Strategy",
            "description": "Repeated candidate rejection test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    def seed_failure_and_fork(seed_tickers: list[str]) -> int:
        for ticker in seed_tickers:
            signal = client.post(
                "/api/v1/signals",
                json={
                    "ticker": ticker,
                    "strategy_id": strategy["id"],
                    "strategy_version_id": base_version_id,
                    "signal_type": "breakout",
                    "thesis": "Seed failure",
                    "entry_zone": {"price": 100},
                    "stop_zone": {"price": 95},
                    "target_zone": {"price": 110},
                    "signal_context": {"source": "test"},
                    "quality_score": 0.25,
                },
            ).json()
            position = client.post(
                "/api/v1/positions",
                json={
                    "ticker": ticker,
                    "signal_id": signal["id"],
                    "strategy_version_id": base_version_id,
                    "entry_price": 100,
                    "stop_price": 95,
                    "target_price": 110,
                    "size": 1,
                },
            ).json()
            client.post(
                f"/api/v1/positions/{position['id']}/close",
                json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
            )
            client.post(
                f"/api/v1/trade-reviews/positions/{position['id']}",
                json={
                    "outcome_label": "loss",
                    "outcome": "loss",
                    "cause_category": "false_breakout",
                    "failure_mode": "false_breakout",
                    "observations": {"pnl_pct": -6.0},
                    "root_cause": "Weak confirmation.",
                    "root_causes": ["Weak confirmation."],
                    "lesson_learned": "Need stronger confirmation.",
                    "recommended_changes": ["Raise confirmation threshold."],
                    "proposed_strategy_change": "Raise confirmation threshold.",
                    "should_modify_strategy": False,
                    "needs_strategy_update": False,
                    "strategy_update_reason": "Repeated false breakout profile.",
                },
            )

        client.post("/api/v1/orchestrator/check")
        fork_act = client.post("/api/v1/orchestrator/act")
        assert fork_act.status_code == 200

        strategy_state = next(
            item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
        )
        candidate = max(
            (version for version in strategy_state["versions"] if version["id"] != base_version_id),
            key=lambda version: version["version"],
        )
        return candidate["id"]

    def reject_candidate(candidate_version_id: int, validation_tickers: list[str]) -> dict:
        for ticker in validation_tickers:
            signal = client.post(
                "/api/v1/signals",
                json={
                    "ticker": ticker,
                    "strategy_id": strategy["id"],
                    "strategy_version_id": candidate_version_id,
                    "signal_type": "candidate_breakout",
                    "thesis": "Candidate validation loss",
                    "entry_zone": {"price": 100},
                    "stop_zone": {"price": 95},
                    "target_zone": {"price": 110},
                    "signal_context": {"source": "test"},
                    "quality_score": 0.55,
                },
            ).json()
            position = client.post(
                "/api/v1/positions",
                json={
                    "ticker": ticker,
                    "signal_id": signal["id"],
                    "strategy_version_id": candidate_version_id,
                    "entry_price": 100,
                    "stop_price": 95,
                    "target_price": 110,
                    "size": 1,
                    "entry_context": {"execution_mode": "candidate_validation"},
                },
            ).json()
            client.post(
                f"/api/v1/positions/{position['id']}/close",
                json={"exit_price": 97, "exit_reason": "failed_follow_through", "max_drawdown_pct": -4, "max_runup_pct": 1},
            )

        reject_act = client.post("/api/v1/orchestrator/act")
        assert reject_act.status_code == 200
        return reject_act.json()

    first_candidate_id = seed_failure_and_fork(["MDB", "DDOG"])
    reject_candidate(first_candidate_id, ["SNAP", "PINS", "ROKU", "SQ"])

    second_candidate_id = seed_failure_and_fork(["U", "SHOP"])
    reject_act = client.post("/api/v1/orchestrator/act")
    assert reject_act.status_code == 200
    reject_candidate(second_candidate_id, ["BILL", "UPST", "COIN", "AFRM"])

    final_act = client.post("/api/v1/orchestrator/act")
    assert final_act.status_code == 200

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    candidate_research_task = next(
        task for task in research_tasks.json() if task["task_type"] == "candidate_recovery_research"
    )
    assert candidate_research_task["strategy_id"] == strategy["id"]
    assert candidate_research_task["scope"]["rejected_candidate_count"] >= 2
    assert first_candidate_id in candidate_research_task["scope"]["candidate_version_ids"]
    assert second_candidate_id in candidate_research_task["scope"]["candidate_version_ids"]


def test_act_phase_queues_success_pattern_variant_for_validation(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "success_pattern_candidate_strategy",
            "name": "Success Pattern Candidate Strategy",
            "description": "Success pattern should queue candidate validation.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base winner hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["NVDA", "AAPL", "MSFT"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "trend_follow",
                "thesis": "Winning trend candidate",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 112},
                "signal_context": {"source": "test"},
                "quality_score": 0.9,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 112,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 106, "exit_reason": "target_hit", "max_drawdown_pct": -1, "max_runup_pct": 7},
        )

    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["generated_variants"] >= 1

    updated = next(item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"])
    assert updated["current_version_id"] == base_version_id
    candidate_versions = [version for version in updated["versions"] if version["id"] != base_version_id]
    assert len(candidate_versions) >= 1
    assert candidate_versions[0]["state"] == "draft"
    assert candidate_versions[0]["lifecycle_stage"] == "candidate"


def test_act_phase_forks_context_rule_candidate_variant(client: TestClient, session) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "context_rule_variant_strategy",
            "name": "Context Rule Variant Strategy",
            "description": "Learned context bundles should fork candidate variants.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Context-sensitive setup.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    session.add(
        StrategyContextRule(
            strategy_id=strategy["id"],
            strategy_version_id=base_version_id,
            feature_scope="combo",
            feature_key="setup__primary_regime",
            feature_value="breakout|ai_capex_boom",
            action_type="boost_confidence",
            rationale="Breakout entries worked best in the ai capex boom regime.",
            confidence=0.74,
            status="active",
            source="feature_outcome_stat",
            evidence_payload={"sample_size": 4, "avg_pnl_pct": 3.1},
        )
    )
    session.commit()

    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["generated_variants"] >= 1

    updated = next(item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"])
    assert updated["current_version_id"] == base_version_id
    candidate_versions = [version for version in updated["versions"] if version["id"] != base_version_id]
    assert len(candidate_versions) >= 1
    preferred_bundles = candidate_versions[0]["general_rules"].get("preferred_context_bundles") or []
    assert "combo.setup__primary_regime=breakout|ai_capex_boom" in preferred_bundles
