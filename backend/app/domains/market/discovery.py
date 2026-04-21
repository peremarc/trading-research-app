from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.position import Position
from app.domains.learning.macro import MacroContextService
from app.domains.market.services import CalendarService, MarketDataService, NewsService, SignalService
from app.domains.strategy.schemas import WatchlistItemCreate
from app.domains.strategy.services import WatchlistService
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError


class OpportunityDiscoveryService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        signal_service: SignalService | None = None,
        watchlist_service: WatchlistService | None = None,
        news_service: NewsService | None = None,
        calendar_service: CalendarService | None = None,
        macro_context_service: MacroContextService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.market_data_service = market_data_service or MarketDataService()
        self.signal_service = signal_service or SignalService()
        self.watchlist_service = watchlist_service or WatchlistService()
        self.news_service = news_service or NewsService()
        self.calendar_service = calendar_service or CalendarService()
        self.macro_context_service = macro_context_service or MacroContextService()

    def refresh_active_watchlists(self, session: Session) -> dict:
        if not self.settings.opportunity_discovery_enabled:
            return {
                "watchlists_scanned": 0,
                "universe_size": 0,
                "discovered_items": 0,
                "top_candidates": [],
                "benchmark_ticker": self.settings.benchmark_ticker,
                "universe_source": "disabled",
                "scanner_types_used": [],
            }

        watchlists = [watchlist for watchlist in self.watchlist_service.list_watchlists(session) if watchlist.status == "active"]
        universe, universe_source, scanner_types_used = self._resolve_universe()
        benchmark = self.market_data_service.get_snapshot(self.settings.benchmark_ticker)
        macro_context = self.macro_context_service.get_context(session, limit=6)
        macro_tracked_tickers = {
            ticker.strip().upper()
            for ticker in macro_context.tracked_tickers
            if isinstance(ticker, str) and ticker.strip()
        }
        discovered_items = 0
        top_candidates: list[dict] = []
        tracked_tickers = {item.ticker.upper() for watchlist in watchlists for item in watchlist.items}

        open_tickers = {
            ticker.upper()
            for ticker in session.query(Position.ticker).filter(Position.status == "open").all()
            for ticker in ticker
            if ticker is not None
        }

        for watchlist in watchlists:
            existing = {item.ticker.upper() for item in watchlist.items}
            candidates = []
            enriched_candidates = []
            soft_floor = max(self.settings.opportunity_discovery_min_score - 0.08, 0.0)
            enrichment_limit = max(int(self.settings.opportunity_discovery_per_watchlist) * 3, int(self.settings.opportunity_discovery_per_watchlist))

            for ticker in universe:
                if ticker in existing or ticker in tracked_tickers or ticker in open_tickers:
                    continue

                snapshot = self.market_data_service.get_snapshot(ticker)
                signal = self.signal_service.analyze_ticker(ticker)
                base_score = self._coerce_score(signal.get("combined_score"))
                if base_score < soft_floor:
                    continue

                candidates.append(
                    {
                        "ticker": ticker,
                        "snapshot": snapshot,
                        "signal": signal,
                        "base_score": base_score,
                    }
                )

            candidates.sort(key=lambda item: item["base_score"], reverse=True)
            for candidate in candidates[:enrichment_limit]:
                context = self._build_candidate_context(
                    ticker=candidate["ticker"],
                    macro_tracked_tickers=macro_tracked_tickers,
                )
                discovery_score = self._compose_discovery_score(
                    base_score=candidate["base_score"],
                    relative_volume=self._coerce_score(candidate["signal"].get("quant_summary", {}).get("relative_volume")),
                    month_performance=getattr(candidate["snapshot"], "month_performance", None),
                    context=context,
                )
                if discovery_score < self.settings.opportunity_discovery_min_score:
                    continue
                enriched_candidates.append(
                    {
                        **candidate,
                        "context": context,
                        "discovery_score": discovery_score,
                    }
                )

            enriched_candidates.sort(
                key=lambda item: (
                    item["discovery_score"],
                    item["base_score"],
                ),
                reverse=True,
            )
            selected = enriched_candidates[: self.settings.opportunity_discovery_per_watchlist]

            for candidate in selected:
                snapshot = candidate["snapshot"]
                signal = candidate["signal"]
                context = candidate["context"]
                self.watchlist_service.add_item(
                    session,
                    watchlist.id,
                    WatchlistItemCreate(
                        ticker=candidate["ticker"],
                        strategy_hypothesis=watchlist.hypothesis,
                        score=candidate["discovery_score"],
                        reason=self._build_candidate_reason(
                            ticker=candidate["ticker"],
                            base_score=candidate["base_score"],
                            discovery_score=candidate["discovery_score"],
                            alpha_gap_pct=self._coerce_score(signal.get("quant_summary", {}).get("alpha_gap_pct_20")),
                            context=context,
                        ),
                        key_metrics={
                            "source": "opportunity_discovery",
                            "benchmark_ticker": self.settings.benchmark_ticker,
                            "alpha_gap_pct": signal["quant_summary"]["alpha_gap_pct_20"],
                            "risk_reward": signal["risk_reward"],
                            "base_combined_score": candidate["base_score"],
                            "discovery_score": candidate["discovery_score"],
                            "news_titles": context["news_titles"],
                            "calendar_events": context["event_titles"],
                            "macro_tracked": context["macro_tracked"],
                            "contextual_bonus": context["contextual_bonus"],
                            "contextual_reasons": context["contextual_reasons"],
                            "month_performance": snapshot.month_performance,
                            "benchmark_month_performance": benchmark.month_performance,
                        },
                        state="watching",
                    ),
                    event_source="opportunity_discovery",
                )
                discovered_items += 1
                existing.add(candidate["ticker"])
                tracked_tickers.add(candidate["ticker"])
                top_candidates.append(
                    {
                        "watchlist_id": watchlist.id,
                        "ticker": candidate["ticker"],
                        "score": candidate["discovery_score"],
                        "base_score": candidate["base_score"],
                        "alpha_gap_pct": signal["quant_summary"]["alpha_gap_pct_20"],
                        "macro_tracked": context["macro_tracked"],
                        "news_count": len(context["news_titles"]),
                        "event_count": len(context["event_titles"]),
                    }
                )

        top_candidates.sort(key=lambda item: item["score"], reverse=True)
        return {
            "watchlists_scanned": len(watchlists),
            "universe_size": len(universe),
            "discovered_items": discovered_items,
            "top_candidates": top_candidates[:5],
            "benchmark_ticker": self.settings.benchmark_ticker,
            "universe_source": universe_source,
            "scanner_types_used": scanner_types_used,
        }

    def get_candidate_universe(self) -> dict:
        universe, universe_source, scanner_types_used = self._resolve_universe()
        return {
            "universe": universe,
            "universe_source": universe_source,
            "scanner_types_used": scanner_types_used,
        }

    def _resolve_universe(self) -> tuple[list[str], str, list[str]]:
        fallback_universe = self._parse_universe(self.settings.opportunity_discovery_universe)
        universe_source = str(getattr(self.settings, "opportunity_discovery_universe_source", "configured_list")).strip().lower()
        if universe_source != "ibkr_scanner":
            return fallback_universe, "configured_list", []

        provider = getattr(self.market_data_service, "provider", None)
        get_scanner_universe = getattr(provider, "get_scanner_universe", None)
        if not callable(get_scanner_universe):
            return fallback_universe, "configured_list_fallback", []

        scanner_types = self._parse_universe(self.settings.opportunity_discovery_scanner_types)
        if not scanner_types:
            return fallback_universe, "configured_list_fallback", []

        try:
            universe = get_scanner_universe(
                scanner_types,
                instrument=self.settings.opportunity_discovery_scanner_instrument,
                location=self.settings.opportunity_discovery_scanner_location,
                filters=self._parse_scanner_filters(self.settings.opportunity_discovery_scanner_filters_json),
                limit=self.settings.opportunity_discovery_universe_limit,
            )
        except (RuntimeError, TypeError, ValueError):
            return fallback_universe, "configured_list_fallback", []

        normalized_universe = [ticker.strip().upper() for ticker in universe if isinstance(ticker, str) and ticker.strip()]
        if normalized_universe:
            return normalized_universe, "ibkr_scanner", scanner_types
        return fallback_universe, "configured_list_fallback", scanner_types

    @staticmethod
    def _parse_universe(raw_universe: str) -> list[str]:
        return [ticker.strip().upper() for ticker in raw_universe.split(",") if ticker.strip()]

    @staticmethod
    def _parse_scanner_filters(raw_filters: str) -> list[dict]:
        if not raw_filters.strip():
            return []
        try:
            payload = json.loads(raw_filters)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _build_candidate_context(
        self,
        *,
        ticker: str,
        macro_tracked_tickers: set[str],
    ) -> dict:
        news_titles = self._list_news_titles(ticker)
        event_titles = self._list_event_titles(ticker)
        macro_tracked = ticker.upper() in macro_tracked_tickers
        contextual_bonus = 0.0
        contextual_reasons: list[str] = []
        if news_titles:
            contextual_bonus += 0.03
            contextual_reasons.append("fresh_news")
        if event_titles:
            contextual_bonus += 0.02
            contextual_reasons.append("corporate_event")
        if macro_tracked:
            contextual_bonus += 0.04
            contextual_reasons.append("macro_theme_alignment")
        return {
            "news_titles": news_titles[:3],
            "event_titles": event_titles[:3],
            "macro_tracked": macro_tracked,
            "contextual_bonus": round(contextual_bonus, 2),
            "contextual_reasons": contextual_reasons,
        }

    def _compose_discovery_score(
        self,
        *,
        base_score: float,
        relative_volume: float | None,
        month_performance: float | None,
        context: dict,
    ) -> float:
        return round(
            min(
                max(
                    float(base_score)
                    + min(max(float(relative_volume or 0.0), 0.0), 3.0) * 0.03
                    + min(max(float(month_performance or 0.0), -0.2), 0.2) * 0.2
                    + float(context.get("contextual_bonus") or 0.0),
                    0.0,
                ),
                1.0,
            ),
            2,
        )

    def _build_candidate_reason(
        self,
        *,
        ticker: str,
        base_score: float,
        discovery_score: float,
        alpha_gap_pct: float | None,
        context: dict,
    ) -> str:
        reasons = list(context.get("contextual_reasons") or [])
        catalysts = ", ".join(reason.replace("_", " ") for reason in reasons) if reasons else "technical strength only"
        alpha_gap_text = f"{alpha_gap_pct:.2f}%" if isinstance(alpha_gap_pct, (int, float)) else "n/a"
        return (
            f"Autonomous discovery candidate {ticker.upper()} scored {discovery_score:.2f} "
            f"(base {base_score:.2f}) with alpha gap {alpha_gap_text} vs {self.settings.benchmark_ticker}; "
            f"catalysts: {catalysts}."
        )

    def _list_news_titles(self, ticker: str) -> list[str]:
        try:
            articles = self.news_service.list_news_for_ticker(ticker, max_results=3)
        except NewsProviderError:
            return []
        return [
            str(article.title).strip()
            for article in articles
            if getattr(article, "title", None)
        ]

    def _list_event_titles(self, ticker: str) -> list[str]:
        try:
            events = self.calendar_service.list_ticker_events(ticker, days_ahead=21)
        except CalendarProviderError:
            return []
        return [
            f"{event.event_type}:{event.event_date}"
            for event in events
            if getattr(event, "event_type", None) and getattr(event, "event_date", None)
        ]

    @staticmethod
    def _coerce_score(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
