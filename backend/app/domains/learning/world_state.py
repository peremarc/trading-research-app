from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.market_state_snapshot import MarketStateSnapshotRecord
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.watchlist import Watchlist
from app.domains.learning.macro import MacroContextService
from app.domains.learning.protocol import MarketStateSnapshot as ProtocolMarketStateSnapshot
from app.domains.market.services import CalendarService, MarketDataService
from app.providers.calendar import CalendarProviderError


class MarketStateService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        market_data_service: MarketDataService | None = None,
        calendar_service: CalendarService | None = None,
        macro_context_service: MacroContextService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_data_service = market_data_service or MarketDataService()
        self.calendar_service = calendar_service or CalendarService()
        self.macro_context_service = macro_context_service or MacroContextService()

    def capture_snapshot(
        self,
        session: Session,
        *,
        trigger: str,
        pdca_phase: str | None = None,
        source_context: dict | None = None,
    ) -> MarketStateSnapshotRecord:
        context = dict(source_context or {})
        payload = self.build_snapshot_payload(
            session,
            trigger=trigger,
            pdca_phase=pdca_phase,
            source_context=context,
        )
        regime = payload.get("market_regime") if isinstance(payload.get("market_regime"), dict) else {}
        record = MarketStateSnapshotRecord(
            trigger=trigger,
            pdca_phase=pdca_phase,
            execution_mode=str(context.get("execution_mode") or "global"),
            benchmark_ticker=self.settings.benchmark_ticker,
            regime_label=str(regime.get("label") or "range_mixed"),
            regime_confidence=float(regime["confidence"]) if regime.get("confidence") is not None else None,
            summary=str(payload.get("summary") or "No market-state summary available."),
            snapshot_payload=payload,
            source_context=context,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    def list_snapshots(
        self,
        session: Session,
        *,
        limit: int = 20,
        pdca_phase: str | None = None,
    ) -> list[MarketStateSnapshotRecord]:
        statement = select(MarketStateSnapshotRecord).order_by(MarketStateSnapshotRecord.created_at.desc())
        if pdca_phase is not None:
            statement = statement.where(MarketStateSnapshotRecord.pdca_phase == pdca_phase)
        statement = statement.limit(max(1, min(limit, 100)))
        return list(session.scalars(statement).all())

    def get_latest_snapshot(
        self,
        session: Session,
        *,
        pdca_phase: str | None = None,
    ) -> MarketStateSnapshotRecord | None:
        snapshots = self.list_snapshots(session, limit=1, pdca_phase=pdca_phase)
        return snapshots[0] if snapshots else None

    def get_latest_protocol_market_state(
        self,
        session: Session,
        *,
        pdca_phase: str | None = None,
    ) -> dict | None:
        latest = self.get_latest_snapshot(session, pdca_phase=pdca_phase)
        if latest is None:
            return None
        payload = latest.snapshot_payload if isinstance(latest.snapshot_payload, dict) else {}
        market_state = payload.get("market_state_snapshot") if isinstance(payload.get("market_state_snapshot"), dict) else None
        if market_state is None:
            return None
        return {
            **market_state,
            "snapshot_id": latest.id,
            "summary": latest.summary,
            "regime_label": latest.regime_label,
            "regime_confidence": latest.regime_confidence,
            "pdca_phase": latest.pdca_phase,
            "trigger": latest.trigger,
        }

    def build_snapshot_payload(
        self,
        session: Session,
        *,
        trigger: str,
        pdca_phase: str | None = None,
        source_context: dict | None = None,
    ) -> dict:
        context = dict(source_context or {})
        benchmark_snapshot = asdict(self.market_data_service.get_snapshot(self.settings.benchmark_ticker))
        macro_context = self.macro_context_service.get_context(session, limit=6).model_dump(mode="json")
        calendar_events, calendar_error = self._get_macro_calendar_context()
        expiry_context = self._get_quarterly_expiry_context()
        open_positions = self._build_open_positions(session)
        active_watchlists = self._build_active_watchlists(session)
        backlog = self._build_backlog_summary(session)
        regime = self._infer_global_regime(
            benchmark_snapshot=benchmark_snapshot,
            macro_context=macro_context,
            calendar_events=calendar_events,
        )
        protocol_market_state = ProtocolMarketStateSnapshot(
            execution_mode=str(context.get("execution_mode") or "global"),
            watchlist_code=context.get("watchlist_code"),
            portfolio_state={
                **backlog,
                "benchmark_ticker": self.settings.benchmark_ticker,
                "benchmark_price": benchmark_snapshot["price"],
                "benchmark_month_performance": benchmark_snapshot["month_performance"],
                "market_state_trigger": trigger,
                "market_state_phase": pdca_phase,
            },
            open_positions=open_positions,
            recent_alerts=[],
            macro_context={
                **macro_context,
                "global_regime": regime["label"],
                "global_regime_confidence": regime["confidence"],
            },
            corporate_calendar=calendar_events,
            market_regime_inputs={
                "benchmark_snapshot": benchmark_snapshot,
                "market_regime": regime,
                "calendar_error": calendar_error,
                "expiry_context": expiry_context,
                "source_context": context,
                "backlog": backlog,
            },
            active_watchlists=active_watchlists,
        ).model_dump(mode="json")
        summary = (
            f"World state for {pdca_phase or 'general'} phase: regime {regime['label']} "
            f"({regime['confidence']:.2f}) with {backlog['open_positions_count']} open positions, "
            f"{backlog['active_watchlists_count']} active watchlists and "
            f"{backlog['open_research_tasks']} open research tasks."
        )
        if expiry_context.get("available") and expiry_context.get("phase") not in {"normal", "unavailable", "error"}:
            summary = f"{summary} Expiry context: {expiry_context.get('phase')} ({expiry_context.get('reason')})."
        return {
            "summary": summary,
            "market_state_snapshot": protocol_market_state,
            "market_regime": regime,
            "benchmark_snapshot": benchmark_snapshot,
            "macro_context": macro_context,
            "calendar_events": calendar_events,
            "calendar_error": calendar_error,
            "expiry_context": expiry_context,
            "backlog": backlog,
            "trigger": trigger,
            "pdca_phase": pdca_phase,
            "source_context": context,
        }

    def _build_open_positions(self, session: Session) -> list[dict]:
        positions = list(
            session.scalars(
                select(Position).where(Position.status == "open").order_by(Position.entry_date.desc()).limit(8)
            ).all()
        )
        return [
            {
                "position_id": position.id,
                "ticker": position.ticker,
                "side": position.side,
                "strategy_version_id": position.strategy_version_id,
                "entry_price": position.entry_price,
                "stop_price": position.stop_price,
                "target_price": position.target_price,
                "size": position.size,
                "thesis": position.thesis,
                "account_mode": position.account_mode,
            }
            for position in positions
        ]

    @staticmethod
    def _build_active_watchlists(session: Session) -> list[dict]:
        watchlists = list(
            session.scalars(select(Watchlist).where(Watchlist.status == "active").order_by(Watchlist.id.desc()).limit(8)).all()
        )
        payload: list[dict] = []
        for watchlist in watchlists:
            active_items = [item for item in watchlist.items if item.state in {"watching", "active", "entered"}]
            payload.append(
                {
                    "watchlist_id": watchlist.id,
                    "code": watchlist.code,
                    "name": watchlist.name,
                    "strategy_id": watchlist.strategy_id,
                    "setup_id": watchlist.setup_id,
                    "item_count": len(watchlist.items),
                    "active_item_count": len(active_items),
                    "tickers": [item.ticker for item in active_items[:6]],
                }
            )
        return payload

    @staticmethod
    def _build_backlog_summary(session: Session) -> dict:
        open_positions_count = session.query(Position).filter(Position.status == "open").count()
        pending_reviews = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        open_research_tasks = session.query(ResearchTask).filter(ResearchTask.status.in_(["open", "in_progress"])).count()
        active_watchlists_count = session.query(Watchlist).filter(Watchlist.status == "active").count()
        return {
            "open_positions_count": open_positions_count,
            "pending_reviews": pending_reviews,
            "open_research_tasks": open_research_tasks,
            "active_watchlists_count": active_watchlists_count,
        }

    def _get_macro_calendar_context(self) -> tuple[list[dict], str | None]:
        try:
            events = self.calendar_service.list_macro_events(days_ahead=7)
        except CalendarProviderError as exc:
            return [], str(exc)
        return [event.__dict__ for event in events[:8]], None

    def _get_quarterly_expiry_context(self) -> dict:
        get_expiry_context = getattr(self.calendar_service, "get_quarterly_expiry_context", None)
        if not callable(get_expiry_context):
            return {
                "available": False,
                "source": "unavailable",
                "phase": "unavailable",
                "reason": "Quarterly expiry context is unavailable from the active calendar service.",
            }
        try:
            payload = dict(get_expiry_context() or {})
        except Exception as exc:
            return {
                "available": False,
                "source": "error",
                "phase": "error",
                "reason": f"Quarterly expiry context failed: {exc}",
            }
        payload.setdefault("available", True)
        payload.setdefault("source", "internal")
        payload.setdefault("phase", "normal")
        payload.setdefault("reason", "No quarterly expiry context available.")
        return payload

    @staticmethod
    def _infer_global_regime(
        *,
        benchmark_snapshot: dict,
        macro_context: dict,
        calendar_events: list[dict],
    ) -> dict:
        active_regimes = [
            str(item).strip().lower()
            for item in macro_context.get("active_regimes", [])
            if isinstance(item, str) and item.strip()
        ]
        macro_risk = any(
            token in " ".join(active_regimes)
            for token in ["risk", "uncertain", "volatile", "bear", "tightening"]
        )
        price = float(benchmark_snapshot.get("price") or 0.0)
        sma_50 = float(benchmark_snapshot.get("sma_50") or 0.0)
        sma_200 = float(benchmark_snapshot.get("sma_200") or 0.0)
        month_perf = float(benchmark_snapshot.get("month_performance") or 0.0)
        pending_high_impact = any(
            str(event.get("impact") or "").strip().lower() == "high" for event in calendar_events if isinstance(event, dict)
        )

        if price > sma_50 > sma_200 and month_perf >= 0 and not macro_risk:
            return {
                "label": "bullish_trend",
                "confidence": 0.74 if not pending_high_impact else 0.67,
                "justification": "Benchmark trend structure is constructive and macro context is not explicitly risk-off.",
            }
        if price < sma_50 and price < sma_200:
            return {
                "label": "high_volatility_risk_off" if macro_risk else "range_mixed",
                "confidence": 0.71 if macro_risk else 0.6,
                "justification": "Benchmark is below intermediate and long-term trend support.",
            }
        if macro_risk or pending_high_impact:
            return {
                "label": "macro_uncertainty",
                "confidence": 0.64,
                "justification": "Macro signals or near-term calendar risk argue for a selective posture.",
            }
        return {
            "label": "range_mixed",
            "confidence": 0.57,
            "justification": "Benchmark structure and macro context are mixed, so playbooks should stay selective.",
        }
