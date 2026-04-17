from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.models.research_task import ResearchTask
from app.db.models.signal import Signal
from app.domains.market.schemas import AnalysisRunCreate, ResearchTaskCreate, SignalCreate


class AnalysisRepository:
    def list(self, session: Session) -> list[AnalysisRun]:
        statement = select(AnalysisRun).order_by(AnalysisRun.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: AnalysisRunCreate) -> AnalysisRun:
        analysis = AnalysisRun(**payload.model_dump())
        session.add(analysis)
        session.commit()
        session.refresh(analysis)
        return analysis


class SignalRepository:
    def list(self, session: Session) -> list[Signal]:
        statement = select(Signal).order_by(Signal.signal_time.desc(), Signal.created_at.desc())
        return list(session.scalars(statement).all())

    def get(self, session: Session, signal_id: int) -> Signal | None:
        return session.get(Signal, signal_id)

    def create(self, session: Session, payload: SignalCreate) -> Signal:
        signal = Signal(**payload.model_dump())
        session.add(signal)
        session.commit()
        session.refresh(signal)
        return signal

    def update_status(
        self,
        session: Session,
        signal_id: int,
        *,
        status: str,
        rejection_reason: str | None = None,
    ) -> Signal:
        signal = self.get(session, signal_id)
        if signal is None:
            raise ValueError("Signal not found")

        signal.status = status
        signal.rejection_reason = rejection_reason
        session.commit()
        session.refresh(signal)
        return signal


class ResearchTaskRepository:
    def list(self, session: Session) -> list[ResearchTask]:
        statement = select(ResearchTask).order_by(ResearchTask.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: ResearchTaskCreate) -> ResearchTask:
        task = ResearchTask(**payload.model_dump())
        session.add(task)
        session.commit()
        session.refresh(task)
        return task

    def find_open_by_signature(
        self,
        session: Session,
        *,
        strategy_id: int | None,
        task_type: str,
        title: str,
    ) -> ResearchTask | None:
        statement = select(ResearchTask).where(
            ResearchTask.strategy_id == strategy_id,
            ResearchTask.task_type == task_type,
            ResearchTask.title == title,
            ResearchTask.status.in_(["open", "in_progress"]),
        )
        return session.scalars(statement).first()

    def complete(self, session: Session, task_id: int, result_summary: str) -> ResearchTask:
        task = session.get(ResearchTask, task_id)
        if task is None:
            raise ValueError("Research task not found")

        task.status = "completed"
        task.result_summary = result_summary
        task.completed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(task)
        return task
