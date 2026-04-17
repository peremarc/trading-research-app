from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models.failure_pattern import FailurePattern
from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.db.models.pdca import PDCACycle
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate, PDCACycleCreate


class JournalRepository:
    def list(self, session: Session) -> list[JournalEntry]:
        statement = select(JournalEntry).order_by(JournalEntry.event_time.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: JournalEntryCreate) -> JournalEntry:
        entry = JournalEntry(**payload.model_dump())
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


class MemoryRepository:
    def list(self, session: Session) -> list[MemoryItem]:
        statement = select(MemoryItem).order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: MemoryItemCreate) -> MemoryItem:
        item = MemoryItem(**payload.model_dump())
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def retrieve(self, session: Session, scope: str, limit: int = 10) -> list[MemoryItem]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.scope == scope)
            .order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc())
            .limit(limit)
        )
        return list(session.scalars(statement).all())


class FailurePatternRepository:
    def list(self, session: Session) -> list[FailurePattern]:
        statement = select(FailurePattern).order_by(FailurePattern.occurrences.desc(), FailurePattern.updated_at.desc())
        return list(session.scalars(statement).all())

    def list_for_strategy(self, session: Session, strategy_id: int) -> list[FailurePattern]:
        statement = (
            select(FailurePattern)
            .where(FailurePattern.strategy_id == strategy_id)
            .order_by(FailurePattern.occurrences.desc(), FailurePattern.updated_at.desc())
        )
        return list(session.scalars(statement).all())

    def get_by_signature(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_version_id: int | None,
        pattern_signature: str,
    ) -> FailurePattern | None:
        statement = select(FailurePattern).where(
            FailurePattern.strategy_id == strategy_id,
            FailurePattern.strategy_version_id == strategy_version_id,
            FailurePattern.pattern_signature == pattern_signature,
        )
        return session.scalars(statement).first()

    def create(self, session: Session, payload: dict) -> FailurePattern:
        pattern = FailurePattern(**payload)
        session.add(pattern)
        session.commit()
        session.refresh(pattern)
        return pattern

    def update(self, session: Session, pattern: FailurePattern) -> FailurePattern:
        session.add(pattern)
        session.commit()
        session.refresh(pattern)
        return pattern


class PDCACycleRepository:
    def list(self, session: Session) -> list[PDCACycle]:
        statement = select(PDCACycle).order_by(PDCACycle.cycle_date.desc(), PDCACycle.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: PDCACycleCreate) -> PDCACycle:
        cycle = PDCACycle(
            cycle_date=payload.cycle_date,
            phase=payload.phase,
            status=payload.status,
            summary=payload.summary,
            context=payload.context,
        )
        session.add(cycle)
        session.commit()
        session.refresh(cycle)
        return cycle
