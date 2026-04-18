from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.journal import JournalEntry
from app.db.models.position import Position
from app.domains.execution.schemas import PositionCloseRequest, PositionCreate, PositionManageRequest
from app.domains.learning.agent import AgentActionPlan
from app.domains.market.analysis import FusedAnalysisService
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError
from app.providers.web_research import WebResearchError

if TYPE_CHECKING:
    from app.domains.execution.services import PositionService
    from app.domains.learning.macro import MacroContextService
    from app.domains.learning.relevance import StrategyContextAdaptationService
    from app.domains.market.services import CalendarService, MarketDataService, NewsService, WebResearchService
    from app.domains.strategy.services import StrategyScoringService, StrategyService


class AgentToolError(ValueError):
    pass


class AgentToolGatewayService:
    MAX_PLAN_STEPS = 11
    NON_FATAL_TOOL_ERRORS = {
        "calendar.get_ticker_events",
        "calendar.get_macro_events",
        "news.get_ticker_news",
        "web.search",
        "web.fetch_article",
    }
    DUPLICATE_LIMITS = {
        "market.get_snapshot": 1,
        "market.get_chart": 1,
        "market.get_multitimeframe_context": 1,
        "news.get_ticker_news": 1,
        "web.search": 1,
        "web.fetch_article": 1,
        "calendar.get_ticker_events": 1,
        "calendar.get_macro_events": 1,
        "positions.list_open": 1,
        "strategies.list": 1,
        "strategies.list_pipelines": 1,
        "positions.open": 1,
        "positions.manage": 1,
        "positions.close": 1,
    }
    TICKER_EVENT_BLOCK_DAYS = 7
    MACRO_EVENT_BLOCK_DAYS = 3
    HIGH_IMPACT_MACRO_KEYWORDS = (
        "cpi",
        "inflation",
        "fomc",
        "fed",
        "rates",
        "rate decision",
        "payroll",
        "nfp",
        "employment",
        "jobs",
        "pce",
        "gdp",
    )

    def __init__(
        self,
        *,
        market_data_service: "MarketDataService | None" = None,
        news_service: "NewsService | None" = None,
        web_research_service: "WebResearchService | None" = None,
        calendar_service: "CalendarService | None" = None,
        position_service: "PositionService | None" = None,
        macro_context_service: "MacroContextService | None" = None,
        strategy_context_adaptation_service: "StrategyContextAdaptationService | None" = None,
        strategy_service: "StrategyService | None" = None,
        strategy_scoring_service: "StrategyScoringService | None" = None,
        execution_event_source: str = "orchestrator_do",
    ) -> None:
        if market_data_service is None:
            from app.domains.market.services import MarketDataService

            market_data_service = MarketDataService()
        if news_service is None:
            from app.domains.market.services import NewsService

            news_service = NewsService()
        if web_research_service is None:
            from app.domains.market.services import WebResearchService

            web_research_service = WebResearchService()
        if calendar_service is None:
            from app.domains.market.services import CalendarService

            calendar_service = CalendarService()
        if position_service is None:
            from app.domains.execution.services import PositionService

            position_service = PositionService()
        if macro_context_service is None:
            from app.domains.learning.macro import MacroContextService

            macro_context_service = MacroContextService()
        if strategy_context_adaptation_service is None:
            from app.domains.learning.relevance import StrategyContextAdaptationService

            strategy_context_adaptation_service = StrategyContextAdaptationService()
        if strategy_service is None:
            from app.domains.strategy.services import StrategyService

            strategy_service = StrategyService()
        if strategy_scoring_service is None:
            from app.domains.strategy.services import StrategyScoringService

            strategy_scoring_service = StrategyScoringService()

        self.market_data_service = market_data_service
        self.news_service = news_service
        self.web_research_service = web_research_service
        self.calendar_service = calendar_service
        self.position_service = position_service
        self.macro_context_service = macro_context_service
        self.strategy_context_adaptation_service = strategy_context_adaptation_service
        self.strategy_service = strategy_service
        self.strategy_scoring_service = strategy_scoring_service
        self.execution_event_source = execution_event_source
        self.fused_analysis_service = FusedAnalysisService(market_data_service=self.market_data_service)

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "market.get_snapshot",
                "category": "market",
                "description": "Get the current market snapshot and basic technical context for a ticker.",
                "input_schema": {"ticker": "str"},
            },
            {
                "name": "market.get_chart",
                "category": "market",
                "description": "Get a standardized chart snapshot plus visual context for a ticker and timeframe.",
                "input_schema": {"ticker": "str", "timeframe": "str?"},
            },
            {
                "name": "market.get_multitimeframe_context",
                "category": "market",
                "description": "Get chart snapshots and visual context across multiple supported timeframes for a ticker.",
                "input_schema": {"ticker": "str", "timeframes": "list[str]?"},
            },
            {
                "name": "news.get_ticker_news",
                "category": "news",
                "description": "Get recent news for a ticker.",
                "input_schema": {"ticker": "str", "max_results": "int?"},
            },
            {
                "name": "web.search",
                "category": "web",
                "description": "Search the web across allowed research domains and return matching links.",
                "input_schema": {"query": "str", "max_results": "int?", "domains": "list[str]?"},
            },
            {
                "name": "web.fetch_article",
                "category": "web",
                "description": "Fetch and extract readable text from an allowed article URL.",
                "input_schema": {"url": "str?", "search_result_index": "int?", "max_chars": "int?"},
            },
            {
                "name": "macro.get_context",
                "category": "macro",
                "description": "Get the current persisted macro and geopolitical context tracked by the research lab.",
                "input_schema": {"limit": "int?"},
            },
            {
                "name": "calendar.get_ticker_events",
                "category": "calendar",
                "description": "Get upcoming corporate calendar events for a ticker, such as earnings.",
                "input_schema": {"ticker": "str", "days_ahead": "int?"},
            },
            {
                "name": "calendar.get_macro_events",
                "category": "calendar",
                "description": "Get upcoming macro calendar events such as CPI, Fed or employment releases.",
                "input_schema": {"days_ahead": "int?"},
            },
            {
                "name": "positions.list_open",
                "category": "execution",
                "description": "List all currently open positions.",
                "input_schema": {},
            },
            {
                "name": "positions.open",
                "category": "execution",
                "description": "Open a new simulated position and record its opening rationale.",
                "input_schema": {
                    "ticker": "str",
                    "hypothesis_id": "int?",
                    "entry_price": "float",
                    "stop_price": "float?",
                    "target_price": "float?",
                    "size": "float",
                    "thesis": "str?",
                    "setup_id": "int?",
                    "signal_definition_id": "int?",
                    "strategy_version_id": "int?",
                    "analysis_run_id": "int?",
                    "signal_id": "int?",
                    "trade_signal_id": "int?",
                    "entry_context": "dict?",
                    "opening_reason": "str?",
                },
            },
            {
                "name": "positions.manage",
                "category": "execution",
                "description": "Adjust stop, target or thesis for an open position and persist the rationale.",
                "input_schema": {
                    "position_id": "int",
                    "event_type": "str",
                    "observed_price": "float?",
                    "stop_price": "float?",
                    "target_price": "float?",
                    "thesis": "str?",
                    "rationale": "str",
                    "management_context": "dict?",
                    "note": "str?",
                },
            },
            {
                "name": "positions.close",
                "category": "execution",
                "description": "Close an existing position.",
                "input_schema": {
                    "position_id": "int",
                    "exit_price": "float",
                    "exit_reason": "str",
                    "max_drawdown_pct": "float?",
                    "max_runup_pct": "float?",
                    "close_context": "dict?",
                },
            },
            {
                "name": "strategies.list",
                "category": "strategy",
                "description": "List known strategies and their current versions.",
                "input_schema": {},
            },
            {
                "name": "strategies.list_pipelines",
                "category": "strategy",
                "description": "List strategy pipelines with active/candidate/degraded versions and scorecards.",
                "input_schema": {},
            },
        ]

    def execute(self, session: Session, tool_name: str, arguments: dict) -> dict:
        if tool_name == "market.get_snapshot":
            ticker = self._require_str(arguments, "ticker")
            result = asdict(self.market_data_service.get_snapshot(ticker))
            self._record_tool_call(session, tool_name, arguments, result, ticker=ticker)
            return result

        if tool_name == "market.get_chart":
            ticker = self._require_str(arguments, "ticker")
            timeframe = arguments.get("timeframe")
            if timeframe is not None and (not isinstance(timeframe, str) or not timeframe.strip()):
                raise AgentToolError("Argument 'timeframe' must be a non-empty string")
            result = self.fused_analysis_service.build_chart_payload(ticker=ticker, timeframe=timeframe)
            self._record_tool_call(session, tool_name, arguments, result, ticker=ticker)
            return result

        if tool_name == "market.get_multitimeframe_context":
            ticker = self._require_str(arguments, "ticker")
            timeframes = arguments.get("timeframes")
            if timeframes is not None and (
                not isinstance(timeframes, list) or any(not isinstance(item, str) or not item.strip() for item in timeframes)
            ):
                raise AgentToolError("Argument 'timeframes' must be a list of non-empty strings")
            result = self.fused_analysis_service.get_multitimeframe_context(ticker=ticker, timeframes=timeframes)
            self._record_tool_call(session, tool_name, arguments, result, ticker=ticker)
            return result

        if tool_name == "news.get_ticker_news":
            ticker = self._require_str(arguments, "ticker")
            max_results = arguments.get("max_results")
            try:
                result = {
                    "articles": [asdict(article) for article in self.news_service.list_news_for_ticker(ticker, max_results=max_results)]
                }
            except NewsProviderError as exc:
                raise AgentToolError(str(exc)) from exc
            self._record_tool_call(session, tool_name, arguments, result, ticker=ticker)
            return result

        if tool_name == "web.search":
            query = self._require_str(arguments, "query")
            max_results = arguments.get("max_results")
            if max_results is not None and not isinstance(max_results, int):
                raise AgentToolError("Argument 'max_results' must be an integer")
            domains_arg = arguments.get("domains")
            if domains_arg is not None and (
                not isinstance(domains_arg, list) or any(not isinstance(item, str) or not item.strip() for item in domains_arg)
            ):
                raise AgentToolError("Argument 'domains' must be a list of non-empty strings")
            domains = domains_arg if isinstance(domains_arg, list) else None
            try:
                result = {
                    "results": [
                        item.__dict__
                        for item in self.web_research_service.search(query, max_results=max_results, domains=domains)
                    ]
                }
            except WebResearchError as exc:
                raise AgentToolError(str(exc)) from exc
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "web.fetch_article":
            url = self._require_str(arguments, "url")
            max_chars = arguments.get("max_chars")
            if max_chars is not None and not isinstance(max_chars, int):
                raise AgentToolError("Argument 'max_chars' must be an integer")
            try:
                page = self.web_research_service.fetch_article(url, max_chars=max_chars)
                result = page.__dict__
            except WebResearchError as exc:
                raise AgentToolError(str(exc)) from exc
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "macro.get_context":
            limit = arguments.get("limit") if isinstance(arguments.get("limit"), int) else 8
            result = self.macro_context_service.get_context(session, limit=limit).model_dump(mode="json")
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "calendar.get_ticker_events":
            ticker = self._require_str(arguments, "ticker")
            days_ahead = arguments.get("days_ahead") if isinstance(arguments.get("days_ahead"), int) else 21
            try:
                result = {
                    "events": [event.__dict__ for event in self.calendar_service.list_ticker_events(ticker, days_ahead=days_ahead)]
                }
            except CalendarProviderError as exc:
                raise AgentToolError(str(exc)) from exc
            self._record_tool_call(session, tool_name, arguments, result, ticker=ticker)
            return result

        if tool_name == "calendar.get_macro_events":
            days_ahead = arguments.get("days_ahead") if isinstance(arguments.get("days_ahead"), int) else 14
            try:
                result = {"events": [event.__dict__ for event in self.calendar_service.list_macro_events(days_ahead=days_ahead)]}
            except CalendarProviderError as exc:
                raise AgentToolError(str(exc)) from exc
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "positions.list_open":
            positions = [position for position in self.position_service.list_positions(session) if position.status == "open"]
            result = {"positions": [self._position_to_dict(position) for position in positions]}
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "positions.open":
            payload = PositionCreate.model_validate(arguments)
            position = self.position_service.create_position_with_source(
                session,
                payload,
                event_source=self.execution_event_source,
            )
            result = self._position_to_dict(position)
            self._record_tool_call(session, tool_name, arguments, result, ticker=position.ticker, position_id=position.id)
            return result

        if tool_name == "positions.manage":
            position_id = self._require_int(arguments, "position_id")
            payload = PositionManageRequest.model_validate({key: value for key, value in arguments.items() if key != "position_id"})
            position = self.position_service.manage_position_with_source(
                session,
                position_id,
                payload,
                event_source=self.execution_event_source,
            )
            result = self._position_to_dict(position)
            self._record_tool_call(session, tool_name, arguments, result, ticker=position.ticker, position_id=position.id)
            return result

        if tool_name == "positions.close":
            position_id = self._require_int(arguments, "position_id")
            payload = PositionCloseRequest.model_validate({key: value for key, value in arguments.items() if key != "position_id"})
            position = self.position_service.close_position_with_source(
                session,
                position_id,
                payload,
                event_source=self.execution_event_source,
            )
            result = self._position_to_dict(position)
            self._record_tool_call(session, tool_name, arguments, result, ticker=position.ticker, position_id=position.id)
            return result

        if tool_name == "strategies.list":
            result = {"strategies": [self._strategy_to_dict(item) for item in self.strategy_service.list_strategies(session)]}
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        if tool_name == "strategies.list_pipelines":
            result = {
                "pipelines": [
                    item.model_dump(mode="json")
                    for item in self.strategy_scoring_service.list_pipelines(session)
                ]
            }
            self._record_tool_call(session, tool_name, arguments, result)
            return result

        raise AgentToolError(f"Unsupported tool '{tool_name}'")

    def execute_plan(self, session: Session, plan: AgentActionPlan) -> list[dict]:
        self._validate_plan(session, plan)
        results: list[dict] = []
        for index, step in enumerate(plan.steps):
            if step.tool_name is None:
                continue
            step_arguments = dict(step.arguments or {})
            if step.tool_name == "positions.open":
                guard = self._evaluate_entry_guard(session, results, step_arguments)
                if guard is not None:
                    self._record_tool_event(
                        session,
                        tool_name=step.tool_name,
                        arguments=step_arguments,
                        outcome="blocked",
                        observations={
                            "arguments": step_arguments,
                            "guard": guard,
                        },
                        reasoning=f"Blocked tool {step.tool_name}: {guard['summary']}",
                        ticker=step_arguments.get("ticker"),
                    )
                    results.append(
                        {
                            "index": index,
                            "tool_name": step.tool_name,
                            "purpose": step.purpose,
                            "status": "blocked",
                            "result": {
                                "skipped": True,
                                **guard,
                            },
                        }
                    )
                    continue
                entry_context = step_arguments.get("entry_context")
                if not isinstance(entry_context, dict):
                    entry_context = {}
                research_context = self._build_entry_research_context(results)
                visual_context = self._build_entry_visual_context(results)
                research_execution = self._build_research_execution_trace(results)
                enriched_context = dict(entry_context)
                if research_context:
                    enriched_context["web_research"] = research_context
                if visual_context:
                    enriched_context["multitimeframe_visual"] = visual_context
                if research_execution:
                    enriched_context["research_execution"] = research_execution
                    if isinstance(enriched_context.get("research_plan"), dict):
                        enriched_context["research_plan"] = self._merge_research_plan_execution(
                            enriched_context.get("research_plan"),
                            research_execution,
                        )
                    if isinstance(enriched_context.get("decision_trace"), dict):
                        enriched_context["decision_trace"] = self._merge_decision_trace_execution(
                            enriched_context.get("decision_trace"),
                            research_execution,
                        )
                if enriched_context != entry_context:
                    step_arguments["entry_context"] = enriched_context
            try:
                if step.tool_name == "web.fetch_article":
                    step_arguments = self._resolve_fetch_article_arguments(results, step_arguments)
                result = self.execute(session, step.tool_name, step_arguments)
                status = "completed"
            except AgentToolError as exc:
                if step.tool_name not in self.NON_FATAL_TOOL_ERRORS:
                    raise
                result = {"error": str(exc), "events": []}
                status = "error"
                self._record_tool_event(
                    session,
                    tool_name=step.tool_name,
                    arguments=step_arguments,
                    outcome="error",
                    observations={
                        "arguments": step_arguments,
                        "error": str(exc),
                    },
                    reasoning=f"Tool {step.tool_name} failed: {exc}",
                    ticker=step_arguments.get("ticker"),
                )
            results.append(
                {
                    "index": index,
                    "tool_name": step.tool_name,
                    "purpose": step.purpose,
                    "status": status,
                    "result": result,
                }
            )
        return results

    def _validate_plan(self, session: Session, plan: AgentActionPlan) -> None:
        executable_steps = [step for step in plan.steps if step.tool_name is not None]
        if len(executable_steps) > self.MAX_PLAN_STEPS:
            raise AgentToolError(
                f"Plan exceeds maximum length: {len(executable_steps)} steps provided, limit is {self.MAX_PLAN_STEPS}"
            )

        available_tools = {tool["name"] for tool in self.list_tools()}
        counts: dict[str, int] = {}

        for step in executable_steps:
            tool_name = step.tool_name
            if tool_name not in available_tools:
                raise AgentToolError(f"Plan references unsupported tool '{tool_name}'")
            counts[tool_name] = counts.get(tool_name, 0) + 1
            max_duplicates = self.DUPLICATE_LIMITS.get(tool_name)
            if max_duplicates is not None and counts[tool_name] > max_duplicates:
                raise AgentToolError(f"Plan repeats tool '{tool_name}' more than allowed")
            self._validate_step_arguments(session, tool_name, step.arguments or {})

    def _validate_step_arguments(self, session: Session, tool_name: str, arguments: dict) -> None:
        if tool_name == "positions.open":
            payload = PositionCreate.model_validate(arguments)
            existing_position = session.scalar(
                select(Position).where(
                    Position.status == "open",
                    Position.ticker == payload.ticker,
                    Position.strategy_version_id == payload.strategy_version_id,
                )
            )
            if existing_position is not None:
                raise AgentToolError(
                    "Plan would open a duplicate position for the same ticker and strategy version"
                )
            return

        if tool_name == "positions.manage":
            position_id = self._require_int(arguments, "position_id")
            PositionManageRequest.model_validate({key: value for key, value in arguments.items() if key != "position_id"})
            self._require_open_position(session, position_id, tool_name)
            return

        if tool_name == "positions.close":
            position_id = self._require_int(arguments, "position_id")
            PositionCloseRequest.model_validate({key: value for key, value in arguments.items() if key != "position_id"})
            self._require_open_position(session, position_id, tool_name)
            return

        if tool_name == "market.get_snapshot":
            self._require_str(arguments, "ticker")
            return

        if tool_name == "market.get_chart":
            self._require_str(arguments, "ticker")
            timeframe = arguments.get("timeframe")
            if timeframe is not None and (not isinstance(timeframe, str) or not timeframe.strip()):
                raise AgentToolError("Argument 'timeframe' must be a non-empty string")
            return

        if tool_name == "market.get_multitimeframe_context":
            self._require_str(arguments, "ticker")
            timeframes = arguments.get("timeframes")
            if timeframes is not None and (
                not isinstance(timeframes, list) or any(not isinstance(item, str) or not item.strip() for item in timeframes)
            ):
                raise AgentToolError("Argument 'timeframes' must be a list of non-empty strings")
            return

        if tool_name == "news.get_ticker_news":
            self._require_str(arguments, "ticker")
            return

        if tool_name == "web.search":
            self._require_str(arguments, "query")
            max_results = arguments.get("max_results")
            if max_results is not None and not isinstance(max_results, int):
                raise AgentToolError("Argument 'max_results' must be an integer")
            domains = arguments.get("domains")
            if domains is not None and (
                not isinstance(domains, list) or any(not isinstance(item, str) or not item.strip() for item in domains)
            ):
                raise AgentToolError("Argument 'domains' must be a list of non-empty strings")
            return

        if tool_name == "web.fetch_article":
            url = arguments.get("url")
            search_result_index = arguments.get("search_result_index")
            if not isinstance(url, str) or not url.strip():
                if not isinstance(search_result_index, int):
                    raise AgentToolError("web.fetch_article requires either 'url' or 'search_result_index'")
            max_chars = arguments.get("max_chars")
            if max_chars is not None and not isinstance(max_chars, int):
                raise AgentToolError("Argument 'max_chars' must be an integer")
            if search_result_index is not None and not isinstance(search_result_index, int):
                raise AgentToolError("Argument 'search_result_index' must be an integer")
            return

        if tool_name == "macro.get_context":
            limit = arguments.get("limit")
            if limit is not None and not isinstance(limit, int):
                raise AgentToolError("Argument 'limit' must be an integer")
            return

        if tool_name == "calendar.get_ticker_events":
            self._require_str(arguments, "ticker")
            days_ahead = arguments.get("days_ahead")
            if days_ahead is not None and not isinstance(days_ahead, int):
                raise AgentToolError("Argument 'days_ahead' must be an integer")
            return

        if tool_name == "calendar.get_macro_events":
            days_ahead = arguments.get("days_ahead")
            if days_ahead is not None and not isinstance(days_ahead, int):
                raise AgentToolError("Argument 'days_ahead' must be an integer")
            return

    def _evaluate_entry_guard(self, session: Session, prior_results: list[dict], arguments: dict) -> dict | None:
        ticker = self._require_str(arguments, "ticker")
        entry_context = arguments.get("entry_context") if isinstance(arguments.get("entry_context"), dict) else {}
        strategy_rules = self._extract_strategy_rules(entry_context)
        ticker_event_block_days = self._coerce_optional_positive_int(
            strategy_rules.get("avoid_near_earnings_days")
        ) or self.TICKER_EVENT_BLOCK_DAYS
        macro_event_block_days = self._coerce_optional_positive_int(
            strategy_rules.get("avoid_near_macro_days")
        ) or self.MACRO_EVENT_BLOCK_DAYS
        calendar_errors: list[str] = []
        corporate_events: list[dict] = []
        macro_events: list[dict] = []
        today = date.today()

        for step in prior_results:
            if step.get("tool_name") == "calendar.get_ticker_events":
                result = step.get("result", {})
                error = result.get("error")
                if isinstance(error, str) and error:
                    calendar_errors.append(error)
                    continue
                for event in result.get("events", []):
                    days_until = self._days_until_event(event, today)
                    if days_until is None or days_until > ticker_event_block_days:
                        continue
                    if event.get("ticker") and str(event.get("ticker")).upper() != ticker.upper():
                        continue
                    corporate_events.append(self._summarize_calendar_event(event, days_until))

            if step.get("tool_name") == "calendar.get_macro_events":
                result = step.get("result", {})
                error = result.get("error")
                if isinstance(error, str) and error:
                    calendar_errors.append(error)
                    continue
                for event in result.get("events", []):
                    days_until = self._days_until_event(event, today)
                    if days_until is None or days_until > macro_event_block_days:
                        continue
                    if not self._is_relevant_macro_event(event):
                        continue
                    macro_events.append(self._summarize_calendar_event(event, days_until))

        if calendar_errors:
            return {
                "reason": "calendar_check_failed",
                "summary": "Calendar checks failed, so the entry was left on watch.",
                "calendar_errors": calendar_errors,
                "corporate_events": [],
                "macro_events": [],
            }

        if not corporate_events and not macro_events:
            scoring_guard = self._evaluate_scoring_guard(arguments=arguments)
            if scoring_guard is not None:
                return scoring_guard
            risk_budget_guard = self._evaluate_risk_budget_guard(arguments=arguments)
            if risk_budget_guard is not None:
                return risk_budget_guard
            portfolio_guard = self._evaluate_portfolio_guard(
                session=session,
                ticker=ticker,
                strategy_version_id=arguments.get("strategy_version_id"),
                strategy_rules=strategy_rules,
            )
            if portfolio_guard is not None:
                return portfolio_guard
            learned_rule_guard = self._evaluate_learned_rule_guard(session=session, arguments=arguments)
            if learned_rule_guard is not None:
                return learned_rule_guard
            return None

        summary_parts: list[str] = []
        if corporate_events:
            summary_parts.append(
                "near corporate event: "
                + ", ".join(f"{event['title']} ({event['event_date']})" for event in corporate_events[:2])
            )
        if macro_events:
            summary_parts.append(
                "near macro event: "
                + ", ".join(f"{event['title']} ({event['event_date']})" for event in macro_events[:2])
            )

        return {
            "reason": "calendar_risk",
            "summary": "Entry blocked by calendar guard due to " + " and ".join(summary_parts) + ".",
            "calendar_errors": [],
            "corporate_events": corporate_events,
            "macro_events": macro_events,
        }

    def _evaluate_learned_rule_guard(self, session: Session | None, arguments: dict) -> dict | None:
        if session is None:
            return None
        strategy_version_id = arguments.get("strategy_version_id")
        entry_context = arguments.get("entry_context")
        if not isinstance(strategy_version_id, int) or not isinstance(entry_context, dict):
            return None
        signal_payload = {
            "quant_summary": dict(entry_context.get("quant_summary") or {}),
            "visual_summary": dict(entry_context.get("visual_summary") or {}),
            "risk_reward": entry_context.get("risk_reward"),
            "ai_overlay": dict(entry_context.get("ai_overlay") or {}),
            "decision_context": dict(entry_context.get("decision_context") or {}),
        }
        return self.strategy_context_adaptation_service.evaluate_entry(
            session,
            strategy_version_id=strategy_version_id,
            signal_payload=signal_payload,
        )

    @staticmethod
    def _evaluate_risk_budget_guard(arguments: dict) -> dict | None:
        entry_context = arguments.get("entry_context")
        if not isinstance(entry_context, dict):
            return None
        risk_budget = entry_context.get("risk_budget")
        position_sizing = entry_context.get("position_sizing")
        if isinstance(risk_budget, dict):
            kill_switch = risk_budget.get("kill_switch")
            if isinstance(kill_switch, dict) and kill_switch.get("triggered"):
                reasons = [
                    str(item)
                    for item in kill_switch.get("reasons", [])
                    if isinstance(item, str) and str(item).strip()
                ]
                return {
                    "reason": "risk_budget_limit",
                    "summary": "Entry blocked by risk budget guard due to " + "; ".join(reasons or ["active kill switch"]) + ".",
                    "guard_reasons": reasons,
                }
            exposure_block_reasons = [
                str(item)
                for item in risk_budget.get("exposure_block_reasons", [])
                if isinstance(item, str) and str(item).strip()
            ]
            if exposure_block_reasons:
                return {
                    "reason": "risk_budget_limit",
                    "summary": "Entry blocked by aggregate risk guard due to " + "; ".join(exposure_block_reasons) + ".",
                    "guard_reasons": exposure_block_reasons,
                }
        if isinstance(position_sizing, dict) and position_sizing.get("status") == "blocked":
            reasons = [
                str(item)
                for item in position_sizing.get("reasons", [])
                if isinstance(item, str) and str(item).strip()
            ]
            return {
                "reason": "risk_budget_limit",
                "summary": "Entry blocked by position sizing guard due to " + "; ".join(reasons or ["blocked sizing"]) + ".",
                "guard_reasons": reasons,
            }
        return None

    @staticmethod
    def _evaluate_scoring_guard(arguments: dict) -> dict | None:
        entry_context = arguments.get("entry_context")
        if not isinstance(entry_context, dict):
            return None
        guard_results = entry_context.get("guard_results")
        if not isinstance(guard_results, dict) or not guard_results.get("blocked"):
            return None
        reasons = guard_results.get("reasons")
        reason_list = [str(item) for item in reasons if isinstance(item, str)] if isinstance(reasons, list) else []
        guard_types = {
            str(item).strip()
            for item in guard_results.get("types", [])
            if isinstance(item, str) and str(item).strip()
        }
        if "learned_rule" in guard_types:
            return {
                "reason": "strategy_context_rule",
                "summary": reason_list[0] if reason_list else "Entry blocked by learned strategy context rule.",
                "guard_reasons": reason_list,
            }
        if "regime_policy" in guard_types:
            return {
                "reason": "regime_policy",
                "summary": "Entry blocked by regime policy due to " + "; ".join(reason_list or ["active regime policy"]) + ".",
                "guard_reasons": reason_list,
            }
        if "portfolio_limit" in guard_types:
            return {
                "reason": "portfolio_limit",
                "summary": "Entry blocked by portfolio guard due to " + "; ".join(reason_list or ["active guard"]) + ".",
                "guard_reasons": reason_list,
            }
        if "risk_budget" in guard_types:
            return {
                "reason": "risk_budget_limit",
                "summary": "Entry blocked by risk budget guard due to " + "; ".join(reason_list or ["active guard"]) + ".",
                "guard_reasons": reason_list,
            }
        return {
            "reason": "decision_layer_guard",
            "summary": "Entry blocked by deterministic decision layer due to " + "; ".join(reason_list or ["active guard"]) + ".",
            "guard_reasons": reason_list,
        }

    @staticmethod
    def _extract_strategy_rules(entry_context: dict) -> dict:
        nested_context = entry_context.get("decision_context")
        if isinstance(nested_context, dict):
            nested_rules = nested_context.get("strategy_rules")
            if isinstance(nested_rules, dict):
                return dict(nested_rules)
        direct_rules = entry_context.get("strategy_rules")
        if isinstance(direct_rules, dict):
            return dict(direct_rules)
        return {}

    def _evaluate_portfolio_guard(
        self,
        *,
        session: Session | None,
        ticker: str,
        strategy_version_id: object,
        strategy_rules: dict,
    ) -> dict | None:
        if session is None:
            return None
        max_total = self._coerce_optional_positive_int(strategy_rules.get("max_open_positions_total"))
        max_same_ticker = self._coerce_optional_positive_int(strategy_rules.get("max_same_ticker_positions"))
        max_same_strategy = self._coerce_optional_positive_int(strategy_rules.get("max_same_strategy_open_positions"))
        if max_total is None and max_same_ticker is None and max_same_strategy is None:
            return None

        open_positions = list(session.scalars(select(Position).where(Position.status == "open")).all())
        same_ticker_open = [position for position in open_positions if position.ticker.upper() == ticker.upper()]
        same_strategy_open = [
            position
            for position in open_positions
            if isinstance(strategy_version_id, int) and position.strategy_version_id == strategy_version_id
        ]

        if max_total is not None and len(open_positions) >= max_total:
            return {
                "reason": "portfolio_limit",
                "summary": (
                    f"Entry blocked by portfolio limit because there are already {len(open_positions)} open positions "
                    f"and the strategy maximum is {max_total}."
                ),
                "open_positions_total": len(open_positions),
                "max_open_positions_total": max_total,
            }
        if max_same_ticker is not None and len(same_ticker_open) >= max_same_ticker:
            return {
                "reason": "portfolio_limit",
                "summary": (
                    f"Entry blocked by ticker exposure limit because {ticker.upper()} already has "
                    f"{len(same_ticker_open)} open position(s) and the strategy maximum is {max_same_ticker}."
                ),
                "same_ticker_open_positions": len(same_ticker_open),
                "max_same_ticker_positions": max_same_ticker,
            }
        if max_same_strategy is not None and len(same_strategy_open) >= max_same_strategy:
            return {
                "reason": "portfolio_limit",
                "summary": (
                    f"Entry blocked by strategy exposure limit because this strategy version already has "
                    f"{len(same_strategy_open)} open position(s) and the maximum is {max_same_strategy}."
                ),
                "same_strategy_open_positions": len(same_strategy_open),
                "max_same_strategy_open_positions": max_same_strategy,
            }
        return None

    @staticmethod
    def _coerce_optional_positive_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value.is_integer() and value > 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _resolve_fetch_article_arguments(prior_results: list[dict], arguments: dict) -> dict:
        if isinstance(arguments.get("url"), str) and arguments["url"].strip():
            return arguments
        search_result_index = arguments.get("search_result_index")
        if not isinstance(search_result_index, int):
            raise AgentToolError("web.fetch_article requires either 'url' or 'search_result_index'")

        latest_search_result = next(
            (step.get("result", {}) for step in reversed(prior_results) if step.get("tool_name") == "web.search"),
            None,
        )
        if latest_search_result is None:
            raise AgentToolError("web.fetch_article requires a prior web.search result in the plan")
        results = latest_search_result.get("results", [])
        if not isinstance(results, list) or search_result_index < 0 or search_result_index >= len(results):
            raise AgentToolError("web.fetch_article references an out-of-range search_result_index")
        selected = results[search_result_index]
        url = selected.get("url")
        if not isinstance(url, str) or not url.strip():
            raise AgentToolError("web.fetch_article could not resolve a valid URL from prior web.search results")
        return {
            **arguments,
            "url": url,
        }

    @staticmethod
    def _build_entry_research_context(prior_results: list[dict]) -> dict:
        search_results: list[dict] = []
        fetched_articles: list[dict] = []

        for step in prior_results:
            if step.get("tool_name") == "web.search":
                for item in step.get("result", {}).get("results", [])[:3]:
                    search_results.append(
                        {
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "source": item.get("source"),
                            "snippet": item.get("snippet"),
                        }
                    )
            if step.get("tool_name") == "web.fetch_article":
                result = step.get("result", {})
                text = result.get("text")
                fetched_articles.append(
                    {
                        "title": result.get("title"),
                        "url": result.get("url"),
                        "source": result.get("source"),
                        "excerpt": text[:500] if isinstance(text, str) else None,
                    }
                )

        payload = {
            "search_results": search_results[:3],
            "fetched_articles": fetched_articles[:2],
        }
        if not payload["search_results"] and not payload["fetched_articles"]:
            return {}
        return payload

    @staticmethod
    def _build_entry_visual_context(prior_results: list[dict]) -> dict:
        latest_context = next(
            (
                step.get("result", {})
                for step in reversed(prior_results)
                if step.get("tool_name") == "market.get_multitimeframe_context"
            ),
            None,
        )
        if not isinstance(latest_context, dict):
            return {}
        charts = latest_context.get("charts")
        if not isinstance(charts, list) or not charts:
            return {}

        summaries: list[dict] = []
        for chart in charts:
            if not isinstance(chart, dict):
                continue
            visual_summary = chart.get("visual_summary") if isinstance(chart.get("visual_summary"), dict) else {}
            quant_summary = chart.get("quant_summary") if isinstance(chart.get("quant_summary"), dict) else {}
            summaries.append(
                {
                    "timeframe": chart.get("timeframe"),
                    "decision": chart.get("decision"),
                    "combined_score": chart.get("combined_score"),
                    "setup_type": visual_summary.get("setup_type"),
                    "visual_score": visual_summary.get("visual_score"),
                    "setup_quality": visual_summary.get("setup_quality"),
                    "structure_clarity": visual_summary.get("structure_clarity"),
                    "trend": quant_summary.get("trend"),
                    "risk_reward": quant_summary.get("risk_reward"),
                }
            )

        if not summaries:
            return {}
        return {
            "ticker": latest_context.get("ticker"),
            "timeframes": summaries,
        }

    @staticmethod
    def _build_research_execution_trace(prior_results: list[dict]) -> dict:
        tool_outcomes: list[dict] = []
        evidence_used: list[dict] = []
        evidence_discarded: list[dict] = []
        successful_tools: list[str] = []
        errored_tools: list[str] = []

        for step in prior_results:
            tool_name = step.get("tool_name")
            if not isinstance(tool_name, str) or tool_name in {"positions.open", "positions.manage", "positions.close"}:
                continue
            status = str(step.get("status") or "completed")
            result = step.get("result") if isinstance(step.get("result"), dict) else {}
            summary, used = AgentToolGatewayService._summarize_research_tool_result(tool_name, status, result)
            outcome = {
                "tool_name": tool_name,
                "status": status,
                "used": used,
                "summary": summary,
            }
            tool_outcomes.append(outcome)
            if status == "error":
                errored_tools.append(tool_name)
            else:
                successful_tools.append(tool_name)
            if used:
                evidence_used.append({"source": tool_name, "summary": summary})
            else:
                evidence_discarded.append({"source": tool_name, "summary": summary})

        if not tool_outcomes:
            return {}
        return {
            "tool_outcomes": tool_outcomes,
            "successful_tools": successful_tools,
            "errored_tools": errored_tools,
            "evidence_used": evidence_used,
            "evidence_discarded": evidence_discarded,
        }

    @staticmethod
    def _summarize_research_tool_result(tool_name: str, status: str, result: dict) -> tuple[str, bool]:
        if status == "error":
            return (str(result.get("error") or f"{tool_name} failed during execution"), False)
        if tool_name == "market.get_snapshot":
            return (f"Refreshed market snapshot at price {result.get('price')}.", True)
        if tool_name == "calendar.get_ticker_events":
            events = result.get("events") if isinstance(result.get("events"), list) else []
            if events:
                return (f"Found {len(events)} corporate calendar event(s).", True)
            return ("No near-term corporate calendar events were returned.", False)
        if tool_name == "calendar.get_macro_events":
            events = result.get("events") if isinstance(result.get("events"), list) else []
            if events:
                return (f"Found {len(events)} macro calendar event(s).", True)
            return ("No near-term macro calendar events were returned.", False)
        if tool_name == "market.get_multitimeframe_context":
            charts = result.get("charts") if isinstance(result.get("charts"), list) else []
            if charts:
                return (f"Collected multi-timeframe context across {len(charts)} timeframe(s).", True)
            return ("Multi-timeframe context returned no usable charts.", False)
        if tool_name == "macro.get_context":
            regimes = result.get("active_regimes") if isinstance(result.get("active_regimes"), list) else []
            if regimes:
                return ("Active macro regimes: " + ", ".join(str(item) for item in regimes[:3]), True)
            return ("Macro context returned no active regimes.", False)
        if tool_name == "news.get_ticker_news":
            articles = result.get("articles") if isinstance(result.get("articles"), list) else []
            if articles:
                return (f"Loaded {len(articles)} ticker-news article(s).", True)
            return ("Ticker-news lookup returned no articles.", False)
        if tool_name == "web.search":
            results = result.get("results") if isinstance(result.get("results"), list) else []
            if results:
                return (f"Web search returned {len(results)} external result(s).", True)
            return ("Web search returned no external confirmation.", False)
        if tool_name == "web.fetch_article":
            text = result.get("text")
            title = result.get("title")
            if isinstance(text, str) and text.strip():
                return (f"Fetched article '{title or 'untitled'}'.", True)
            return ("External article fetch returned no readable text.", False)
        if tool_name == "positions.list_open":
            positions = result.get("positions") if isinstance(result.get("positions"), list) else []
            return (f"Reviewed {len(positions)} open position(s).", True)
        if tool_name == "strategies.list_pipelines":
            pipelines = result.get("pipelines") if isinstance(result.get("pipelines"), list) else []
            return (f"Reviewed {len(pipelines)} strategy pipeline(s).", True)
        return (f"Executed {tool_name}.", True)

    @staticmethod
    def _merge_research_plan_execution(research_plan: dict, research_execution: dict) -> dict:
        merged = dict(research_plan)
        merged["executed_tools"] = list(research_execution.get("successful_tools") or [])
        merged["errored_tools"] = list(research_execution.get("errored_tools") or [])
        merged["tool_outcomes"] = list(research_execution.get("tool_outcomes") or [])
        return merged

    @staticmethod
    def _merge_decision_trace_execution(decision_trace: dict, research_execution: dict) -> dict:
        merged = dict(decision_trace)
        existing_used = merged.get("evidence_used") if isinstance(merged.get("evidence_used"), list) else []
        existing_discarded = (
            merged.get("evidence_discarded") if isinstance(merged.get("evidence_discarded"), list) else []
        )
        merged["evidence_used"] = existing_used + list(research_execution.get("evidence_used") or [])
        merged["evidence_discarded"] = existing_discarded + list(research_execution.get("evidence_discarded") or [])
        merged["runtime_tool_outcomes"] = list(research_execution.get("tool_outcomes") or [])
        return merged

    @staticmethod
    def _require_open_position(session: Session, position_id: int, tool_name: str) -> Position:
        position = session.get(Position, position_id)
        if position is None:
            raise AgentToolError(f"Plan references unknown position_id={position_id} for tool '{tool_name}'")
        if position.status != "open":
            raise AgentToolError(f"Plan references non-open position_id={position_id} for tool '{tool_name}'")
        return position

    @classmethod
    def _record_tool_call(
        cls,
        session: Session,
        tool_name: str,
        arguments: dict,
        result: dict,
        *,
        ticker: str | None = None,
        position_id: int | None = None,
    ) -> None:
        cls._record_tool_event(
            session,
            tool_name=tool_name,
            arguments=arguments,
            outcome="completed",
            observations={
                "arguments": arguments,
                "result_summary": cls._summarize_result(result),
            },
            reasoning=f"Executed tool {tool_name}",
            ticker=ticker,
            position_id=position_id,
        )

    @staticmethod
    def _record_tool_event(
        session: Session,
        tool_name: str,
        arguments: dict,
        outcome: str,
        observations: dict,
        reasoning: str,
        *,
        ticker: str | None = None,
        position_id: int | None = None,
    ) -> None:
        session.add(
            JournalEntry(
                entry_type="agent_tool_call",
                ticker=ticker,
                position_id=position_id,
                market_context={"tool_name": tool_name},
                observations=observations,
                reasoning=reasoning,
                decision=tool_name,
                outcome=outcome,
            )
        )
        session.commit()

    @staticmethod
    def _summarize_result(result: dict) -> dict:
        if "positions" in result:
            return {"positions_count": len(result["positions"])}
        if "articles" in result:
            return {"articles_count": len(result["articles"])}
        if "results" in result:
            return {"results_count": len(result["results"])}
        if "chart_svg" in result:
            return {"ticker": result.get("ticker"), "timeframe": result.get("timeframe"), "visible_candles": result.get("visible_candles")}
        if "charts" in result:
            return {"ticker": result.get("ticker"), "charts_count": len(result["charts"])}
        if "text" in result:
            return {"title": result.get("title"), "text_chars": len(result.get("text", ""))}
        if "signals" in result:
            return {"signals_count": len(result["signals"]), "active_regimes": result.get("active_regimes", [])}
        if "events" in result:
            return {"events_count": len(result["events"])}
        if "strategies" in result:
            return {"strategies_count": len(result["strategies"])}
        if "pipelines" in result:
            return {"pipelines_count": len(result["pipelines"])}
        if "id" in result:
            return {"entity_id": result["id"], "status": result.get("status")}
        return {"keys": sorted(result.keys())}

    @staticmethod
    def _require_str(arguments: dict, key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AgentToolError(f"Argument '{key}' must be a non-empty string")
        return value

    @staticmethod
    def _require_int(arguments: dict, key: str) -> int:
        value = arguments.get(key)
        if not isinstance(value, int):
            raise AgentToolError(f"Argument '{key}' must be an integer")
        return value

    @staticmethod
    def _days_until_event(event: dict, today: date) -> int | None:
        raw_date = event.get("event_date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            return None
        try:
            event_date = date.fromisoformat(raw_date[:10])
        except ValueError:
            return None
        days_until = (event_date - today).days
        if days_until < 0:
            return None
        return days_until

    @classmethod
    def _is_relevant_macro_event(cls, event: dict) -> bool:
        impact = str(event.get("impact") or "").strip().lower()
        if impact == "high":
            return True
        title = str(event.get("title") or "").strip().lower()
        return any(keyword in title for keyword in cls.HIGH_IMPACT_MACRO_KEYWORDS)

    @staticmethod
    def _summarize_calendar_event(event: dict, days_until: int) -> dict:
        return {
            "title": event.get("title"),
            "event_date": event.get("event_date"),
            "impact": event.get("impact"),
            "ticker": event.get("ticker"),
            "days_until": days_until,
        }

    @staticmethod
    def _position_to_dict(position) -> dict:
        return {
            "id": position.id,
            "ticker": position.ticker,
            "signal_id": position.signal_id,
            "trade_signal_id": position.trade_signal_id,
            "strategy_version_id": position.strategy_version_id,
            "status": position.status,
            "entry_price": position.entry_price,
            "stop_price": position.stop_price,
            "target_price": position.target_price,
            "exit_price": position.exit_price,
            "exit_reason": position.exit_reason,
            "thesis": position.thesis,
            "review_status": position.review_status,
            "events": [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "event_time": event.event_time.isoformat() if hasattr(event.event_time, "isoformat") else event.event_time,
                    "payload": event.payload,
                    "note": event.note,
                }
                for event in position.events
            ],
        }

    @staticmethod
    def _strategy_to_dict(strategy) -> dict:
        return {
            "id": strategy.id,
            "code": strategy.code,
            "name": strategy.name,
            "status": strategy.status,
            "current_version_id": strategy.current_version_id,
            "versions": [
                {
                    "id": version.id,
                    "version": version.version,
                    "state": version.state,
                    "lifecycle_stage": version.lifecycle_stage,
                    "hypothesis": version.hypothesis,
                }
                for version in strategy.versions
            ],
        }
