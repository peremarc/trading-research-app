from datetime import date

from app.db.models.decision_context import DecisionContextSnapshot, FeatureOutcomeStat, StrategyContextRule
from app.db.models.knowledge_claim import KnowledgeClaim, KnowledgeClaimEvidence
from app.domains.learning import api as learning_api
from app.providers.calendar import CalendarEvent
from app.providers.news import NewsArticle
from app.providers.web_research import WebPage, WebSearchResult


class StubWebResearchService:
    def search(self, query: str, *, max_results: int | None = None, domains: list[str] | None = None) -> list[WebSearchResult]:
        del max_results
        del domains
        return [
            WebSearchResult(
                title=f"{query} confirmation",
                url="https://reuters.com/markets/nvda-outlook",
                snippet="External confirmation",
                source="stub",
            )
        ]

    def fetch_article(self, url: str, *, max_chars: int | None = None) -> WebPage:
        del max_chars
        return WebPage(
            url=url,
            title="NVDA setup confirmed",
            text="Momentum and earnings expectations remain constructive.",
            source="stub",
        )


class StubNewsService:
    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None) -> list[NewsArticle]:
        del max_results
        return [
            NewsArticle(
                title=f"{ticker} raises guidance after strong demand",
                description="Fresh positive catalyst for the ticker.",
                url="https://example.com/news",
                source_name="ExampleWire",
                published_at="2026-04-17T12:00:00Z",
            )
        ]


class StubCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        del days_ahead
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {ticker}",
                event_date="2026-04-24",
                ticker=ticker,
                source="stub",
            )
        ]

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        del days_ahead
        return [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date="2026-04-20",
                country="US",
                impact="high",
                source="stub",
            )
        ]

    def get_quarterly_expiry_context(self, *, as_of: date | None = None) -> dict:
        del as_of
        return {
            "available": True,
            "source": "stub",
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


class OpenMarketHoursService:
    class Session:
        is_regular_session_open = True
        session_label = "regular"
        next_regular_open = None
        next_regular_close = None

        def to_payload(self) -> dict:
            return {
                "market": "us_equities",
                "timezone": "America/New_York",
                "session_label": self.session_label,
                "is_weekend": False,
                "is_trading_day": True,
                "is_regular_session_open": True,
                "is_extended_hours": False,
                "now_utc": "2026-04-19T14:30:00+00:00",
                "now_local": "2026-04-19T10:30:00-04:00",
                "next_regular_open": None,
                "next_regular_close": None,
            }

    def get_session_state(self):
        return self.Session()


class ExecutionCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        del ticker, days_ahead
        return []

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        del days_ahead
        return []


def noop_macro_research(session, *, market_state_snapshot):
    del session, market_state_snapshot
    return {
        "triggered": False,
        "topics_reviewed": 0,
        "signals_recorded": 0,
        "tasks_opened": 0,
        "watchlists_created": 0,
        "watchlists_refreshed": 0,
        "watchlist_codes": [],
        "focus_themes": [],
        "focus_assets": [],
        "reason": "macro_research_disabled_in_test",
    }


def capture_bullish_snapshot(session, *, trigger: str, pdca_phase: str | None = None, source_context: dict | None = None):
    from app.db.models.market_state_snapshot import MarketStateSnapshotRecord

    payload = {
        "summary": "World state for do phase: regime bullish_trend with room for selective long entries.",
        "market_state_snapshot": {
            "execution_mode": "global",
            "watchlist_code": None,
            "portfolio_state": {
                "benchmark_ticker": "SPY",
                "benchmark_price": 100.0,
                "benchmark_month_performance": 0.08,
                "market_state_trigger": trigger,
                "market_state_phase": pdca_phase,
            },
            "open_positions": [],
            "recent_alerts": [],
            "macro_context": {
                "summary": "Bullish trend regime.",
                "active_regimes": ["bullish_trend"],
                "global_regime": "bullish_trend",
                "global_regime_confidence": 0.82,
            },
            "corporate_calendar": [],
            "market_regime_inputs": {
                "benchmark_snapshot": {
                    "ticker": "SPY",
                    "price": 100.0,
                    "month_performance": 0.08,
                },
                "market_regime": {
                    "label": "bullish_trend",
                    "confidence": 0.82,
                    "justification": "Test bullish trend regime.",
                },
            },
            "active_watchlists": [],
        },
        "market_regime": {
            "label": "bullish_trend",
            "confidence": 0.82,
            "justification": "Test bullish trend regime.",
        },
        "benchmark_snapshot": {
            "ticker": "SPY",
            "price": 100.0,
            "month_performance": 0.08,
        },
        "macro_context": {
            "summary": "Bullish trend regime.",
            "active_regimes": ["bullish_trend"],
        },
        "calendar_events": [],
        "calendar_error": None,
        "backlog": {
            "open_positions_count": 0,
            "pending_reviews": 0,
            "open_research_tasks": 0,
            "active_watchlists_count": 1,
        },
        "trigger": trigger,
        "pdca_phase": pdca_phase,
        "source_context": source_context or {},
    }
    snapshot = MarketStateSnapshotRecord(
        trigger=trigger,
        pdca_phase=pdca_phase,
        execution_mode=(source_context or {}).get("execution_mode"),
        benchmark_ticker="SPY",
        regime_label="bullish_trend",
        regime_confidence=0.82,
        summary=payload["summary"],
        snapshot_payload=payload,
        source_context=source_context or {},
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


def _deterministic_signal(ticker: str) -> dict:
    return {
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
        "price_action_context": {
            "available": True,
            "timeframe": "1D",
            "method": "ohlcv_price_action_proxies_v1",
            "bias": "supportive",
            "volume_state": "high",
            "close_location_state": "strong_close",
            "signal_count": 2,
            "triggered_signals": [
                {
                    "code": "failed_breakdown_reversal",
                    "signal_kind": "trigger",
                    "score": 0.81,
                    "details": "Synthetic daily reclaim test signal.",
                },
                {
                    "code": "high_relative_volume_reversal",
                    "signal_kind": "confirmation",
                    "score": 0.74,
                    "details": "Synthetic high relative volume confirmation.",
                },
            ],
            "triggered_signal_codes": [
                "failed_breakdown_reversal",
                "high_relative_volume_reversal",
            ],
            "signal_definition_codes": [
                "failed_breakdown_reversal",
                "high_relative_volume_reversal",
            ],
            "primary_signal_code": "failed_breakdown_reversal",
            "primary_signal_kind": "trigger",
            "primary_signal_score": 0.81,
            "confirmation_bonus": 0.05,
            "summary": (
                "Daily price action proxy is supportive: "
                "primary_signal=failed_breakdown_reversal, signal_count=2, signal_kind=trigger."
            ),
        },
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 110.0,
        "risk_reward": 2.5,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": f"Deterministic test signal for {ticker}.",
    }


def _create_strategy_with_watchlist(client) -> tuple[dict, dict]:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "relevance_strategy",
            "name": "Relevance Strategy",
            "description": "Strategy for relevance engine tests.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Momentum with contextual confirmation.",
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
            "code": "relevance_watchlist",
            "name": "Relevance Watchlist",
            "strategy_id": strategy["id"],
            "hypothesis": "Track candidates for relevance learning.",
            "status": "active",
        },
    ).json()
    assert client.post(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"ticker": "NVDA", "reason": "Relevance engine candidate"},
    ).status_code == 201
    return strategy, watchlist


def test_do_phase_records_decision_context_snapshot(client, session) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_web = learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service
    original_news = learning_api.orchestrator_service.decision_context_assembler_service.news_service
    original_calendar = learning_api.orchestrator_service.decision_context_assembler_service.calendar_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot
    original_advise_trade_candidate = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    original_tool_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_tool_news = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_macro_research = learning_api.orchestrator_service._run_macro_geopolitical_research
    learning_api.orchestrator_service.signal_service.analyze_ticker = _deterministic_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    learning_api.orchestrator_service.decision_context_assembler_service.news_service = StubNewsService()
    learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = StubCalendarService()
    learning_api.orchestrator_service.market_hours_service = OpenMarketHoursService()
    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = lambda *args, **kwargs: None
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = ExecutionCalendarService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = StubNewsService()
    learning_api.orchestrator_service._run_macro_geopolitical_research = noop_macro_research
    try:
        _create_strategy_with_watchlist(client)
        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = original_web
        learning_api.orchestrator_service.decision_context_assembler_service.news_service = original_news
        learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = original_calendar
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_advise_trade_candidate
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_tool_calendar
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_tool_news
        learning_api.orchestrator_service._run_macro_geopolitical_research = original_macro_research

    assert response.status_code == 200
    snapshots = session.query(DecisionContextSnapshot).all()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.executed is True
    assert snapshot.trade_signal_id == snapshot.signal_id
    assert snapshot.planner_action == "paper_enter"
    assert snapshot.execution_outcome == "paper_enter"
    assert snapshot.quant_features["trend"] == "uptrend"
    assert snapshot.visual_features["setup_type"] == "breakout"
    assert snapshot.web_context["search"]["results"][0]["source"] == "stub"
    assert snapshot.web_context["article"]["title"] == "NVDA setup confirmed"
    assert snapshot.position_context["decision_context"]["news_context"]["sentiment_bias"] == "positive"
    assert snapshot.position_context["research_plan"]["tool_budget"]["max_research_steps"] >= 9
    assert snapshot.position_context["decision_trace"]["initial_hypothesis"]
    assert snapshot.position_context["price_action_context"]["primary_signal_code"] == "failed_breakdown_reversal"
    assert snapshot.position_context["skill_context"]["catalog_version"] == "skills_v1"
    assert snapshot.position_context["skill_context"]["primary_skill_code"] == "detect_risk_off_conditions"
    assert (
        snapshot.position_context["executed_entry_context"]["price_action_context"]["primary_signal_code"]
        == "failed_breakdown_reversal"
    )
    assert snapshot.position_context["executed_entry_context"]["skill_context"]["primary_skill_code"] == "detect_risk_off_conditions"
    assert snapshot.position_context["executed_entry_context"]["research_execution"]["successful_tools"]
    assert snapshot.position_context["executed_decision_trace"]["runtime_tool_outcomes"]
    assert snapshot.position_context["decision_context"]["calendar_context"]["near_earnings_days"] is not None
    assert snapshot.position_context["decision_context"]["calendar_context"]["near_macro_high_impact_days"] is not None
    assert snapshot.position_context["decision_context"]["calendar_context"]["expiry_context"]["phase"] == "tight_pre_expiry_window"
    assert snapshot.position_context["executed_entry_context"]["expiry_context"]["phase"] == "tight_pre_expiry_window"


def test_check_phase_recomputes_feature_outcome_stats(client, session) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_web = learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service
    original_calendar = learning_api.orchestrator_service.decision_context_assembler_service.calendar_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot
    original_advise_trade_candidate = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    original_tool_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_tool_news = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_macro_research = learning_api.orchestrator_service._run_macro_geopolitical_research
    learning_api.orchestrator_service.signal_service.analyze_ticker = _deterministic_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = StubCalendarService()
    learning_api.orchestrator_service.market_hours_service = OpenMarketHoursService()
    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = lambda *args, **kwargs: None
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = ExecutionCalendarService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = StubNewsService()
    learning_api.orchestrator_service._run_macro_geopolitical_research = noop_macro_research
    try:
        _create_strategy_with_watchlist(client)
        assert client.post("/api/v1/orchestrator/do").status_code == 200
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = original_web
        learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = original_calendar
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_advise_trade_candidate
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_tool_calendar
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_tool_news
        learning_api.orchestrator_service._run_macro_geopolitical_research = original_macro_research

    position = client.get("/api/v1/positions").json()[0]
    closed = client.post(
        f"/api/v1/positions/{position['id']}/close",
        json={
            "exit_price": 106.0,
            "exit_reason": "target_hit",
            "max_drawdown_pct": -1.2,
            "max_runup_pct": 6.0,
        },
    )
    assert closed.status_code == 200

    check = client.post("/api/v1/orchestrator/check")
    assert check.status_code == 200
    assert check.json()["metrics"]["feature_stats_generated"] > 0

    stats = session.query(FeatureOutcomeStat).all()
    assert len(stats) > 0
    breakout_stat = next(
        item
        for item in stats
        if item.feature_scope == "quant" and item.feature_key == "setup" and item.feature_value == "breakout"
    )
    price_action_stat = next(
        item
        for item in stats
        if item.feature_scope == "price_action"
        and item.feature_key == "primary_signal"
        and item.feature_value == "failed_breakdown_reversal"
    )
    price_action_combo_stat = next(
        item
        for item in stats
        if item.feature_scope == "combo"
        and item.feature_key == "setup__price_action_primary"
        and item.feature_value == "breakout|failed_breakdown_reversal"
    )
    skill_stat = next(
        item
        for item in stats
        if item.feature_scope == "skill"
        and item.feature_key == "primary_skill"
        and item.feature_value == "detect_risk_off_conditions"
    )
    expiry_combo_stat = next(
        item
        for item in stats
        if item.feature_scope == "combo"
        and item.feature_key == "setup__days_to_quarterly_expiry_bucket"
        and item.feature_value == "breakout|T-1"
    )
    assert breakout_stat.sample_size == 1
    assert breakout_stat.wins_count == 1
    assert breakout_stat.losses_count == 0
    assert breakout_stat.avg_pnl_pct == 6.0
    assert price_action_stat.sample_size == 1
    assert skill_stat.sample_size == 1
    assert price_action_combo_stat.sample_size == 1
    assert expiry_combo_stat.sample_size == 1


def test_check_phase_generates_positive_strategy_context_rules(client, session) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_web = learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service
    original_news = learning_api.orchestrator_service.decision_context_assembler_service.news_service
    original_calendar = learning_api.orchestrator_service.decision_context_assembler_service.calendar_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot
    original_advise_trade_candidate = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    original_tool_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_tool_news = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_macro_research = learning_api.orchestrator_service._run_macro_geopolitical_research
    learning_api.orchestrator_service.signal_service.analyze_ticker = _deterministic_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    learning_api.orchestrator_service.decision_context_assembler_service.news_service = StubNewsService()
    learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = StubCalendarService()
    learning_api.orchestrator_service.market_hours_service = OpenMarketHoursService()
    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = lambda *args, **kwargs: None
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = ExecutionCalendarService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = StubNewsService()
    learning_api.orchestrator_service._run_macro_geopolitical_research = noop_macro_research
    try:
        _, watchlist = _create_strategy_with_watchlist(client)
        assert client.post("/api/v1/orchestrator/do").status_code == 200
        first_position = client.get("/api/v1/positions").json()[0]
        assert client.post(
            f"/api/v1/positions/{first_position['id']}/close",
            json={
                "exit_price": 106.0,
                "exit_reason": "target_hit",
                "max_drawdown_pct": -1.2,
                "max_runup_pct": 6.0,
            },
        ).status_code == 200

        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "AAPL", "reason": "Positive rule candidate"},
        ).status_code == 201
        assert client.post("/api/v1/orchestrator/do").status_code == 200
        second_position = next(position for position in client.get("/api/v1/positions").json() if position["ticker"] == "AAPL")
        assert client.post(
            f"/api/v1/positions/{second_position['id']}/close",
            json={
                "exit_price": 107.0,
                "exit_reason": "target_hit",
                "max_drawdown_pct": -1.0,
                "max_runup_pct": 7.0,
            },
        ).status_code == 200

        check = client.post("/api/v1/orchestrator/check")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = original_web
        learning_api.orchestrator_service.decision_context_assembler_service.news_service = original_news
        learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = original_calendar
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_advise_trade_candidate
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_tool_calendar
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_tool_news
        learning_api.orchestrator_service._run_macro_geopolitical_research = original_macro_research

    assert check.status_code == 200
    assert check.json()["metrics"]["strategy_context_rules_generated"] > 0

    rules = session.query(StrategyContextRule).all()
    positive_rule = next(
        item
        for item in rules
        if item.action_type == "boost_confidence"
        and item.feature_scope == "quant"
        and item.feature_key == "setup"
        and item.feature_value == "breakout"
    )
    assert positive_rule.confidence is not None
    assert "historical average PnL" in positive_rule.rationale
    assert positive_rule.evidence_payload["promotion_trace"]["promotion_path_stage"] == "temporary_rule"

    claims = session.query(KnowledgeClaim).all()
    rule_claim = next(
        item
        for item in claims
        if item.claim_type == "context_rule"
        and item.meta.get("rule_id") == positive_rule.id
    )
    assert rule_claim.status in {"supported", "validated"}
    rule_evidence = session.query(KnowledgeClaimEvidence).filter(KnowledgeClaimEvidence.claim_id == rule_claim.id).all()
    assert len(rule_evidence) == 1
    assert rule_evidence[0].source_type == "strategy_context_rule"
    assert rule_claim.meta["linked_skill_candidate_id"] is not None

    skill_candidates = client.get("/api/v1/skills/candidates")
    assert skill_candidates.status_code == 200
    rule_candidate = next(
        item
        for item in skill_candidates.json()
        if item["meta"].get("source_claim_id") == rule_claim.id
    )
    assert rule_candidate["target_skill_code"] == "evaluate_daily_breakout"
    assert rule_candidate["candidate_action"] == "update_existing_skill"


def test_learned_strategy_context_rules_can_block_future_entries(client, session) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_web = learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot
    original_advise_trade_candidate = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    original_tool_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_tool_news = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_macro_research = learning_api.orchestrator_service._run_macro_geopolitical_research
    learning_api.orchestrator_service.signal_service.analyze_ticker = _deterministic_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    learning_api.orchestrator_service.market_hours_service = OpenMarketHoursService()
    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = lambda *args, **kwargs: None
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = ExecutionCalendarService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = StubNewsService()
    learning_api.orchestrator_service._run_macro_geopolitical_research = noop_macro_research
    try:
        _, watchlist = _create_strategy_with_watchlist(client)

        assert client.post("/api/v1/orchestrator/do").status_code == 200
        first_position = client.get("/api/v1/positions").json()[0]
        assert client.post(
            f"/api/v1/positions/{first_position['id']}/close",
            json={
                "exit_price": 94.0,
                "exit_reason": "failed_breakout",
                "max_drawdown_pct": -6.0,
                "max_runup_pct": 1.0,
            },
        ).status_code == 200

        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "AAPL", "reason": "Second relevance candidate"},
        ).status_code == 201
        assert client.post("/api/v1/orchestrator/do").status_code == 200
        second_position = next(position for position in client.get("/api/v1/positions").json() if position["ticker"] == "AAPL")
        assert client.post(
            f"/api/v1/positions/{second_position['id']}/close",
            json={
                "exit_price": 95.0,
                "exit_reason": "failed_breakout",
                "max_drawdown_pct": -5.0,
                "max_runup_pct": 1.2,
            },
        ).status_code == 200

        check = client.post("/api/v1/orchestrator/check")
        assert check.status_code == 200
        assert check.json()["metrics"]["strategy_context_rules_generated"] > 0

        rules = session.query(StrategyContextRule).all()
        assert len(rules) > 0
        setup_rule = next(
            item
            for item in rules
            if item.feature_scope == "quant" and item.feature_key == "setup" and item.feature_value == "breakout"
        )
        assert setup_rule.action_type == "downgrade_to_watch"

        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "MSFT", "reason": "Third relevance candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = original_web
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_advise_trade_candidate
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_tool_calendar
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_tool_news
        learning_api.orchestrator_service._run_macro_geopolitical_research = original_macro_research

    assert response.status_code == 200
    assert response.json()["metrics"]["learned_rule_blocked_entries"] >= 1

    positions = client.get("/api/v1/positions").json()
    assert len(positions) == 2
    assert {position["ticker"] for position in positions} == {"NVDA", "AAPL"}

    journal = client.get("/api/v1/journal").json()
    assert any(entry["decision"] == "skip_strategy_context_rule" for entry in journal)


def test_combo_strategy_context_rules_are_generated_and_reused(client, session) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_web = learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service
    original_news = learning_api.orchestrator_service.decision_context_assembler_service.news_service
    original_calendar = learning_api.orchestrator_service.decision_context_assembler_service.calendar_service
    original_market_hours = learning_api.orchestrator_service.market_hours_service
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot
    original_advise_trade_candidate = learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate
    original_tool_calendar = learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service
    original_tool_news = learning_api.orchestrator_service.agent_tool_gateway_service.news_service
    original_macro_research = learning_api.orchestrator_service._run_macro_geopolitical_research
    learning_api.orchestrator_service.signal_service.analyze_ticker = _deterministic_signal
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    learning_api.orchestrator_service.decision_context_assembler_service.news_service = StubNewsService()
    learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = StubCalendarService()
    learning_api.orchestrator_service.market_hours_service = OpenMarketHoursService()
    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = lambda *args, **kwargs: None
    learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = ExecutionCalendarService()
    learning_api.orchestrator_service.agent_tool_gateway_service.news_service = StubNewsService()
    learning_api.orchestrator_service._run_macro_geopolitical_research = noop_macro_research
    try:
        _, watchlist = _create_strategy_with_watchlist(client)
        assert client.post("/api/v1/orchestrator/do").status_code == 200
        first_position = client.get("/api/v1/positions").json()[0]
        assert client.post(
            f"/api/v1/positions/{first_position['id']}/close",
            json={
                "exit_price": 106.0,
                "exit_reason": "target_hit",
                "max_drawdown_pct": -1.2,
                "max_runup_pct": 6.0,
            },
        ).status_code == 200

        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "AAPL", "reason": "Combo context candidate"},
        ).status_code == 201
        assert client.post("/api/v1/orchestrator/do").status_code == 200
        second_position = next(position for position in client.get("/api/v1/positions").json() if position["ticker"] == "AAPL")
        assert client.post(
            f"/api/v1/positions/{second_position['id']}/close",
            json={
                "exit_price": 107.0,
                "exit_reason": "target_hit",
                "max_drawdown_pct": -1.0,
                "max_runup_pct": 7.0,
            },
        ).status_code == 200

        check = client.post("/api/v1/orchestrator/check")
        assert check.status_code == 200

        stats = session.query(FeatureOutcomeStat).all()
        combo_stat = next(
            item
            for item in stats
            if item.feature_scope == "combo"
            and item.feature_key == "setup__has_news"
            and item.feature_value == "breakout|true"
        )
        assert combo_stat.sample_size == 2
        assert combo_stat.avg_pnl_pct == 6.5
        assert combo_stat.evidence_payload["components"][0]["scope"] == "quant"
        assert combo_stat.evidence_payload["components"][1]["scope"] == "news"

        rules = session.query(StrategyContextRule).all()
        combo_rule = next(
            item
            for item in rules
            if item.feature_scope == "combo"
            and item.feature_key == "setup__has_news"
            and item.feature_value == "breakout|true"
        )
        assert combo_rule.action_type == "boost_confidence"
        assert "quant.setup=breakout + news.has_news=true" in combo_rule.rationale

        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "MSFT", "reason": "Combo rule reuse candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.agent_tool_gateway_service.web_research_service = original_web
        learning_api.orchestrator_service.decision_context_assembler_service.news_service = original_news
        learning_api.orchestrator_service.decision_context_assembler_service.calendar_service = original_calendar
        learning_api.orchestrator_service.market_hours_service = original_market_hours
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot
        learning_api.orchestrator_service.trading_agent_service.advise_trade_candidate = original_advise_trade_candidate
        learning_api.orchestrator_service.agent_tool_gateway_service.calendar_service = original_tool_calendar
        learning_api.orchestrator_service.agent_tool_gateway_service.news_service = original_tool_news
        learning_api.orchestrator_service._run_macro_geopolitical_research = original_macro_research

    assert response.status_code == 200
    msft_signal = next(item for item in client.get("/api/v1/signals").json() if item["ticker"] == "MSFT")
    supporting_rules = msft_signal["signal_context"]["decision_context"]["supporting_context_rules"]
    assert any(
        rule["feature_scope"] == "combo"
        and rule["feature_key"] == "setup__has_news"
        and rule["feature_value"] == "breakout|true"
        for rule in supporting_rules
    )
    assert msft_signal["signal_context"]["score_breakdown"]["learned_rule_bonus"] > 0
