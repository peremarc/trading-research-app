import pytest

from app.domains.learning.agent import AgentActionPlan, AgentDecision, AgentToolStep, AutonomousTradingAgentService
from app.domains.learning.tools import AgentToolError, AgentToolGatewayService
from app.providers.calendar import CalendarEvent, CalendarProviderError
from app.providers.web_research import WebPage, WebSearchResult


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


class FailingCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        raise CalendarProviderError("calendar provider unavailable")

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        return []


class StubWebResearchService:
    def search(self, query: str, *, max_results: int | None = None, domains: list[str] | None = None) -> list[WebSearchResult]:
        del max_results
        del domains
        return [
            WebSearchResult(
                title=f"{query} article",
                url="https://reuters.com/markets/nvda-outlook",
                snippet="Research snippet",
                source="stub",
            )
        ]

    def fetch_article(self, url: str, *, max_chars: int | None = None) -> WebPage:
        del max_chars
        return WebPage(
            url=url,
            title="NVDA article",
            text="AI demand remains strong and earnings expectations improved.",
            source="stub",
        )


class OverviewMarketDataService:
    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        return {
            "available": True,
            "symbol": ticker.upper(),
            "sec_type": sec_type,
            "provider_source": "test_fixture",
            "market_signals": {"available": True, "last_price": 100.5},
            "options_sentiment": {"available": True, "put_call_ratio": 1.12},
            "corporate_events": [],
            "provider_error": None,
        }


def test_agent_can_build_trade_execution_plan() -> None:
    service = AutonomousTradingAgentService()

    plan = service.plan_trade_candidate_execution(
        ticker="NVDA",
        strategy_version_id=7,
        signal_id=11,
        analysis_run_id=13,
        signal_payload={
            "decision": "paper_enter",
            "decision_confidence": 0.82,
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 112.0,
            "rationale": "Breakout with strong follow-through.",
            "ai_overlay": {"action": "paper_enter"},
        },
        entry_context={"source": "test", "execution_mode": "candidate_validation"},
        opening_reason="Planner test",
    )

    assert plan.should_execute is True
    assert len(plan.steps) == 11
    assert plan.steps[0].tool_name == "market.get_snapshot"
    assert plan.steps[1].tool_name == "calendar.get_ticker_events"
    assert plan.steps[2].tool_name == "calendar.get_macro_events"
    assert plan.steps[3].tool_name == "positions.list_open"
    assert plan.steps[4].tool_name == "market.get_multitimeframe_context"
    assert plan.steps[5].tool_name == "macro.get_context"
    assert plan.steps[6].tool_name == "news.get_ticker_news"
    assert plan.steps[7].tool_name == "web.search"
    assert plan.steps[8].tool_name == "web.fetch_article"
    assert plan.steps[9].tool_name == "strategies.list_pipelines"
    assert plan.steps[10].tool_name == "positions.open"
    assert plan.steps[10].arguments["ticker"] == "NVDA"
    assert plan.steps[10].arguments["strategy_version_id"] == 7
    assert plan.steps[10].arguments["signal_id"] == 11
    assert plan.steps[10].arguments["trade_signal_id"] == 11
    assert plan.steps[4].arguments["timeframes"] == ["1M", "3M", "6M", "1Y", "5Y"]
    assert plan.steps[8].arguments["search_result_index"] == 0


def test_agent_can_build_management_execution_plan() -> None:
    service = AutonomousTradingAgentService()

    class PositionStub:
        id = 3
        ticker = "AAPL"
        strategy_version_id = 9
        entry_price = 100.0
        stop_price = 95.0
        target_price = 105.0
        side = "long"

    plan = service.plan_open_position_management_execution(
        position=PositionStub(),
        market_snapshot={"price": 104.0, "atr_14": 2.0},
        decision=AgentDecision(
            action="tighten_stop_and_extend_target",
            confidence=0.8,
            thesis="Trend intact, tighten risk and extend upside.",
            risks=["failed breakout"],
            lessons_applied=["protect gains"],
            raw_payload={},
        ),
    )

    assert plan is not None
    assert plan.should_execute is True
    assert len(plan.steps) == 2
    assert plan.steps[0].tool_name == "positions.list_open"
    assert plan.steps[1].tool_name == "positions.manage"
    assert plan.steps[1].arguments["position_id"] == 3
    assert plan.steps[1].arguments["stop_price"] == 102.0
    assert plan.steps[1].arguments["target_price"] == 108.0


def test_execute_plan_rejects_duplicate_expensive_steps(session) -> None:
    gateway = AgentToolGatewayService()
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.8,
        rationale="Invalid duplicate news lookup",
        should_execute=True,
        steps=[
            AgentToolStep(tool_name="market.get_snapshot", arguments={"ticker": "NVDA"}, purpose="refresh market state"),
            AgentToolStep(tool_name="news.get_ticker_news", arguments={"ticker": "NVDA"}, purpose="news check"),
            AgentToolStep(tool_name="news.get_ticker_news", arguments={"ticker": "NVDA"}, purpose="duplicate news check"),
        ],
    )

    with pytest.raises(AgentToolError, match="repeats tool 'news.get_ticker_news'"):
        gateway.execute_plan(session, plan)


def test_market_overview_tool_executes(session) -> None:
    gateway = AgentToolGatewayService(market_data_service=OverviewMarketDataService())

    result = gateway.execute(session, "market.get_overview", {"ticker": "NVDA"})

    assert result["symbol"] == "NVDA"
    assert result["provider_source"] == "test_fixture"
    assert result["market_signals"]["last_price"] == 100.5


def test_execute_plan_rejects_non_open_position_references(session) -> None:
    gateway = AgentToolGatewayService()
    plan = AgentActionPlan(
        action="close_position",
        confidence=0.9,
        rationale="Invalid close on missing position",
        should_execute=True,
        steps=[
            AgentToolStep(tool_name="positions.list_open", arguments={}, purpose="load exposure"),
            AgentToolStep(
                tool_name="positions.close",
                arguments={
                    "position_id": 999,
                    "exit_price": 101.0,
                    "exit_reason": "test close",
                },
                purpose="close missing position",
            ),
        ],
    )

    with pytest.raises(AgentToolError, match="unknown position_id=999"):
        gateway.execute_plan(session, plan)


def test_execute_plan_rejects_excessive_length(session) -> None:
    gateway = AgentToolGatewayService()
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.85,
        rationale="Too many steps for a controlled plan",
        should_execute=True,
        steps=[
            AgentToolStep(tool_name="market.get_snapshot", arguments={"ticker": "NVDA"}, purpose="step 1"),
            AgentToolStep(tool_name="calendar.get_ticker_events", arguments={"ticker": "NVDA"}, purpose="step 2"),
            AgentToolStep(tool_name="calendar.get_macro_events", arguments={"days_ahead": 3}, purpose="step 3"),
            AgentToolStep(tool_name="macro.get_context", arguments={"limit": 5}, purpose="step 4"),
            AgentToolStep(tool_name="market.get_multitimeframe_context", arguments={"ticker": "NVDA"}, purpose="step 5"),
            AgentToolStep(tool_name="positions.list_open", arguments={}, purpose="step 6"),
            AgentToolStep(tool_name="news.get_ticker_news", arguments={"ticker": "NVDA"}, purpose="step 7"),
            AgentToolStep(tool_name="web.search", arguments={"query": "NVDA earnings", "max_results": 3}, purpose="step 8"),
            AgentToolStep(tool_name="web.fetch_article", arguments={"search_result_index": 0, "max_chars": 4000}, purpose="step 9"),
            AgentToolStep(tool_name="strategies.list_pipelines", arguments={}, purpose="step 10"),
            AgentToolStep(tool_name="strategies.list", arguments={}, purpose="step 11"),
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 112.0,
                    "size": 1.0,
                    "opening_reason": "Too long",
                },
                purpose="step 12",
            ),
        ],
    )

    with pytest.raises(AgentToolError, match="Plan exceeds maximum length"):
        gateway.execute_plan(session, plan)


def test_execute_plan_blocks_position_open_when_near_term_calendar_risk_exists(session) -> None:
    gateway = AgentToolGatewayService(calendar_service=NearTermCalendarService())
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.84,
        rationale="Strong setup but must pass calendar checks.",
        should_execute=True,
        steps=[
            AgentToolStep(
                tool_name="calendar.get_ticker_events",
                arguments={"ticker": "NVDA", "days_ahead": 7},
                purpose="check earnings",
            ),
            AgentToolStep(
                tool_name="calendar.get_macro_events",
                arguments={"days_ahead": 3},
                purpose="check macro",
            ),
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 112.0,
                    "size": 1.0,
                    "opening_reason": "Calendar-guarded test",
                },
                purpose="open only if calendar is clear",
            ),
        ],
    )

    results = gateway.execute_plan(session, plan)

    assert results[-1]["tool_name"] == "positions.open"
    assert results[-1]["status"] == "blocked"
    assert results[-1]["result"]["reason"] == "calendar_risk"
    assert results[-1]["result"]["corporate_events"][0]["title"] == "Earnings NVDA"
    assert gateway.position_service.list_positions(session) == []


def test_execute_plan_blocks_position_open_when_calendar_checks_fail(session) -> None:
    gateway = AgentToolGatewayService(calendar_service=FailingCalendarService())
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.84,
        rationale="Strong setup but calendar provider failed.",
        should_execute=True,
        steps=[
            AgentToolStep(
                tool_name="calendar.get_ticker_events",
                arguments={"ticker": "NVDA", "days_ahead": 7},
                purpose="check earnings",
            ),
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 112.0,
                    "size": 1.0,
                    "opening_reason": "Calendar failure test",
                },
                purpose="open only if calendar is clear",
            ),
        ],
    )

    results = gateway.execute_plan(session, plan)

    assert results[0]["tool_name"] == "calendar.get_ticker_events"
    assert results[0]["status"] == "error"
    assert results[-1]["tool_name"] == "positions.open"
    assert results[-1]["status"] == "blocked"
    assert results[-1]["result"]["reason"] == "calendar_check_failed"
    assert gateway.position_service.list_positions(session) == []


def test_execute_plan_blocks_position_open_when_portfolio_limits_are_reached(session) -> None:
    gateway = AgentToolGatewayService()
    gateway.execute(
        session,
        "positions.open",
        {
            "ticker": "NVDA",
            "strategy_version_id": 1,
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 112.0,
            "size": 1.0,
            "opening_reason": "Existing position for exposure test",
        },
    )
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.82,
        rationale="Second thesis on the same ticker must respect exposure limits.",
        should_execute=True,
        steps=[
            AgentToolStep(tool_name="positions.list_open", arguments={}, purpose="review exposure"),
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "strategy_version_id": 2,
                    "entry_price": 101.0,
                    "stop_price": 97.0,
                    "target_price": 111.0,
                    "size": 1.0,
                    "opening_reason": "Should be blocked by exposure rule",
                    "entry_context": {"strategy_rules": {"max_same_ticker_positions": 1}},
                },
                purpose="open only if ticker exposure limit allows it",
            ),
        ],
    )

    results = gateway.execute_plan(session, plan)

    assert results[-1]["tool_name"] == "positions.open"
    assert results[-1]["status"] == "blocked"
    assert results[-1]["result"]["reason"] == "portfolio_limit"
    assert "ticker exposure limit" in results[-1]["result"]["summary"]
    assert len(gateway.position_service.list_positions(session)) == 1


def test_execute_plan_blocks_position_open_when_risk_budget_guard_is_active(session) -> None:
    gateway = AgentToolGatewayService()
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.81,
        rationale="Good setup but the kill switch should block execution.",
        should_execute=True,
        steps=[
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 112.0,
                    "size": 200.0,
                    "opening_reason": "Risk budget guard test",
                    "entry_context": {
                        "risk_budget": {
                            "kill_switch": {
                                "triggered": True,
                                "reasons": ["daily realized pnl -5.0% breached limit -4.0%"],
                            }
                        },
                        "position_sizing": {
                            "status": "blocked",
                            "reasons": ["daily realized pnl -5.0% breached limit -4.0%"],
                        },
                    },
                },
                purpose="open only if risk budget is available",
            ),
        ],
    )

    results = gateway.execute_plan(session, plan)

    assert results[-1]["tool_name"] == "positions.open"
    assert results[-1]["status"] == "blocked"
    assert results[-1]["result"]["reason"] == "risk_budget_limit"
    assert "risk budget guard" in results[-1]["result"]["summary"]
    assert gateway.position_service.list_positions(session) == []


def test_execute_plan_persists_web_research_and_multitimeframe_visual_into_entry_context(session) -> None:
    gateway = AgentToolGatewayService(web_research_service=StubWebResearchService())
    plan = AgentActionPlan(
        action="paper_enter",
        confidence=0.88,
        rationale="Strong setup with external confirmation.",
        should_execute=True,
        steps=[
            AgentToolStep(
                tool_name="positions.list_open",
                arguments={},
                purpose="review exposure",
            ),
            AgentToolStep(
                tool_name="market.get_multitimeframe_context",
                arguments={"ticker": "NVDA", "timeframes": ["1M", "3M", "6M", "1Y", "5Y"]},
                purpose="review charts",
            ),
            AgentToolStep(
                tool_name="web.search",
                arguments={"query": "NVDA earnings outlook guidance stock", "max_results": 3},
                purpose="search web",
            ),
            AgentToolStep(
                tool_name="web.fetch_article",
                arguments={"search_result_index": 0, "max_chars": 4000},
                purpose="fetch top result",
            ),
            AgentToolStep(
                tool_name="positions.open",
                arguments={
                    "ticker": "NVDA",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 112.0,
                    "size": 1.0,
                    "opening_reason": "Web research persistence test",
                    "entry_context": {
                        "source": "planner_test",
                        "research_plan": {"selected_tools": [{"tool_name": "web.search"}]},
                        "decision_trace": {
                            "initial_hypothesis": "Investigate NVDA with external confirmation.",
                            "evidence_used": [{"source": "quant", "summary": "Breakout setup"}],
                            "evidence_discarded": [],
                        },
                    },
                },
                purpose="open with enriched context",
            ),
        ],
    )

    results = gateway.execute_plan(session, plan)

    assert results[-1]["tool_name"] == "positions.open"
    assert results[-1]["status"] == "completed"
    assert results[-1]["result"]["ticker"] == "NVDA"
    assert all(isinstance(step["elapsed_ms"], (int, float)) for step in results)

    positions = gateway.position_service.list_positions(session)
    assert len(positions) == 1
    assert positions[0].entry_context["source"] == "planner_test"
    assert "positions.list_open" in positions[0].entry_context["research_execution"]["successful_tools"]
    assert positions[0].entry_context["research_execution"]["total_elapsed_ms"] >= 0
    assert positions[0].entry_context["research_execution"]["slowest_tool"] is not None
    assert positions[0].entry_context["web_research"]["search_results"][0]["title"].endswith("article")
    assert positions[0].entry_context["web_research"]["fetched_articles"][0]["title"] == "NVDA article"
    assert [item["timeframe"] for item in positions[0].entry_context["multitimeframe_visual"]["timeframes"]] == [
        "1M",
        "3M",
        "6M",
        "1Y",
        "5Y",
    ]
    assert positions[0].entry_context["multitimeframe_visual"]["timeframes"][0]["setup_type"] is not None
    assert positions[0].entry_context["research_plan"]["executed_tools"] == [
        "positions.list_open",
        "market.get_multitimeframe_context",
        "web.search",
        "web.fetch_article",
    ]
    assert positions[0].entry_context["decision_trace"]["runtime_tool_outcomes"][0]["tool_name"] == "positions.list_open"
    assert positions[0].entry_context["decision_trace"]["evidence_used"][-1]["source"] == "web.fetch_article"
