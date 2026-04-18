from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.position import Position
from app.domains.market.services import MarketDataService, SignalService
from app.domains.strategy.schemas import WatchlistItemCreate
from app.domains.strategy.services import WatchlistService


class OpportunityDiscoveryService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        signal_service: SignalService | None = None,
        watchlist_service: WatchlistService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.market_data_service = market_data_service or MarketDataService()
        self.signal_service = signal_service or SignalService()
        self.watchlist_service = watchlist_service or WatchlistService()

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

            for ticker in universe:
                if ticker in existing or ticker in tracked_tickers or ticker in open_tickers:
                    continue

                snapshot = self.market_data_service.get_snapshot(ticker)
                signal = self.signal_service.analyze_ticker(ticker)
                if signal["combined_score"] < self.settings.opportunity_discovery_min_score:
                    continue

                candidates.append(
                    {
                        "ticker": ticker,
                        "snapshot": snapshot,
                        "signal": signal,
                    }
                )

            candidates.sort(key=lambda item: item["signal"]["combined_score"], reverse=True)
            selected = candidates[: self.settings.opportunity_discovery_per_watchlist]

            for candidate in selected:
                snapshot = candidate["snapshot"]
                signal = candidate["signal"]
                self.watchlist_service.add_item(
                    session,
                    watchlist.id,
                    WatchlistItemCreate(
                        ticker=candidate["ticker"],
                        strategy_hypothesis=watchlist.hypothesis,
                        score=signal["combined_score"],
                        reason=(
                            f"Autonomous discovery candidate with alpha gap "
                            f"{signal['quant_summary']['alpha_gap_pct_20']}% vs {self.settings.benchmark_ticker}."
                        ),
                        key_metrics={
                            "source": "opportunity_discovery",
                            "benchmark_ticker": self.settings.benchmark_ticker,
                            "alpha_gap_pct": signal["quant_summary"]["alpha_gap_pct_20"],
                            "risk_reward": signal["risk_reward"],
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
                        "score": signal["combined_score"],
                        "alpha_gap_pct": signal["quant_summary"]["alpha_gap_pct_20"],
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
