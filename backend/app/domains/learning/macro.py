from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings, get_settings
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.domains.learning.schemas import MacroContextRead, MacroSignalCreate
from app.providers.macro_indicators import MacroIndicatorsService


class MacroContextService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        indicators_service: MacroIndicatorsService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.indicators_service = indicators_service or MacroIndicatorsService(self.settings)

    def list_signals(self, session: Session, limit: int = 20) -> list[MemoryItem]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.scope == "macro")
            .order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc())
            .limit(limit)
        )
        return list(session.scalars(statement).all())

    def create_signal(self, session: Session, payload: MacroSignalCreate) -> MemoryItem:
        meta = {
            "regime": payload.regime,
            "relevance": payload.relevance,
            "tickers": payload.tickers,
            "timeframe": payload.timeframe,
            "scenario": payload.scenario,
            "source": payload.source,
            "evidence": payload.evidence,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        item = MemoryItem(
            memory_type="macro_signal",
            scope="macro",
            key=payload.key,
            content=payload.content,
            meta=meta,
            importance=payload.importance,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
        )
        session.add(item)
        session.commit()
        session.refresh(item)

        session.add(
            JournalEntry(
                entry_type="macro_signal",
                market_context={
                    "regime": payload.regime,
                    "relevance": payload.relevance,
                    "timeframe": payload.timeframe,
                    "source": payload.source,
                },
                observations={
                    "tickers": payload.tickers,
                    "scenario": payload.scenario,
                    "evidence": payload.evidence,
                },
                reasoning=payload.content,
                decision=payload.key,
                expectations=payload.scenario,
                outcome="recorded",
            )
        )
        session.commit()
        return item

    def get_context(self, session: Session, limit: int = 8) -> MacroContextRead:
        now = datetime.now(timezone.utc)
        statement = (
            select(MemoryItem)
            .where(
                MemoryItem.scope == "macro",
                or_(MemoryItem.valid_from.is_(None), MemoryItem.valid_from <= now),
                or_(MemoryItem.valid_to.is_(None), MemoryItem.valid_to >= now),
            )
            .order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc())
            .limit(limit)
        )
        signals = list(session.scalars(statement).all())
        regime_counts: dict[str, int] = {}
        relevance_counts: dict[str, int] = {}
        tickers: set[str] = set()

        for item in signals:
            regime = str(item.meta.get("regime") or "unspecified")
            relevance = str(item.meta.get("relevance") or "general")
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            relevance_counts[relevance] = relevance_counts.get(relevance, 0) + 1
            for ticker in item.meta.get("tickers") or []:
                if isinstance(ticker, str) and ticker.strip():
                    tickers.add(ticker.strip().upper())

        ordered_regimes = sorted(regime_counts, key=lambda key: (-regime_counts[key], key))
        ordered_relevance = sorted(relevance_counts, key=lambda key: (-relevance_counts[key], key))
        indicators = self.indicators_service.list_indicators()
        top_signals = [
            {
                "key": item.key,
                "content": item.content,
                "importance": item.importance,
                "regime": item.meta.get("regime"),
                "relevance": item.meta.get("relevance"),
                "timeframe": item.meta.get("timeframe"),
                "scenario": item.meta.get("scenario"),
                "tickers": item.meta.get("tickers") or [],
                "source": item.meta.get("source"),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in signals
        ]

        indicator_summary = self._summarize_indicators(indicators)
        if not top_signals:
            summary = (
                "No hay señales macro persistidas todavía. Conviene registrar escenarios, riesgos de régimen "
                "y eventos geopolíticos para que el bot pueda usarlos en research y ejecución."
            )
            if indicator_summary:
                summary += f" Indicadores macro observados: {indicator_summary}."
        else:
            summary = (
                "Contexto macro activo: "
                + "; ".join(
                    f"{signal['key']} ({signal['regime'] or 'sin regimen'}, imp {signal['importance']:.2f})"
                    for signal in top_signals[:3]
                )
                + "."
            )
            if indicator_summary:
                summary += f" Dashboard macro: {indicator_summary}."

        return MacroContextRead(
            summary=summary,
            active_regimes=ordered_regimes[:5],
            relevance_tags=ordered_relevance[:5],
            tracked_tickers=sorted(tickers),
            signals=top_signals,
            indicators=indicators,
        )

    @staticmethod
    def _summarize_indicators(indicators: list[dict]) -> str:
        available = [item for item in indicators if item.get("status") == "available" and item.get("value") is not None]
        if not available:
            return ""
        snippets: list[str] = []
        for indicator in available[:6]:
            label = str(indicator.get("label") or indicator.get("key") or "Indicador")
            value = indicator.get("value")
            unit = str(indicator.get("unit") or "").strip()
            interpretation = str(indicator.get("interpretation") or "").strip()
            if unit == "%":
                formatted_value = f"{float(value):.2f}%"
            elif unit == "score":
                formatted_value = f"{float(value):.0f}"
            else:
                formatted_value = f"{float(value):.2f}"
            if interpretation:
                snippets.append(f"{label} {formatted_value} ({interpretation})")
            else:
                snippets.append(f"{label} {formatted_value}")
        return "; ".join(snippets)
