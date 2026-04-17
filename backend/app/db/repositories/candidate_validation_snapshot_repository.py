from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot


class CandidateValidationSnapshotRepository:
    def create(self, session: Session, payload: dict) -> CandidateValidationSnapshot:
        snapshot = CandidateValidationSnapshot(**payload)
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot

    def list_latest(self, session: Session) -> list[CandidateValidationSnapshot]:
        statement = select(CandidateValidationSnapshot).order_by(
            CandidateValidationSnapshot.strategy_version_id.asc(),
            CandidateValidationSnapshot.generated_at.desc(),
            CandidateValidationSnapshot.id.desc(),
        )
        snapshots = list(session.scalars(statement).all())
        latest: dict[int, CandidateValidationSnapshot] = {}
        for snapshot in snapshots:
            latest.setdefault(snapshot.strategy_version_id, snapshot)
        return list(latest.values())

    def list_latest_for_strategy(self, session: Session, strategy_id: int) -> list[CandidateValidationSnapshot]:
        snapshots = [
            snapshot
            for snapshot in self.list_latest(session)
            if snapshot.strategy_id == strategy_id
        ]
        snapshots.sort(key=lambda snapshot: snapshot.generated_at, reverse=True)
        return snapshots

    def list_for_strategy(self, session: Session, strategy_id: int) -> list[CandidateValidationSnapshot]:
        statement = (
            select(CandidateValidationSnapshot)
            .where(CandidateValidationSnapshot.strategy_id == strategy_id)
            .order_by(
                CandidateValidationSnapshot.generated_at.desc(),
                CandidateValidationSnapshot.id.desc(),
            )
        )
        return list(session.scalars(statement).all())
