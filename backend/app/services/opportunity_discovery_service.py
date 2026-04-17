from __future__ import annotations

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
            }

        watchlists = [watchlist for watchlist in self.watchlist_service.list_watchlists(session) if watchlist.status == "active"]
        universe = self._parse_universe(self.settings.opportunity_discovery_universe)
        benchmark = self.market_data_service.get_snapshot(self.settings.benchmark_ticker)
        discovered_items = 0
        top_candidates: list[dict] = []
        tracked_tickers = {
            item.ticker.upper()
            for watchlist in watchlists
            for item in watchlist.items
        }

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
        }

    @staticmethod
    def _parse_universe(raw_universe: str) -> list[str]:
        return [ticker.strip().upper() for ticker in raw_universe.split(",") if ticker.strip()]
