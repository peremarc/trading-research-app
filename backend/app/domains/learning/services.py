from __future__ import annotations

import unicodedata
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.analysis import AnalysisRun
from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot
from app.db.models.decision_context import DecisionContextSnapshot
from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.signal_definition import SignalDefinition
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.strategy_evolution import StrategyActivationEvent, StrategyChangeEvent
from app.db.models.trade_review import TradeReview
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.learning.agent import AIDecisionError, AutonomousTradingAgentService
from app.domains.learning.decisioning import DecisionContextAssemblerService, EntryScoringService, PositionSizingService
from app.domains.learning.relevance import DecisionContextService, FeatureRelevanceService, StrategyContextAdaptationService
from app.domains.learning.world_state import MarketStateService
from app.domains.learning.tools import AgentToolGatewayService
from app.domains.learning.repositories import FailurePatternRepository, JournalRepository, MemoryRepository, PDCACycleRepository
from app.domains.learning.schemas import (
    AutoReviewBatchResult,
    AutoReviewResult,
    BotChatResponse,
    DailyPlanRequest,
    ExecutionCandidateResult,
    JournalEntryCreate,
    MemoryItemCreate,
    MarketStateSnapshotRead,
    OrchestratorActResponse,
    OrchestratorDoResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    PDCACycleCreate,
)
from app.domains.market.schemas import AnalysisRunCreate, SignalCreate
from app.domains.execution.schemas import AutoExitBatchResult, TradeReviewCreate
from app.domains.learning.macro import MacroContextService
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError


class JournalService:
    RETENTION_LIMITS: dict[str, int] = {
        "pdca_do": 288,
        "pdca_check": 288,
        "pdca_act": 288,
        "strategy_evolution_success": 160,
    }

    def __init__(self, repository: JournalRepository | None = None) -> None:
        self.repository = repository or JournalRepository()

    def list_entries(self, session: Session):
        return self.repository.list(session)

    def create_entry(self, session: Session, payload: JournalEntryCreate):
        entry = self.repository.create(session, payload)
        self._apply_retention(session, entry.entry_type)
        return entry

    @classmethod
    def _stale_entry_ids(cls, session: Session, *, entry_type: str, keep_latest: int) -> list[int]:
        if keep_latest <= 0:
            return [
                item_id
                for (item_id,) in session.query(JournalEntry.id)
                .filter(JournalEntry.entry_type == entry_type)
                .all()
            ]
        rows = (
            session.query(JournalEntry.id)
            .filter(JournalEntry.entry_type == entry_type)
            .order_by(JournalEntry.event_time.desc(), JournalEntry.id.desc())
            .offset(keep_latest)
            .all()
        )
        return [item_id for (item_id,) in rows]

    @classmethod
    def _apply_retention(cls, session: Session, entry_type: str) -> int:
        keep_latest = cls.RETENTION_LIMITS.get(entry_type)
        if keep_latest is None:
            return 0
        stale_ids = cls._stale_entry_ids(session, entry_type=entry_type, keep_latest=keep_latest)
        if not stale_ids:
            return 0
        session.query(JournalEntry).filter(JournalEntry.id.in_(stale_ids)).delete(synchronize_session=False)
        session.commit()
        return len(stale_ids)


class MemoryService:
    RETENTION_LIMITS_EXACT: dict[tuple[str, str], int] = {
        ("episodic", "pdca_check"): 288,
        ("episodic", "pdca_act"): 288,
    }
    RETENTION_LIMITS_PREFIX: dict[tuple[str, str], int] = {
        ("strategy_evolution", "strategy:"): 120,
    }

    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def list_items(self, session: Session):
        return self.repository.list(session)

    def create_item(self, session: Session, payload: MemoryItemCreate):
        item = self.repository.create(session, payload)
        self._apply_retention(session, item.memory_type, item.scope)
        return item

    def retrieve_scope(self, session: Session, scope: str, limit: int = 10):
        return self.repository.retrieve(session, scope=scope, limit=limit)

    @classmethod
    def _retention_limit(cls, memory_type: str, scope: str) -> int | None:
        exact = cls.RETENTION_LIMITS_EXACT.get((memory_type, scope))
        if exact is not None:
            return exact
        for (rule_type, prefix), keep_latest in cls.RETENTION_LIMITS_PREFIX.items():
            if memory_type == rule_type and scope.startswith(prefix):
                return keep_latest
        return None

    @classmethod
    def _stale_item_ids(cls, session: Session, *, memory_type: str, scope: str, keep_latest: int) -> list[int]:
        if keep_latest <= 0:
            return [
                item_id
                for (item_id,) in session.query(MemoryItem.id)
                .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope == scope)
                .all()
            ]
        rows = (
            session.query(MemoryItem.id)
            .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope == scope)
            .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
            .offset(keep_latest)
            .all()
        )
        return [item_id for (item_id,) in rows]

    @classmethod
    def _apply_retention(cls, session: Session, memory_type: str, scope: str) -> int:
        keep_latest = cls._retention_limit(memory_type, scope)
        if keep_latest is None:
            return 0
        stale_ids = cls._stale_item_ids(
            session,
            memory_type=memory_type,
            scope=scope,
            keep_latest=keep_latest,
        )
        if not stale_ids:
            return 0
        session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
        session.commit()
        return len(stale_ids)


class LearningHistoryMaintenanceService:
    def trim_history(self, session: Session, *, dry_run: bool = True) -> dict:
        journal_summary = self.prune_journal_entries(session, dry_run=dry_run)
        memory_summary = self.prune_memory_items(session, dry_run=dry_run)
        return {
            "dry_run": dry_run,
            "journal": journal_summary,
            "memory": memory_summary,
            "deleted_total": journal_summary["deleted_count"] + memory_summary["deleted_count"],
        }

    def prune_journal_entries(self, session: Session, *, dry_run: bool = True) -> dict:
        deleted_ids: list[int] = []
        rules: list[dict] = []
        for entry_type, keep_latest in JournalService.RETENTION_LIMITS.items():
            stale_ids = JournalService._stale_entry_ids(session, entry_type=entry_type, keep_latest=keep_latest)
            if stale_ids and not dry_run:
                session.query(JournalEntry).filter(JournalEntry.id.in_(stale_ids)).delete(synchronize_session=False)
            deleted_ids.extend(stale_ids)
            rules.append(
                {
                    "entry_type": entry_type,
                    "keep_latest": keep_latest,
                    "deleted_count": len(stale_ids),
                }
            )
        if deleted_ids and not dry_run:
            session.commit()
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "rules": rules,
        }

    def prune_memory_items(self, session: Session, *, dry_run: bool = True) -> dict:
        deleted_ids: list[int] = []
        rules: list[dict] = []

        for (memory_type, scope), keep_latest in MemoryService.RETENTION_LIMITS_EXACT.items():
            stale_ids = MemoryService._stale_item_ids(
                session,
                memory_type=memory_type,
                scope=scope,
                keep_latest=keep_latest,
            )
            if stale_ids and not dry_run:
                session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
            deleted_ids.extend(stale_ids)
            rules.append(
                {
                    "memory_type": memory_type,
                    "scope": scope,
                    "keep_latest": keep_latest,
                    "deleted_count": len(stale_ids),
                }
            )

        for (memory_type, prefix), keep_latest in MemoryService.RETENTION_LIMITS_PREFIX.items():
            scopes = [
                scope
                for (scope,) in session.query(MemoryItem.scope)
                .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope.like(f"{prefix}%"))
                .distinct()
                .all()
            ]
            for scope in scopes:
                stale_ids = MemoryService._stale_item_ids(
                    session,
                    memory_type=memory_type,
                    scope=scope,
                    keep_latest=keep_latest,
                )
                if stale_ids and not dry_run:
                    session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
                deleted_ids.extend(stale_ids)
                rules.append(
                    {
                        "memory_type": memory_type,
                        "scope": scope,
                        "keep_latest": keep_latest,
                        "deleted_count": len(stale_ids),
                    }
                )

        if deleted_ids and not dry_run:
            session.commit()
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "rules": rules,
        }


class BotChatService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        research_service: object | None = None,
        work_queue_service: object | None = None,
        news_service: object | None = None,
        calendar_service: object | None = None,
        macro_context_service: MacroContextService | None = None,
        market_state_service: MarketStateService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if research_service is None:
            from app.domains.market.services import ResearchService

            research_service = ResearchService()
        if work_queue_service is None:
            from app.domains.market.services import WorkQueueService

            work_queue_service = WorkQueueService()
        if news_service is None:
            from app.domains.market.services import NewsService

            news_service = NewsService()
        if calendar_service is None:
            from app.domains.market.services import CalendarService

            calendar_service = CalendarService()
        if macro_context_service is None:
            macro_context_service = MacroContextService()
        if market_state_service is None:
            market_state_service = MarketStateService(settings=self.settings)
        self.research_service = research_service
        self.work_queue_service = work_queue_service
        self.news_service = news_service
        self.calendar_service = calendar_service
        self.macro_context_service = macro_context_service
        self.market_state_service = market_state_service

    def _get_latest_market_state_context(self, session: Session) -> dict | None:
        snapshot = self.market_state_service.get_latest_snapshot(session)
        if snapshot is None:
            return None
        payload = snapshot.snapshot_payload if isinstance(snapshot.snapshot_payload, dict) else {}
        backlog = payload.get("backlog") if isinstance(payload.get("backlog"), dict) else {}
        macro_context = payload.get("macro_context") if isinstance(payload.get("macro_context"), dict) else {}
        active_regimes = macro_context.get("active_regimes") if isinstance(macro_context.get("active_regimes"), list) else []
        return {
            "snapshot_id": snapshot.id,
            "trigger": snapshot.trigger,
            "pdca_phase": snapshot.pdca_phase,
            "regime_label": snapshot.regime_label,
            "regime_confidence": snapshot.regime_confidence,
            "summary": snapshot.summary,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at is not None else None,
            "open_positions_count": backlog.get("open_positions_count"),
            "active_watchlists_count": backlog.get("active_watchlists_count"),
            "open_research_tasks": backlog.get("open_research_tasks"),
            "active_regimes": active_regimes,
        }

    def reply(self, session: Session, message: str) -> BotChatResponse:
        topic = self._classify_topic(message)

        if topic == "discoveries":
            reply, context = self._build_discoveries_reply(session)
        elif topic == "news":
            reply, context = self._build_news_reply(session, message)
        elif topic == "calendar":
            reply, context = self._build_calendar_reply(session, message)
        elif topic == "macro":
            reply, context = self._build_macro_reply(session)
        elif topic == "status":
            reply, context = self._build_status_reply(session)
        elif topic == "tools":
            reply, context = self._build_tools_reply(session)
        elif topic == "operations":
            reply, context = self._build_operations_reply(session)
        else:
            reply, context = self._build_overview_reply(session)

        return BotChatResponse(
            topic=topic,
            reply=reply,
            suggested_prompts=self._suggested_prompts(topic),
            context=context,
        )

    def _build_status_reply(self, session: Session) -> tuple[str, dict]:
        from app.domains.system.runtime import scheduler_service

        status = scheduler_service.get_status_payload()
        bot = status["bot"]
        queue = self.work_queue_service.get_queue(session)
        open_research = [task for task in self.research_service.list_tasks(session) if task.status != "completed"]
        open_positions = session.query(Position).filter(Position.status == "open").count()
        latest_incident = next((item for item in bot["incidents"] if item["status"] == "open"), None)
        top_item = queue.items[0] if queue.items else None
        latest_market_state = self._get_latest_market_state_context(session)

        lines = [
            f"Ahora mismo el bot está {bot['status'].upper()}.",
            (
                f"Fase actual: {bot['current_phase']}."
                if bot["current_phase"]
                else f"Última fase correcta: {bot['last_successful_phase'] or 'ninguna'}."
            ),
            f"Ciclos completados: {bot['cycle_runs']}. Posiciones abiertas: {open_positions}.",
        ]
        if latest_market_state is not None:
            lines.append(
                "Último market state: "
                f"régimen {latest_market_state['regime_label']} en fase {latest_market_state['pdca_phase'] or 'general'}."
            )
        if latest_incident is not None:
            lines.append(f"Está bloqueado por una incidencia: {latest_incident['title']}.")
        elif top_item is not None:
            lines.append(f"El foco inmediato es: {top_item.title}.")
        else:
            lines.append("No veo una incidencia abierta ni una cola prioritaria urgente.")
        if open_research:
            lines.append(f"Tiene {len(open_research)} tareas de research activas.")

        return " ".join(lines), {
            "bot_status": bot["status"],
            "current_phase": bot["current_phase"],
            "cycle_runs": bot["cycle_runs"],
            "open_positions": open_positions,
            "open_research_tasks": len(open_research),
            "top_queue_item": top_item.title if top_item is not None else None,
            "latest_incident": latest_incident["title"] if latest_incident is not None else None,
            "market_state": latest_market_state,
        }

    def _build_discoveries_reply(self, session: Session) -> tuple[str, dict]:
        open_tasks = list(
            session.scalars(
                select(ResearchTask).where(ResearchTask.status != "completed").order_by(ResearchTask.created_at.desc()).limit(3)
            ).all()
        )
        latest_changes = list(
            session.scalars(select(StrategyChangeEvent).order_by(StrategyChangeEvent.created_at.desc()).limit(3)).all()
        )
        latest_activations = list(
            session.scalars(
                select(StrategyActivationEvent).order_by(StrategyActivationEvent.created_at.desc()).limit(3)
            ).all()
        )
        latest_validations = list(
            session.scalars(
                select(CandidateValidationSnapshot).order_by(CandidateValidationSnapshot.generated_at.desc()).limit(3)
            ).all()
        )

        discoveries: list[str] = []
        if latest_validations:
            for snapshot in latest_validations:
                discoveries.append(
                    f"validación candidata v{snapshot.strategy_version_id} de estrategia {snapshot.strategy_id}: "
                    f"{snapshot.evaluation_status} con win rate {snapshot.win_rate or 0:.1f}% y {snapshot.trade_count} trades"
                )
        if latest_activations:
            discoveries.append(f"última activación automática: {latest_activations[0].activation_reason}")
        if latest_changes:
            discoveries.append(f"último cambio de estrategia: {latest_changes[0].change_reason}")
        if open_tasks:
            discoveries.append(f"research abierto: {open_tasks[0].title}")

        if not discoveries:
            reply = (
                "Todavía no tengo descubrimientos materiales persistidos. "
                "Ahora mismo conviene ejecutar ciclos DO/CHECK o arrancar el scheduler para generar señales, research y cambios."
            )
        else:
            reply = "Lo más relevante que he descubierto o dejado preparado es: " + "; ".join(discoveries[:4]) + "."

        return reply, {
            "open_research_titles": [task.title for task in open_tasks],
            "latest_change_reasons": [item.change_reason for item in latest_changes],
            "latest_activation_reasons": [item.activation_reason for item in latest_activations],
            "candidate_validation_statuses": [item.evaluation_status for item in latest_validations],
        }

    def _build_tools_reply(self, session: Session) -> tuple[str, dict]:
        from app.domains.system.runtime import scheduler_service

        ai_status = scheduler_service.get_status_payload()["ai"]
        gaps: list[str] = []

        if not ai_status["enabled"]:
            gaps.append("activar un proveedor de IA operativo para que el bot pueda razonar y explicar mejor sus decisiones")
        elif not ai_status["ready"]:
            gaps.append("credenciales válidas para el proveedor de IA configurado")

        if self.settings.market_data_provider == "stub":
            gaps.append("activar el proxy interno de IBKR para dejar de depender del proveedor stub")
        elif self.settings.market_data_provider == "twelve_data" and not self.settings.twelve_data_api_key:
            gaps.append("una API key de Twelve Data para dejar de depender del proveedor stub")

        if session.query(Position).filter(Position.account_mode != "paper").count() == 0:
            gaps.append("integración de broker o execution gateway, porque ahora mismo solo veo paper trading")

        if session.query(AnalysisRun).count() == 0:
            gaps.append("más flujo de análisis persistido para comparar setups y medir mejor qué funciona")

        gaps.append("alguna fuente externa de noticias, catalysts o sentimiento, que hoy no aparece integrada en esta MVP")
        gaps.append("un módulo de backtesting/replay más explícito para validar cambios antes de promover estrategias")

        reply = (
            "Viendo el código y la configuración actual, las herramientas que más faltan para mejorar resultados son: "
            + "; ".join(gaps[:5])
            + "."
        )
        return reply, {
            "ai_enabled": ai_status["enabled"],
            "ai_ready": ai_status["ready"],
            "market_data_provider": self.settings.market_data_provider,
            "using_stub_market_data": self.settings.market_data_provider == "stub",
            "has_twelve_data_key": bool(self.settings.twelve_data_api_key),
            "paper_only_positions": session.query(Position).filter(Position.account_mode != "paper").count() == 0,
        }

    def _build_news_reply(self, session: Session, message: str) -> tuple[str, dict]:
        ticker = self._extract_ticker_candidate(message)
        query = ticker if ticker else message

        try:
            articles = (
                self.news_service.list_news_for_ticker(ticker, max_results=5)
                if ticker
                else self.news_service.list_news(query, max_results=5)
            )
        except NewsProviderError as exc:
            return (
                f"No puedo traer noticias ahora mismo: {exc}.",
                {"query": query, "articles": [], "ticker": ticker, "error": str(exc)},
            )

        if not articles:
            if not self.settings.gnews_api_key:
                return (
                    "No puedo traer noticias porque GNews no está configurado todavía en el backend activo.",
                    {"query": query, "articles": [], "ticker": ticker},
                )
            return (
                f"No encontré noticias recientes para {ticker or query}.",
                {"query": query, "articles": [], "ticker": ticker},
            )

        summaries = [
            f"{article.title} ({article.source_name}, {article.published_at[:10]})"
            for article in articles[:3]
        ]
        prefix = f"Noticias recientes para {ticker}: " if ticker else f"Noticias recientes para '{query}': "
        return prefix + "; ".join(summaries) + ".", {
            "query": query,
            "ticker": ticker,
            "articles": [
                {
                    "title": article.title,
                    "source_name": article.source_name,
                    "published_at": article.published_at,
                    "url": article.url,
                }
                for article in articles
            ],
        }

    def _build_operations_reply(self, session: Session) -> tuple[str, dict]:
        latest_positions = list(
            session.scalars(
                select(Position).order_by(Position.exit_date.desc().nullslast(), Position.entry_date.desc()).limit(5)
            ).all()
        )
        closed_positions = list(session.scalars(select(Position).where(Position.status == "closed")).all())
        open_positions = [position for position in latest_positions if position.status == "open"]
        wins = [position for position in closed_positions if (position.pnl_pct or 0.0) > 0]
        losses = [position for position in closed_positions if (position.pnl_pct or 0.0) <= 0]
        avg_realized = (
            round(sum((position.pnl_pct or 0.0) for position in closed_positions) / len(closed_positions), 2)
            if closed_positions
            else None
        )

        if not latest_positions:
            return (
                "Todavía no hay operaciones registradas. En cuanto el bot abra o cierre posiciones, aquí podré resumirte entradas, salidas y PnL.",
                {
                    "latest_positions": [],
                    "closed_positions": 0,
                    "open_positions": 0,
                    "avg_realized_pnl_pct": None,
                },
            )

        summaries = []
        for position in latest_positions[:4]:
            status = "abierta" if position.status == "open" else f"cerrada {position.pnl_pct or 0:.2f}%"
            reason = position.exit_reason or position.thesis or "sin detalle"
            summaries.append(f"{position.ticker}: {status} ({reason})")

        reply = (
            f"Resumen rápido de las últimas operaciones: {'; '.join(summaries)}. "
            f"Acumulado cerrado: {len(closed_positions)} trades, {len(wins)} ganadoras, {len(losses)} perdedoras"
            + (f", PnL medio {avg_realized:.2f}%." if avg_realized is not None else ".")
        )
        return reply, {
            "latest_positions": [
                {
                    "ticker": position.ticker,
                    "status": position.status,
                    "pnl_pct": position.pnl_pct,
                    "exit_reason": position.exit_reason,
                }
                for position in latest_positions
            ],
            "closed_positions": len(closed_positions),
            "open_positions": len(open_positions),
            "wins": len(wins),
            "losses": len(losses),
            "avg_realized_pnl_pct": avg_realized,
        }

    def _build_macro_reply(self, session: Session) -> tuple[str, dict]:
        context = self.macro_context_service.get_context(session, limit=6).model_dump(mode="json")
        latest_market_state = self._get_latest_market_state_context(session)
        dominant_regimes = context["active_regimes"][:3] or (latest_market_state or {}).get("active_regimes", [])[:3]
        lines = [context["summary"]]
        if latest_market_state is not None:
            confidence_suffix = (
                f" con confianza {latest_market_state['regime_confidence']:.2f}"
                if latest_market_state.get("regime_confidence") is not None
                else ""
            )
            lines.append(
                "Último Market State Snapshot: "
                f"régimen {latest_market_state['regime_label']} en fase {latest_market_state['pdca_phase'] or 'general'}{confidence_suffix}."
            )
        if dominant_regimes:
            lines.append(f"Regimenes dominantes: {', '.join(dominant_regimes)}.")
        if context["signals"]:
            top_lines = [
                f"{signal['key']}: {signal['content']}"
                for signal in context["signals"][:3]
            ]
            lines.append(f"Señales más relevantes: {'; '.join(top_lines)}.")
        return " ".join(lines), {
            **context,
            "market_state": latest_market_state,
        }

    def _build_calendar_reply(self, session: Session, message: str) -> tuple[str, dict]:
        del session
        ticker = self._extract_ticker_candidate(message)
        try:
            events = (
                self.calendar_service.list_ticker_events(ticker, days_ahead=30)
                if ticker
                else self.calendar_service.list_macro_events(days_ahead=14)
            )
        except CalendarProviderError as exc:
            return (
                f"No puedo traer calendario ahora mismo: {exc}.",
                {"ticker": ticker, "events": [], "error": str(exc)},
            )

        if ticker:
            if not events:
                return (
                    f"No veo eventos corporativos próximos para {ticker} o el calendario externo no está configurado.",
                    {"ticker": ticker, "events": []},
                )
            reply = (
                f"Próximos eventos corporativos para {ticker}: "
                + "; ".join(f"{event.title} ({event.event_date})" for event in events[:3])
                + "."
            )
            return reply, {
                "ticker": ticker,
                "events": [event.__dict__ for event in events],
            }

        if not events:
            return (
                "No veo eventos macro próximos o el calendario externo no está configurado.",
                {"events": []},
            )
        reply = (
            "Próximos eventos macro relevantes: "
            + "; ".join(f"{event.title} ({event.event_date})" for event in events[:4])
            + "."
        )
        return reply, {"events": [event.__dict__ for event in events]}

    def _build_overview_reply(self, session: Session) -> tuple[str, dict]:
        status_reply, status_context = self._build_status_reply(session)
        discoveries_reply, discoveries_context = self._build_discoveries_reply(session)
        operations_reply, operations_context = self._build_operations_reply(session)
        macro_reply, macro_context = self._build_macro_reply(session)
        reply = f"{status_reply} {discoveries_reply} {operations_reply} {macro_reply}"
        return reply, {
            "status": status_context,
            "discoveries": discoveries_context,
            "operations": operations_context,
            "macro": macro_context,
        }

    @staticmethod
    def _normalize_message(message: str) -> str:
        normalized = unicodedata.normalize("NFKD", message.lower())
        return "".join(char for char in normalized if not unicodedata.combining(char))

    def _classify_topic(self, message: str) -> str:
        text = self._normalize_message(message)

        if any(token in text for token in ["noticia", "noticias", "news", "catalyst", "catalysts", "titulares"]):
            return "news"
        if any(
            token in text
            for token in ["earnings", "calendario", "evento", "eventos", "ipc", "cpi", "fomc", "dividendo", "split"]
        ):
            return "calendar"
        if any(
            token in text
            for token in [
                "macro",
                "geopolit",
                "fed",
                "tipos",
                "inflacion",
                "petroleo",
                "guerra",
                "eleccion",
                "regimen",
                "rates",
            ]
        ):
            return "macro"
        if any(token in text for token in ["operacion", "trade", "trades", "pnl", "ultimo", "ultimas"]):
            return "operations"
        if any(token in text for token in ["descubr", "detect", "hall", "research", "oportun"]):
            return "discoveries"
        if any(token in text for token in ["herramient", "falta", "faltan", "mejorar", "improve", "tool"]):
            return "tools"
        if any(token in text for token in ["haciendo", "doing", "estado", "status", "ahora", "runtime"]):
            return "status"
        return "overview"

    @staticmethod
    def _suggested_prompts(topic: str) -> list[str]:
        suggestions = {
            "news": [
                "Noticias de NVDA",
                "Catalysts recientes de AAPL",
                "Ultimas noticias del mercado",
            ],
            "calendar": [
                "Proximos earnings de NVDA",
                "Que eventos macro hay esta semana",
                "Calendario corporativo de AAPL",
            ],
            "discoveries": [
                "Que has descubierto hoy",
                "Que candidatos merecen promocion",
                "Que research sigue abierto",
            ],
            "macro": [
                "Cual es el contexto macro actual",
                "Que regimen de mercado estas viendo",
                "Que riesgos geopoliticos importan ahora",
            ],
            "status": [
                "Que estas haciendo ahora",
                "Cual es tu siguiente foco",
                "Por que estas en pausa",
            ],
            "tools": [
                "Que herramientas te faltan",
                "Como mejorarias el stack actual",
                "Que integracion aporta mas valor",
            ],
            "operations": [
                "Resumen de las ultimas operaciones",
                "Cuantas posiciones abiertas hay",
                "Cuales fueron las ultimas perdidas",
            ],
            "overview": [
                "Dame un resumen general",
                "Que has descubierto",
                "Que estas haciendo ahora",
            ],
        }
        return suggestions[topic]

    @staticmethod
    def _extract_ticker_candidate(message: str) -> str | None:
        matches = re.findall(r"\b[A-Z]{2,6}\b", message)
        return matches[0] if matches else None


class FailureAnalysisService:
    def __init__(self, repository: FailurePatternRepository | None = None) -> None:
        self.repository = repository or FailurePatternRepository()

    def refresh_patterns(self, session: Session) -> list:
        reviews = list(
            session.scalars(
                select(TradeReview).where(
                    TradeReview.outcome_label == "loss",
                    TradeReview.strategy_version_id.is_not(None),
                )
            ).all()
        )
        results = []
        for review in reviews:
            strategy_version = session.get(StrategyVersion, review.strategy_version_id)
            if strategy_version is None:
                continue
            failure_mode = review.failure_mode or review.cause_category
            signature = f"{strategy_version.strategy_id}:{review.strategy_version_id}:{failure_mode}"
            pattern = self.repository.get_by_signature(
                session,
                strategy_id=strategy_version.strategy_id,
                strategy_version_id=review.strategy_version_id,
                pattern_signature=signature,
            )
            if pattern is None:
                pattern = self.repository.create(
                    session,
                    {
                        "strategy_id": strategy_version.strategy_id,
                        "strategy_version_id": review.strategy_version_id,
                        "failure_mode": failure_mode,
                        "pattern_signature": signature,
                        "occurrences": 1,
                        "avg_loss_pct": review.observations.get("pnl_pct"),
                        "evidence": {
                            "review_ids": [review.id],
                            "latest_root_cause": review.root_cause,
                        },
                        "recommended_action": review.strategy_update_reason or review.proposed_strategy_change,
                        "status": "open",
                    },
                )
            else:
                review_ids = list(pattern.evidence.get("review_ids", []))
                if review.id not in review_ids:
                    review_ids.append(review.id)
                    losses = [pattern.avg_loss_pct] if pattern.avg_loss_pct is not None else []
                    current_loss = review.observations.get("pnl_pct")
                    if current_loss is not None:
                        losses.append(current_loss)
                    pattern.occurrences = len(review_ids)
                    pattern.avg_loss_pct = round(sum(losses) / len(losses), 2) if losses else None
                    pattern.evidence = {
                        **pattern.evidence,
                        "review_ids": review_ids,
                        "latest_root_cause": review.root_cause,
                    }
                    pattern.recommended_action = review.strategy_update_reason or review.proposed_strategy_change
                    pattern = self.repository.update(session, pattern)
            results.append(pattern)
        return results

    def list_patterns(self, session: Session):
        return self.repository.list(session)

    def list_patterns_for_strategy(self, session: Session, strategy_id: int):
        return self.repository.list_for_strategy(session, strategy_id)


class AutoReviewService:
    def __init__(self, trade_review_service: object | None = None) -> None:
        if trade_review_service is None:
            from app.domains.execution.services import TradeReviewService

            trade_review_service = TradeReviewService()
        self.trade_review_service = trade_review_service

    def generate_pending_loss_reviews(self, session: Session) -> AutoReviewBatchResult:
        positions = list(
            session.scalars(
                select(Position).where(
                    Position.status == "closed",
                    Position.review_status == "pending",
                    Position.pnl_pct.is_not(None),
                    Position.pnl_pct <= 0,
                )
            ).all()
        )

        generated_reviews = 0
        skipped_positions = 0
        results: list[AutoReviewResult] = []

        for position in positions:
            existing_review = session.scalar(select(TradeReview.id).where(TradeReview.position_id == position.id))
            if existing_review is not None:
                skipped_positions += 1
                results.append(
                    AutoReviewResult(
                        position_id=position.id,
                        generated=False,
                        review_id=existing_review,
                        reason="existing_review",
                    )
                )
                continue

            payload = self._build_review_payload(position)
            review = self.trade_review_service.create_review(session, position.id, payload)
            generated_reviews += 1
            results.append(
                AutoReviewResult(
                    position_id=position.id,
                    generated=True,
                    review_id=review.id,
                    reason="generated_from_loss_heuristic",
                )
            )

        return AutoReviewBatchResult(
            generated_reviews=generated_reviews,
            skipped_positions=skipped_positions,
            results=results,
        )

    @staticmethod
    def _build_review_payload(position: Position) -> TradeReviewCreate:
        cause_category = "setup_failure"
        root_cause = (
            "The trade closed negative without a completed review. Initial heuristic assumes the setup quality or timing was insufficient."
        )
        lesson = "Require stronger confirmation before entry and compare failed setup context against recent winning trades."
        proposed_change = (
            "Tighten entry filters for similar setups and review whether relative volume or trend alignment thresholds should be raised."
        )

        if position.max_drawdown_pct is not None and position.max_drawdown_pct <= -5:
            cause_category = "late_exit_or_weak_invalidation"
            root_cause = (
                "The trade experienced a meaningful drawdown before exit. Initial heuristic suggests invalidation rules were too loose or the exit came too late."
            )
            lesson = "Review invalidation timing and define clearer exit conditions when drawdown expands beyond acceptable behavior for the setup."
            proposed_change = "Reduce tolerance for adverse movement and formalize earlier invalidation on weak follow-through."
        elif position.exit_reason and "breakout" in position.exit_reason.lower():
            cause_category = "false_breakout"
            root_cause = "The exit reason points to a failed breakout dynamic. Initial heuristic suggests insufficient confirmation of continuation."
            lesson = "Demand cleaner breakout confirmation with volume and less extended entries."
            proposed_change = "Increase minimum breakout confirmation requirements before entry."

        return TradeReviewCreate(
            outcome_label="loss",
            outcome="loss",
            cause_category=cause_category,
            failure_mode=cause_category,
            observations={
                "entry_price": position.entry_price,
                "exit_price": position.exit_price,
                "pnl_pct": position.pnl_pct,
                "max_drawdown_pct": position.max_drawdown_pct,
                "max_runup_pct": position.max_runup_pct,
            },
            root_cause=root_cause,
            root_causes=[root_cause],
            lesson_learned=lesson,
            proposed_strategy_change=proposed_change,
            recommended_changes=[proposed_change],
            confidence=0.55,
            review_priority="high",
            should_modify_strategy=True,
            needs_strategy_update=True,
            strategy_update_reason=proposed_change,
        )


class PDCACycleService:
    def __init__(self, repository: PDCACycleRepository | None = None) -> None:
        self.repository = repository or PDCACycleRepository()

    def list_cycles(self, session: Session):
        return self.repository.list(session)

    def create_cycle(self, session: Session, payload: PDCACycleCreate):
        return self.repository.create(session, payload)

    def create_daily_plan(self, session: Session, cycle_date):
        payload = PDCACycleCreate(
            cycle_date=cycle_date,
            phase="plan",
            status="completed",
            summary="Daily PLAN cycle created by orchestrator bootstrap.",
            context={"focus": ["review_active_strategies", "refresh_screeners", "prepare_watchlists"]},
        )
        return self.repository.create(session, payload)


class OrchestratorService:
    def __init__(
        self,
        pdca_service: PDCACycleService | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        analysis_service: object | None = None,
        market_data_service: object | None = None,
        signal_service: object | None = None,
        position_service: object | None = None,
        auto_review_service: AutoReviewService | None = None,
        strategy_lab_service: object | None = None,
        exit_management_service: object | None = None,
        strategy_scoring_service: object | None = None,
        research_service: object | None = None,
        failure_analysis_service: FailureAnalysisService | None = None,
        work_queue_service: object | None = None,
        strategy_evolution_service: object | None = None,
        opportunity_discovery_service: object | None = None,
        trading_agent_service: AutonomousTradingAgentService | None = None,
        agent_tool_gateway_service: AgentToolGatewayService | None = None,
        market_state_service: MarketStateService | None = None,
        decision_context_service: DecisionContextService | None = None,
        feature_relevance_service: FeatureRelevanceService | None = None,
        strategy_context_adaptation_service: StrategyContextAdaptationService | None = None,
        decision_context_assembler_service: DecisionContextAssemblerService | None = None,
        entry_scoring_service: EntryScoringService | None = None,
        position_sizing_service: PositionSizingService | None = None,
        halt_on_market_data_failure: bool = False,
    ) -> None:
        self.pdca_service = pdca_service or PDCACycleService()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        if analysis_service is None:
            from app.domains.market.services import AnalysisService

            analysis_service = AnalysisService()
        if market_data_service is None:
            from app.domains.market.services import MarketDataService

            market_data_service = MarketDataService(raise_on_provider_error=halt_on_market_data_failure)
        if signal_service is None:
            from app.domains.market.analysis import FusedAnalysisService
            from app.domains.market.services import SignalService

            signal_service = SignalService(
                fused_analysis_service=FusedAnalysisService(market_data_service=market_data_service)
            )
        if position_service is None:
            from app.domains.execution.services import PositionService

            position_service = PositionService()
        if strategy_lab_service is None:
            from app.domains.strategy.services import StrategyLabService

            strategy_lab_service = StrategyLabService()
        if exit_management_service is None:
            from app.domains.execution.services import ExitManagementService

            exit_management_service = ExitManagementService()
        if strategy_scoring_service is None:
            from app.domains.strategy.services import StrategyScoringService

            strategy_scoring_service = StrategyScoringService()
        if research_service is None:
            from app.domains.market.services import ResearchService

            research_service = ResearchService()
        self.auto_review_service = auto_review_service or AutoReviewService()
        self.analysis_service = analysis_service
        self.market_data_service = market_data_service
        self.signal_service = signal_service
        self.position_service = position_service
        self.strategy_lab_service = strategy_lab_service
        self.exit_management_service = exit_management_service
        self.strategy_scoring_service = strategy_scoring_service
        self.research_service = research_service
        if strategy_evolution_service is None:
            from app.domains.strategy.services import StrategyEvolutionService

            strategy_evolution_service = StrategyEvolutionService(research_service=self.research_service)
        if opportunity_discovery_service is None:
            from app.domains.market.discovery import OpportunityDiscoveryService

            opportunity_discovery_service = OpportunityDiscoveryService(
                market_data_service=self.market_data_service,
                signal_service=self.signal_service,
            )
        self.strategy_evolution_service = strategy_evolution_service
        self.opportunity_discovery_service = opportunity_discovery_service
        self.failure_analysis_service = failure_analysis_service or FailureAnalysisService()
        if work_queue_service is None:
            from app.domains.market.services import WorkQueueService

            work_queue_service = WorkQueueService(failure_analysis_service=self.failure_analysis_service)
        self.work_queue_service = work_queue_service
        self.trading_agent_service = trading_agent_service or AutonomousTradingAgentService()
        self.agent_tool_gateway_service = agent_tool_gateway_service or AgentToolGatewayService()
        self.market_state_service = market_state_service or MarketStateService(
            settings=self.trading_agent_service.settings,
            market_data_service=self.market_data_service,
        )
        self.decision_context_service = decision_context_service or DecisionContextService()
        self.feature_relevance_service = feature_relevance_service or FeatureRelevanceService()
        self.strategy_context_adaptation_service = strategy_context_adaptation_service or StrategyContextAdaptationService()
        self.decision_context_assembler_service = decision_context_assembler_service or DecisionContextAssemblerService(
            strategy_context_adaptation_service=self.strategy_context_adaptation_service
        )
        self.entry_scoring_service = entry_scoring_service or EntryScoringService()
        self.position_sizing_service = position_sizing_service or PositionSizingService()

    @staticmethod
    def _get_execution_version(strategy: Strategy | None) -> tuple[int | None, bool]:
        if strategy is None:
            return None, False

        if strategy.status == "degraded":
            candidate_versions = [version for version in strategy.versions if version.lifecycle_stage == "candidate"]
            if candidate_versions:
                candidate_versions.sort(key=lambda version: version.version, reverse=True)
                return candidate_versions[0].id, True

        return strategy.current_version_id, False

    @staticmethod
    def _classify_guard_results(guard_results: dict | None) -> tuple[str | None, str]:
        if not isinstance(guard_results, dict) or not guard_results.get("blocked"):
            return None, "keep_on_watchlist"

        guard_types = {
            str(item).strip()
            for item in guard_results.get("types", [])
            if isinstance(item, str) and str(item).strip()
        }
        if "regime_policy" in guard_types:
            return "regime_policy", "skip_regime_policy"
        if "learned_rule" in guard_types:
            return "learned_rule", "skip_strategy_context_rule"
        if "portfolio_limit" in guard_types:
            return "portfolio_limit", "skip_portfolio_limit"
        if "risk_budget" in guard_types:
            return "risk_budget", "skip_risk_budget_limit"
        return "decision_layer", "keep_on_watchlist"

    @staticmethod
    def _to_market_state_read(record) -> MarketStateSnapshotRead | None:
        if record is None:
            return None
        return MarketStateSnapshotRead.model_validate(record)

    def plan_daily_cycle(self, session: Session, payload: DailyPlanRequest) -> OrchestratorPlanResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_plan",
            pdca_phase="plan",
            source_context=payload.market_context,
        )
        cycle = self.pdca_service.create_daily_plan(session, payload.cycle_date)
        review_backlog = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        open_research_tasks = len([task for task in self.research_service.list_tasks(session) if task.status in ["open", "in_progress"]])
        work_queue = self.work_queue_service.get_queue(session)
        degraded_candidate_backlog = len([item for item in work_queue.items if item.item_type == "degraded_candidate_validation"])
        cycle.context = {
            **cycle.context,
            **payload.market_context,
            "market_state_snapshot_id": market_state_snapshot.id,
            "market_state_regime": market_state_snapshot.regime_label,
            "review_backlog": review_backlog,
            "open_research_tasks": open_research_tasks,
            "degraded_candidate_backlog": degraded_candidate_backlog,
        }
        session.commit()
        session.refresh(cycle)
        return OrchestratorPlanResponse(
            cycle_id=cycle.id,
            phase=cycle.phase,
            status=cycle.status,
            summary=cycle.summary or "",
            market_context=cycle.context,
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
            work_queue=work_queue,
        )

    def run_do_phase(self, session: Session) -> OrchestratorDoResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_do",
            pdca_phase="do",
            source_context={"execution_mode": "global"},
        )
        exit_result: AutoExitBatchResult = self.exit_management_service.evaluate_open_positions(session)
        discovery_result = self.opportunity_discovery_service.refresh_active_watchlists(session)
        active_watchlists = session.query(Watchlist).filter(Watchlist.status == "active").count()
        items = list(
            session.scalars(
                select(WatchlistItem)
                .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
                .where(Watchlist.status == "active", WatchlistItem.state.in_(["watching", "active"]))
            ).all()
        )
        items.sort(
            key=lambda item: (
                0
                if (
                    (watchlist := session.get(Watchlist, item.watchlist_id)) is not None
                    and watchlist.strategy_id is not None
                    and (strategy := session.get(Strategy, watchlist.strategy_id)) is not None
                    and strategy.status == "degraded"
                    and any(version.lifecycle_stage == "candidate" for version in strategy.versions)
                )
                else 1,
                item.id,
            )
        )
        candidates: list[ExecutionCandidateResult] = []
        generated_analyses = 0
        generated_signals = 0
        opened_positions = 0
        prioritized_candidate_items = 0
        ai_decisions = 0
        ai_unavailable_entries = 0
        calendar_blocked_entries = 0
        learned_rule_blocked_entries = 0
        regime_policy_blocked_entries = 0
        decision_layer_blocked_entries = 0
        portfolio_blocked_entries = 0
        risk_budget_blocked_entries = 0
        decision_context_snapshots = 0

        for item in items:
            watchlist = session.get(Watchlist, item.watchlist_id)
            strategy = session.get(Strategy, watchlist.strategy_id) if watchlist and watchlist.strategy_id is not None else None
            strategy_version_id, using_candidate_version = self._get_execution_version(strategy)
            if using_candidate_version:
                prioritized_candidate_items += 1
            signal = self.signal_service.analyze_ticker(item.ticker)
            signal["base_combined_score"] = signal.get("combined_score")
            signal["base_decision"] = signal.get("decision")
            market_context = {
                "watchlist_id": watchlist.id if watchlist is not None else None,
                "execution_mode": "candidate_validation" if using_candidate_version else "default",
                "market_state_snapshot_id": market_state_snapshot.id,
                "market_state_regime": market_state_snapshot.regime_label,
                "opened_positions_so_far": opened_positions,
            }
            decision_context = self.decision_context_assembler_service.build_trade_candidate_context(
                session,
                ticker=item.ticker,
                strategy_id=strategy.id if strategy is not None else None,
                strategy_version_id=strategy_version_id,
                signal_payload=signal,
                market_context=market_context,
            )
            entry_score = self.entry_scoring_service.evaluate(
                signal_payload=signal,
                decision_context=decision_context,
            )
            signal["decision_context"] = decision_context
            signal["risk_budget"] = decision_context.get("risk_budget")
            signal["regime_policy"] = decision_context.get("regime_policy")
            signal["score_breakdown"] = entry_score["score_breakdown"]
            signal["guard_results"] = entry_score["guard_results"]
            signal["combined_score"] = entry_score["final_score"]
            signal["decision_confidence"] = entry_score["final_score"]
            signal["decision"] = entry_score["recommended_action"]
            signal["rationale"] = f"{signal['rationale']} {entry_score['summary']}"
            research_package = self.trading_agent_service.build_trade_candidate_research_package(
                ticker=item.ticker,
                strategy_version_id=strategy_version_id,
                signal_payload=signal,
                entry_context=market_context,
            )
            signal["research_plan"] = research_package.get("research_plan")
            signal["decision_trace"] = research_package.get("decision_trace")
            initial_decision_source = "deterministic_scoring"
            pre_entry_guard_category = None
            if entry_score["guard_results"]["blocked"]:
                pre_entry_guard_category, _ = self._classify_guard_results(entry_score["guard_results"])
                if pre_entry_guard_category == "learned_rule":
                    learned_rule_blocked_entries += 1
                elif pre_entry_guard_category == "regime_policy":
                    regime_policy_blocked_entries += 1
                elif pre_entry_guard_category == "portfolio_limit":
                    portfolio_blocked_entries += 1
                elif pre_entry_guard_category == "risk_budget":
                    risk_budget_blocked_entries += 1
                else:
                    decision_layer_blocked_entries += 1
                initial_decision_source = f"deterministic_{pre_entry_guard_category or 'guard'}"

            ai_decision = None
            ai_decision_error: str | None = None
            if not entry_score["guard_results"]["blocked"]:
                try:
                    ai_decision = self.trading_agent_service.advise_trade_candidate(
                        session,
                        ticker=item.ticker,
                        strategy_id=strategy.id if strategy is not None else None,
                        strategy_version_id=strategy_version_id,
                        watchlist_code=watchlist.code if watchlist is not None else None,
                        signal_payload=signal,
                        market_context=market_context,
                    )
                except AIDecisionError as exc:
                    ai_decision_error = str(exc)
                    ai_unavailable_entries += 1
                    signal["rationale"] = f"{signal['rationale']} AI unavailable; kept deterministic decision."
                    signal["ai_overlay"] = {
                        "provider": self.trading_agent_service.runtime.provider,
                        "model": self.trading_agent_service.runtime.model,
                        "status": "unavailable",
                        "error": ai_decision_error,
                        "fallback_to": signal["decision"],
                    }
            if ai_decision is not None:
                ai_decisions += 1
                signal["decision"] = ai_decision.action
                signal["decision_confidence"] = round(
                    min(
                        max(
                            (
                                float(signal.get("decision_confidence", signal["combined_score"]))
                                + ai_decision.confidence
                            )
                            / 2,
                            0.0,
                        ),
                        1.0,
                    ),
                    2,
                )
                signal["rationale"] = f"{signal['rationale']} AI thesis: {ai_decision.thesis}"
                signal["ai_overlay"] = {
                    "provider": self.trading_agent_service.runtime.provider,
                    "model": self.trading_agent_service.runtime.model,
                    "action": ai_decision.action,
                    "confidence": ai_decision.confidence,
                    "thesis": ai_decision.thesis,
                    "risks": ai_decision.risks,
                    "lessons_applied": ai_decision.lessons_applied,
                }
            sizing_decision_source: str | None = None
            if signal["decision"] == "paper_enter":
                sizing_result = self.position_sizing_service.size_trade_candidate(
                    signal_payload=signal,
                    decision_context=decision_context,
                )
                signal["risk_budget"] = sizing_result.get("risk_budget")
                signal["position_sizing"] = sizing_result.get("position_sizing")
                signal["rationale"] = f"{signal['rationale']} {sizing_result['summary']}"
                if sizing_result.get("blocked"):
                    signal["decision"] = "watch"
                    guard_results = signal.get("guard_results") if isinstance(signal.get("guard_results"), dict) else {}
                    existing_reasons = [
                        str(item)
                        for item in guard_results.get("reasons", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    existing_types = [
                        str(item)
                        for item in guard_results.get("types", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    existing_advisories = [
                        str(item)
                        for item in guard_results.get("advisories", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    signal["guard_results"] = {
                        "blocked": True,
                        "reasons": existing_reasons + [str(sizing_result["summary"])],
                        "types": existing_types + ["risk_budget"],
                        "advisories": existing_advisories,
                    }
                    risk_budget_blocked_entries += 1
                    sizing_decision_source = "risk_budget"
                else:
                    signal["size"] = signal["position_sizing"]["size"]
                    effective_stop_price = signal["position_sizing"].get("effective_stop_price")
                    if isinstance(effective_stop_price, (int, float)):
                        signal["stop_price"] = float(effective_stop_price)
            signal["decision_trace"] = self.trading_agent_service.finalize_trade_candidate_trace(
                decision_trace=signal.get("decision_trace"),
                final_action=signal["decision"],
                final_reason=signal["rationale"],
                decision_source=(
                    sizing_decision_source
                    or (
                        "ai_overlay"
                        if ai_decision is not None
                        else ("deterministic_ai_unavailable" if ai_decision_error is not None else initial_decision_source)
                    )
                ),
                confidence=signal.get("decision_confidence"),
                ai_thesis=ai_decision.thesis if ai_decision is not None else None,
            )
            analysis = self.analysis_service.create_run(
                session,
                AnalysisRunCreate(
                    ticker=item.ticker,
                    strategy_version_id=strategy_version_id,
                    watchlist_item_id=item.id,
                    quant_summary=signal["quant_summary"],
                    visual_summary=signal["visual_summary"],
                    combined_score=signal["combined_score"],
                    entry_price=signal["entry_price"],
                    stop_price=signal["stop_price"],
                    target_price=signal["target_price"],
                    risk_reward=signal["risk_reward"],
                    decision=signal["decision"],
                    decision_confidence=signal["decision_confidence"],
                    rationale=signal["rationale"],
                ),
            )
            generated_analyses += 1
            primary_setup_id = item.setup_id or (watchlist.setup_id if watchlist is not None else None)
            primary_hypothesis_id = (
                watchlist.hypothesis_id
                if watchlist is not None and watchlist.hypothesis_id is not None
                else (strategy.hypothesis_id if strategy is not None else None)
            )
            primary_signal_definition_id = self._resolve_primary_signal_definition_id(
                session,
                setup_type=str(signal["visual_summary"].get("setup_type") or signal["quant_summary"].get("setup") or ""),
            )
            signal["hypothesis_id"] = primary_hypothesis_id
            signal["setup_id"] = primary_setup_id
            signal["signal_definition_id"] = primary_signal_definition_id
            signal_record = self.signal_service.create_trade_signal_with_source(
                session,
                SignalCreate(
                    hypothesis_id=primary_hypothesis_id,
                    strategy_id=strategy.id if strategy is not None else None,
                    strategy_version_id=strategy_version_id,
                    setup_id=primary_setup_id,
                    signal_definition_id=primary_signal_definition_id,
                    watchlist_item_id=item.id,
                    ticker=item.ticker,
                    timeframe="1D",
                    signal_type="watchlist_analysis",
                    thesis=signal["rationale"],
                    entry_zone={"price": signal["entry_price"]},
                    stop_zone={"price": signal["stop_price"]},
                    target_zone={"price": signal["target_price"]},
                    signal_context={
                        "decision": signal["decision"],
                        "decision_confidence": signal["decision_confidence"],
                        "quant_summary": signal["quant_summary"],
                        "visual_summary": signal["visual_summary"],
                        "risk_reward": signal["risk_reward"],
                        "base_combined_score": signal.get("base_combined_score"),
                        "base_decision": signal.get("base_decision"),
                        "decision_context": signal.get("decision_context"),
                        "risk_budget": signal.get("risk_budget"),
                        "position_sizing": signal.get("position_sizing"),
                        "research_plan": signal.get("research_plan"),
                        "decision_trace": signal.get("decision_trace"),
                        "score_breakdown": signal.get("score_breakdown"),
                        "guard_results": signal.get("guard_results"),
                        "ai_overlay": signal.get("ai_overlay"),
                        "regime_policy": signal.get("regime_policy"),
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "market_state_snapshot_id": market_state_snapshot.id,
                        "market_state_regime": market_state_snapshot.regime_label,
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                    },
                    quality_score=signal["combined_score"],
                    status="new",
                ),
                event_source="orchestrator_do",
            )
            generated_signals += 1

            existing_open = session.scalar(
                select(Position).where(
                    Position.ticker == item.ticker,
                    Position.status == "open",
                    Position.strategy_version_id == strategy_version_id,
                )
            )
            position_id: int | None = None
            execution_guard: dict | None = None
            step_results: list[dict] = []
            opening_reason = (
                "Autonomous entry from candidate validation."
                if using_candidate_version
                else "Autonomous entry from orchestrator DO phase."
            )
            planned_entry = self.trading_agent_service.plan_trade_candidate_execution(
                ticker=item.ticker,
                strategy_version_id=strategy_version_id,
                signal_id=signal_record.id,
                analysis_run_id=analysis.id,
                signal_payload=signal,
                entry_context={
                    "source": "orchestrator_do",
                    "watchlist_item_id": item.id,
                    "quant_summary": signal["quant_summary"],
                    "visual_summary": signal["visual_summary"],
                    "risk_reward": signal["risk_reward"],
                    "decision_context": signal.get("decision_context"),
                    "risk_budget": signal.get("risk_budget"),
                    "position_sizing": signal.get("position_sizing"),
                    "research_plan": signal.get("research_plan"),
                    "decision_trace": signal.get("decision_trace"),
                    "score_breakdown": signal.get("score_breakdown"),
                    "guard_results": signal.get("guard_results"),
                    "ai_overlay": signal.get("ai_overlay"),
                    "regime_policy": signal.get("regime_policy"),
                    "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                    "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                    "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                    "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                    "execution_mode": "candidate_validation" if using_candidate_version else "default",
                },
                opening_reason=opening_reason,
            )
            final_decision = signal["decision"]
            if planned_entry.should_execute and any(step.tool_name == "positions.open" for step in planned_entry.steps) and existing_open is None:
                step_results = self.agent_tool_gateway_service.execute_plan(session, planned_entry)
                open_step = next((step for step in step_results if step["tool_name"] == "positions.open"), None)
                position_result = open_step["result"] if open_step is not None else None
                if position_result is not None and not position_result.get("skipped"):
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="executed",
                        event_source="orchestrator_do",
                    )
                    position_id = position_result["id"]
                    opened_positions += 1
                    item.state = "entered"
                    journal_decision = "open_paper_position"
                    journal_outcome = "executed"
                else:
                    execution_guard = position_result
                    guard_summary = (
                        execution_guard.get("summary")
                        if isinstance(execution_guard, dict)
                        else "Entry plan did not open a position."
                    )
                    if isinstance(execution_guard, dict):
                        signal["rationale"] = f"{signal['rationale']} {guard_summary}"
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="new",
                        event_source="orchestrator_do",
                    )
                    item.state = "watching"
                    final_decision = "watch"
                    guard_reason = execution_guard.get("reason") if isinstance(execution_guard, dict) else None
                    if guard_reason == "strategy_context_rule":
                        learned_rule_blocked_entries += 1
                        journal_decision = "skip_strategy_context_rule"
                    elif guard_reason == "regime_policy":
                        regime_policy_blocked_entries += 1
                        journal_decision = "skip_regime_policy"
                    elif guard_reason == "portfolio_limit":
                        portfolio_blocked_entries += 1
                        journal_decision = "skip_portfolio_limit"
                    elif guard_reason == "risk_budget_limit":
                        risk_budget_blocked_entries += 1
                        journal_decision = "skip_risk_budget_limit"
                    else:
                        calendar_blocked_entries += 1
                        journal_decision = (
                            "skip_calendar_check_failed"
                            if guard_reason == "calendar_check_failed"
                            else "skip_calendar_risk"
                        )
                    journal_outcome = "watching"
            elif planned_entry.action == "discard":
                self.signal_service.update_trade_signal_status_with_source(
                    session,
                    signal_record.id,
                    status="rejected",
                    rejection_reason="signal_below_threshold",
                    event_source="orchestrator_do",
                )
                item.state = "discarded"
                final_decision = "discard"
                journal_decision = "discard_signal"
                journal_outcome = "rejected"
            else:
                if existing_open is not None and planned_entry.action == "paper_enter":
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="rejected",
                        rejection_reason="existing_open_position",
                        event_source="orchestrator_do",
                    )
                    item.state = "entered"
                    final_decision = "watch"
                    journal_decision = "skip_existing_open_position"
                    journal_outcome = "rejected"
                else:
                    pre_entry_guard_category, pre_entry_guard_journal_decision = self._classify_guard_results(
                        signal.get("guard_results")
                    )
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="new",
                        event_source="orchestrator_do",
                    )
                    item.state = "watching"
                    final_decision = "watch"
                    journal_decision = pre_entry_guard_journal_decision
                    journal_outcome = "watching"

            signal["decision_trace"] = self.trading_agent_service.finalize_trade_candidate_trace(
                decision_trace=signal.get("decision_trace"),
                final_action=final_decision,
                final_reason=signal["rationale"],
                decision_source=(
                    "execution_guard"
                    if execution_guard is not None
                    else "ai_overlay"
                    if ai_decision is not None
                    else initial_decision_source
                ),
                confidence=signal.get("decision_confidence"),
                ai_thesis=(signal.get("ai_overlay") or {}).get("thesis") if isinstance(signal.get("ai_overlay"), dict) else None,
                execution_outcome=final_decision,
            )
            signal_record.signal_context = {
                **dict(signal_record.signal_context or {}),
                "decision": signal.get("decision"),
                "decision_confidence": signal.get("decision_confidence"),
                "risk_budget": signal.get("risk_budget"),
                "position_sizing": signal.get("position_sizing"),
                "research_plan": signal.get("research_plan"),
                "decision_trace": signal.get("decision_trace"),
                "regime_policy": signal.get("regime_policy"),
                "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                "market_state_snapshot_id": market_state_snapshot.id,
                "market_state_regime": market_state_snapshot.regime_label,
                "final_decision": final_decision,
            }
            session.add(signal_record)
            self.decision_context_service.record_trade_candidate_context(
                session,
                signal=signal_record,
                analysis_run=analysis,
                ticker=item.ticker,
                planned_entry_action=planned_entry.action,
                final_decision=final_decision,
                step_results=step_results,
                position_id=position_id,
                execution_guard=execution_guard,
            )
            decision_context_snapshots += 1

            self.journal_service.create_entry(
                session,
                JournalEntryCreate(
                    entry_type="execution_decision",
                    ticker=item.ticker,
                    strategy_id=strategy.id if strategy is not None else None,
                    strategy_version_id=strategy_version_id,
                    position_id=position_id,
                    market_context={
                        "watchlist_id": watchlist.id if watchlist is not None else None,
                        "watchlist_code": watchlist.code if watchlist is not None else None,
                        "market_state_snapshot_id": market_state_snapshot.id,
                        "market_state_regime": market_state_snapshot.regime_label,
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                    },
                    hypothesis=watchlist.hypothesis if watchlist is not None else None,
                    observations={
                        "watchlist_item_id": item.id,
                        "signal_id": signal_record.id,
                        "score": signal["combined_score"],
                        "risk_reward": signal["risk_reward"],
                        "alpha_gap_pct": signal.get("alpha_gap_pct"),
                        "risk_budget": signal.get("risk_budget"),
                        "position_sizing": signal.get("position_sizing"),
                        "research_plan": signal.get("research_plan"),
                        "decision_trace": signal.get("decision_trace"),
                        "score_breakdown": signal.get("score_breakdown"),
                        "guard_results": signal.get("guard_results"),
                        "decision_context": signal.get("decision_context"),
                        "ai_overlay": signal.get("ai_overlay"),
                        "regime_policy": signal.get("regime_policy"),
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "execution_guard": execution_guard,
                    },
                    reasoning=signal["rationale"],
                    decision=journal_decision,
                    outcome=journal_outcome,
                    lessons=(
                        f"Base strategy #{strategy.id} v{strategy_version_id}."
                        if strategy is not None and strategy_version_id is not None
                        else "Decision recorded without linked strategy."
                    ),
                ),
            )

            session.add(item)
            session.commit()

            candidates.append(
                ExecutionCandidateResult(
                    ticker=item.ticker,
                    watchlist_item_id=item.id,
                    analysis_run_id=analysis.id,
                    signal_id=signal_record.id,
                    trade_signal_id=signal_record.id,
                    decision=final_decision,
                    score=signal["combined_score"],
                    position_id=position_id,
                )
            )

        open_positions = session.query(Position).filter(Position.status == "open").count()
        metrics = {
            "active_watchlists": active_watchlists,
            "watchlist_items": len(items),
            "discovered_items": discovery_result["discovered_items"],
            "watchlists_scanned": discovery_result["watchlists_scanned"],
            "discovery_universe_size": discovery_result["universe_size"],
            "prioritized_candidate_items": prioritized_candidate_items,
            "ai_decisions": ai_decisions,
            "ai_unavailable_entries": ai_unavailable_entries,
            "decision_layer_blocked_entries": decision_layer_blocked_entries,
            "calendar_blocked_entries": calendar_blocked_entries,
            "learned_rule_blocked_entries": learned_rule_blocked_entries,
            "regime_policy_blocked_entries": regime_policy_blocked_entries,
            "portfolio_blocked_entries": portfolio_blocked_entries,
            "risk_budget_blocked_entries": risk_budget_blocked_entries,
            "decision_context_snapshots": decision_context_snapshots,
            "generated_analyses": generated_analyses,
            "generated_signals": generated_signals,
            "opened_positions": opened_positions,
            "open_positions": open_positions,
            "auto_exit_evaluated": exit_result.evaluated_positions,
            "auto_exit_closed": exit_result.closed_positions,
            "auto_exit_adjusted": exit_result.adjusted_positions,
        }
        summary = (
            f"DO phase processed {len(items)} watchlist items, generated {generated_analyses} analyses "
            f"opened {opened_positions} paper positions, discovered {discovery_result['discovered_items']} new "
            f"opportunities, prioritized {prioritized_candidate_items} candidate-validation items and "
            f"applied {ai_decisions} AI overlays, degraded {ai_unavailable_entries} entries due to AI unavailability, "
            f"blocked {decision_layer_blocked_entries} decision-layer entries, "
            f"blocked {calendar_blocked_entries} calendar-risk entries, blocked {learned_rule_blocked_entries} "
            f"learned-rule entries, blocked {portfolio_blocked_entries} portfolio-limit entries, "
            f"blocked {risk_budget_blocked_entries} risk-budget entries, "
            f"while auto-closing {exit_result.closed_positions} positions and updating risk on "
            f"{exit_result.adjusted_positions} open positions."
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_do",
                hypothesis=(
                    "Continuously expand the opportunity set, pursue alpha above the benchmark, and keep drawdown "
                    "contained through risk-aware entries."
                ),
                market_context={
                    "benchmark_ticker": discovery_result["benchmark_ticker"],
                    "top_discovery_candidates": discovery_result["top_candidates"],
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                },
                observations=metrics,
                reasoning=summary,
                decision="continue_execution_loop",
            ),
        )
        return OrchestratorDoResponse(
            phase="do",
            status="completed",
            summary=summary,
            metrics=metrics,
            generated_analyses=generated_analyses,
            opened_positions=opened_positions,
            candidates=candidates,
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
            exits=exit_result,
            discovery=discovery_result,
        )

    @staticmethod
    def _resolve_primary_signal_definition_id(session: Session, *, setup_type: str) -> int | None:
        normalized = setup_type.strip().lower()
        code_map = {
            "breakout": "breakout_trigger",
            "pullback": "pullback_resume_confirmation",
            "consolidation": "trend_context_filter",
            "range": "trend_context_filter",
        }
        code = code_map.get(normalized)
        if code is None:
            return None
        signal_definition = session.scalars(select(SignalDefinition).where(SignalDefinition.code == code)).first()
        return signal_definition.id if signal_definition is not None else None

    def run_check_phase(self, session: Session) -> OrchestratorPhaseResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_check",
            pdca_phase="check",
            source_context={"execution_mode": "global"},
        )
        auto_review_result = self.auto_review_service.generate_pending_loss_reviews(session)
        failure_patterns = self.failure_analysis_service.refresh_patterns(session)
        scorecards = self.strategy_scoring_service.recalculate_all(session)
        feature_stats_generated = self.feature_relevance_service.recompute_all(session)
        strategy_context_rules_generated = self.strategy_context_adaptation_service.refresh_rules(session)
        benchmark_snapshot = self.market_data_service.get_snapshot("SPY")
        benchmark_return_pct = round(benchmark_snapshot.month_performance * 100, 2)
        research_tasks_opened = 0
        for scorecard in scorecards:
            strategy = session.get(Strategy, scorecard.strategy_id)
            if strategy is None:
                continue
            if scorecard.signals_count < 2 and scorecard.closed_trades_count == 0:
                _, created = self.research_service.ensure_low_activity_task(
                    session,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    signals_count=scorecard.signals_count,
                    closed_trades_count=scorecard.closed_trades_count,
                )
                if created:
                    research_tasks_opened += 1
            if (
                scorecard.closed_trades_count >= 1
                and (
                    (scorecard.avg_return_pct is not None and scorecard.avg_return_pct < benchmark_return_pct)
                    or (scorecard.max_drawdown_pct is not None and scorecard.max_drawdown_pct <= -5)
                )
            ):
                _, created = self.research_service.ensure_alpha_improvement_task(
                    session,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    avg_return_pct=scorecard.avg_return_pct,
                    benchmark_return_pct=benchmark_return_pct,
                    max_drawdown_pct=scorecard.max_drawdown_pct,
                )
                if created:
                    research_tasks_opened += 1
        active_strategies = session.query(Strategy).filter(Strategy.status.in_(["paper", "live", "research"])).count()
        total_analyses = session.query(AnalysisRun).count()
        closed_positions = session.query(Position).filter(Position.status == "closed").count()
        open_positions = session.query(Position).filter(Position.status == "open").count()
        winning_positions = session.query(Position).filter(Position.status == "closed", Position.pnl_pct > 0).count()
        losing_positions = session.query(Position).filter(Position.status == "closed", Position.pnl_pct <= 0).count()
        pending_reviews = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        decision_context_snapshots = session.query(DecisionContextSnapshot).count()

        closed_rows = session.query(Position.pnl_pct, Position.max_drawdown_pct).filter(Position.status == "closed").all()
        avg_pnl_pct = round(sum((row[0] or 0.0) for row in closed_rows) / len(closed_rows), 2) if closed_rows else 0.0
        avg_drawdown_pct = round(sum((row[1] or 0.0) for row in closed_rows) / len(closed_rows), 2) if closed_rows else 0.0

        metrics = {
            "active_strategies": active_strategies,
            "total_analyses": total_analyses,
            "closed_positions": closed_positions,
            "open_positions": open_positions,
            "winning_positions": winning_positions,
            "losing_positions": losing_positions,
            "pending_reviews": pending_reviews,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_drawdown_pct": avg_drawdown_pct,
            "benchmark_return_pct": benchmark_return_pct,
            "portfolio_alpha_gap_pct": round(avg_pnl_pct - benchmark_return_pct, 2),
            "auto_generated_reviews": auto_review_result.generated_reviews,
            "failure_patterns_tracked": len(failure_patterns),
            "scorecards_generated": len(scorecards),
            "decision_context_snapshots": decision_context_snapshots,
            "feature_stats_generated": feature_stats_generated,
            "strategy_context_rules_generated": strategy_context_rules_generated,
            "research_tasks_opened": research_tasks_opened,
        }
        summary = (
            f"CHECK phase evaluated {closed_positions} closed trades with {winning_positions} wins, "
            f"{losing_positions} losses, {pending_reviews} pending reviews and "
            f"{auto_review_result.generated_reviews} auto-generated reviews."
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_check",
                key="latest_check_summary",
                content=summary,
                meta=metrics,
                importance=0.7,
            ),
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_check",
                hypothesis=(
                    "The system should outperform the benchmark while containing drawdown and convert repeated "
                    "outcomes into reusable lessons."
                ),
                market_context={
                    "benchmark_ticker": "SPY",
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                },
                observations=metrics,
                reasoning=summary,
                decision="review_outcomes",
                lessons=(
                    "Prioritize strategy changes that improve alpha relative to the benchmark without paying for it "
                    "through excessive drawdown."
                ),
            ),
        )
        return OrchestratorPhaseResponse(
            phase="check",
            status="completed",
            summary=summary,
            metrics=metrics,
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
        )

    def run_act_phase(self, session: Session) -> OrchestratorActResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_act",
            pdca_phase="act",
            source_context={"execution_mode": "global"},
        )
        health_result = self.strategy_evolution_service.evaluate_failure_patterns(session)
        candidate_result = self.strategy_evolution_service.evaluate_candidate_versions(session)
        candidate_research_tasks_opened = 0
        repeated_candidate_rejections = self.strategy_evolution_service.find_repeated_candidate_rejections(session)
        for repeated_rejection in repeated_candidate_rejections:
            strategy = session.get(Strategy, repeated_rejection["strategy_id"])
            if strategy is None:
                continue
            _, created = self.research_service.ensure_candidate_research_task(
                session,
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                rejected_candidate_count=repeated_rejection["rejected_candidate_count"],
                candidate_version_ids=repeated_rejection["candidate_version_ids"],
            )
            if created:
                candidate_research_tasks_opened += 1
        promoted_strategy_ids = {item["strategy_id"] for item in candidate_result.get("promotions", [])}
        lab_result = self.strategy_lab_service.evolve_from_success_patterns(
            session,
            excluded_strategy_ids=promoted_strategy_ids,
        )
        open_research_tasks = len([task for task in self.research_service.list_tasks(session) if task.status in ["open", "in_progress"]])
        metrics = {
            "forked_variants": health_result["forked_variants"],
            "promoted_candidates": candidate_result["promoted_candidates"],
            "rejected_candidates": candidate_result["rejected_candidates"],
            "degraded_strategies": health_result["degraded_strategies"],
            "archived_strategies": health_result["archived_strategies"],
            "candidate_research_tasks_opened": candidate_research_tasks_opened,
            "generated_variants": lab_result["generated_variants"],
            "skipped_candidates": lab_result["skipped_candidates"],
            "open_research_tasks": open_research_tasks,
        }
        summary = (
            f"ACT phase forked {health_result['forked_variants']} candidate variants, promoted "
            f"{candidate_result['promoted_candidates']} candidates, rejected {candidate_result['rejected_candidates']} "
            f"candidates, opened {candidate_research_tasks_opened} candidate-research tasks, "
            f"degraded {health_result['degraded_strategies']} strategies, archived {health_result['archived_strategies']}, "
            f"generated {lab_result['generated_variants']} proactive strategy variants and skipped "
            f"{lab_result['skipped_candidates']} candidates."
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_act",
                market_context={
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                },
                observations=metrics,
                reasoning=summary,
                decision="promote_success_patterns",
                lessons="Use failure-pattern feedback to fork weaker strategies and promote candidates that improve alpha and resilience.",
            ),
        )
        return OrchestratorActResponse(
            phase="act",
            status="completed",
            summary=summary,
            metrics=metrics,
            generated_variants=lab_result["generated_variants"],
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
        )
