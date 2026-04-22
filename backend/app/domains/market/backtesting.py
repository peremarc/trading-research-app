from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.external_backtest_run import ExternalBacktestRun
from app.db.models.memory import MemoryItem
from app.db.models.research_task import ResearchTask
from app.db.models.strategy import Strategy, StrategyVersion
from app.domains.market.schemas import (
    ResearchBacktestBatchSyncRead,
    ResearchBacktestCreate,
    ResearchBacktestProviderContextRead,
    ResearchBacktestRead,
    ResearchBacktestSyncErrorRead,
)
from app.domains.system.events import EventLogService
from app.providers.backtesting import BacktestingProviderError, RemoteBacktestingProvider

NON_TERMINAL_BACKTEST_STATUSES = frozenset({"queued", "running", "cancel_requested"})
TERMINAL_BACKTEST_STATUSES = frozenset({"completed", "failed", "cancelled"})
SKILL_CANDIDATE_MEMORY_TYPE = "skill_candidate"


class ResearchBacktestNotFoundError(LookupError):
    pass


class ResearchBacktestDependencyError(ValueError):
    pass


class ExternalBacktestRunRepository:
    def create(self, session: Session, payload: dict[str, Any]) -> ExternalBacktestRun:
        record = ExternalBacktestRun(**payload)
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    def get(self, session: Session, backtest_id: int) -> ExternalBacktestRun | None:
        return session.get(ExternalBacktestRun, backtest_id)

    def list(
        self,
        session: Session,
        *,
        status: str | None = None,
        strategy_id: int | None = None,
        research_task_id: int | None = None,
        skill_candidate_id: int | None = None,
    ) -> list[ExternalBacktestRun]:
        statement = select(ExternalBacktestRun).order_by(
            ExternalBacktestRun.submitted_at.desc(),
            ExternalBacktestRun.id.desc(),
        )
        if status:
            statement = statement.where(ExternalBacktestRun.status == status)
        if strategy_id is not None:
            statement = statement.where(ExternalBacktestRun.strategy_id == strategy_id)
        if research_task_id is not None:
            statement = statement.where(ExternalBacktestRun.research_task_id == research_task_id)
        if skill_candidate_id is not None:
            statement = statement.where(ExternalBacktestRun.skill_candidate_id == skill_candidate_id)
        return list(session.scalars(statement).all())

    def list_non_terminal(self, session: Session, *, limit: int | None = None) -> list[ExternalBacktestRun]:
        statement = (
            select(ExternalBacktestRun)
            .where(ExternalBacktestRun.status.in_(tuple(NON_TERMINAL_BACKTEST_STATUSES)))
            .order_by(ExternalBacktestRun.submitted_at.asc(), ExternalBacktestRun.id.asc())
        )
        if limit is not None:
            statement = statement.limit(max(int(limit), 1))
        return list(session.scalars(statement).all())

    def save(self, session: Session, record: ExternalBacktestRun) -> ExternalBacktestRun:
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


class ResearchBacktestService:
    def __init__(
        self,
        *,
        repository: ExternalBacktestRunRepository | None = None,
        provider: object | None = None,
        settings: Settings | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or ExternalBacktestRunRepository()
        self.provider = provider
        self.settings = settings or get_settings()
        self.event_log_service = event_log_service or EventLogService()

    def list_runs(
        self,
        session: Session,
        *,
        status: str | None = None,
        strategy_id: int | None = None,
        research_task_id: int | None = None,
        skill_candidate_id: int | None = None,
    ) -> list[ResearchBacktestRead]:
        return [
            self.to_read_model(record)
            for record in self.repository.list(
                session,
                status=status,
                strategy_id=strategy_id,
                research_task_id=research_task_id,
                skill_candidate_id=skill_candidate_id,
            )
        ]

    def get_run(self, session: Session, backtest_id: int) -> ResearchBacktestRead:
        record = self.repository.get(session, backtest_id)
        if record is None:
            raise ResearchBacktestNotFoundError("Backtest link not found.")
        return self.to_read_model(record)

    def submit_run(self, session: Session, payload: ResearchBacktestCreate) -> ResearchBacktestRead:
        skill_candidate = self._resolve_skill_candidate(session, payload.skill_candidate_id)
        candidate_strategy_version_id = _as_int((skill_candidate.meta or {}).get("strategy_version_id")) if skill_candidate else None
        if (
            payload.strategy_version_id is not None
            and candidate_strategy_version_id is not None
            and payload.strategy_version_id != candidate_strategy_version_id
        ):
            raise ResearchBacktestDependencyError("strategy_version_id does not match skill_candidate_id.")
        strategy, strategy_version = self._resolve_strategy_context(
            session,
            strategy_id=payload.strategy_id,
            strategy_version_id=payload.strategy_version_id or candidate_strategy_version_id,
        )
        research_task = self._resolve_research_task(session, payload.research_task_id)
        spec = self._build_remote_spec(
            payload=payload,
            strategy=strategy,
            strategy_version=strategy_version,
            research_task=research_task,
            skill_candidate=skill_candidate,
        )
        remote_run = self._provider().submit_backtest(spec)

        record = self.repository.create(
            session,
            {
                "remote_run_id": str(remote_run["run_id"]),
                "provider": "backtesting",
                "status": str(remote_run.get("status") or "queued"),
                "engine": remote_run.get("engine"),
                "spec_version": remote_run.get("spec_version"),
                "dataset_version": remote_run.get("dataset_version"),
                "strategy_id": strategy.id if strategy is not None else research_task.strategy_id if research_task else None,
                "strategy_version_id": strategy_version.id if strategy_version is not None else None,
                "research_task_id": research_task.id if research_task is not None else None,
                "skill_candidate_id": skill_candidate.id if skill_candidate is not None else None,
                "linked_entity_type": spec.get("source", {}).get("linked_entity_type"),
                "linked_entity_id": spec.get("source", {}).get("linked_entity_id"),
                "target_type": spec.get("target", {}).get("type"),
                "target_code": spec.get("target", {}).get("code"),
                "target_version": spec.get("target", {}).get("version"),
                "requested_by": spec.get("source", {}).get("requested_by"),
                "source_app": spec.get("source", {}).get("source_app"),
                "latest_run_payload": remote_run,
                "summary_metrics": {},
                "artifact_refs": [],
                "backtest_spec": spec,
                "error_message": remote_run.get("error_message"),
                "started_at": _parse_remote_datetime(remote_run.get("started_at")),
                "completed_at": _parse_remote_datetime(remote_run.get("completed_at")),
                "last_synced_at": datetime.now(timezone.utc),
            },
        )
        self.event_log_service.record(
            session,
            event_type="external_backtest.submitted",
            entity_type="external_backtest_run",
            entity_id=record.id,
            source="research_backtesting",
            pdca_phase_hint="check",
            payload={
                "remote_run_id": record.remote_run_id,
                "status": record.status,
                "strategy_id": record.strategy_id,
                "strategy_version_id": record.strategy_version_id,
                "research_task_id": record.research_task_id,
                "skill_candidate_id": record.skill_candidate_id,
            },
        )
        return self.to_read_model(record)

    def sync_non_terminal_runs(
        self,
        session: Session,
        *,
        limit: int | None = None,
        emit_events: bool = False,
    ) -> ResearchBacktestBatchSyncRead:
        records = self.repository.list_non_terminal(session, limit=limit)
        items: list[ResearchBacktestRead] = []
        errors: list[ResearchBacktestSyncErrorRead] = []
        updated = 0
        terminal = 0

        for record in records:
            try:
                read_model, changed, reached_terminal = self._sync_record(session, record, emit_events=emit_events)
            except BacktestingProviderError as exc:
                record.error_message = str(exc)
                record.last_synced_at = datetime.now(timezone.utc)
                self.repository.save(session, record)
                errors.append(
                    ResearchBacktestSyncErrorRead(
                        backtest_id=record.id,
                        remote_run_id=record.remote_run_id,
                        error=str(exc),
                    )
                )
                continue
            items.append(read_model)
            if changed:
                updated += 1
            if reached_terminal:
                terminal += 1

        return ResearchBacktestBatchSyncRead(
            attempted=len(records),
            updated=updated,
            terminal=terminal,
            failed=len(errors),
            items=items,
            errors=errors,
        )

    def sync_run(self, session: Session, backtest_id: int) -> ResearchBacktestRead:
        record = self._get_record(session, backtest_id)
        read_model, _, _ = self._sync_record(session, record, emit_events=True, force_event=True)
        self.event_log_service.record(
            session,
            event_type="external_backtest.synced",
            entity_type="external_backtest_run",
            entity_id=record.id,
            source="research_backtesting",
            pdca_phase_hint="check",
            payload={
                "remote_run_id": record.remote_run_id,
                "status": record.status,
                "dataset_version": record.dataset_version,
            },
        )
        return read_model

    def cancel_run(self, session: Session, backtest_id: int) -> ResearchBacktestRead:
        record = self._get_record(session, backtest_id)
        run_payload = self._provider().cancel_backtest(record.remote_run_id)
        self._apply_remote_snapshot(session, record, run_payload=run_payload)
        self.event_log_service.record(
            session,
            event_type="external_backtest.cancel_requested",
            entity_type="external_backtest_run",
            entity_id=record.id,
            source="research_backtesting",
            pdca_phase_hint="check",
            payload={
                "remote_run_id": record.remote_run_id,
                "status": record.status,
            },
        )
        return self.to_read_model(record)

    def provider_context(self) -> ResearchBacktestProviderContextRead:
        configured = bool(
            self.settings.backtesting_enabled
            and self.settings.backtesting_provider == "remote_service"
            and self.settings.backtesting_base_url.strip()
        )
        if not configured:
            return ResearchBacktestProviderContextRead(
                configured=False,
                provider=self.settings.backtesting_provider,
                base_url=None,
                capabilities={},
                ai_context={},
            )
        provider = self._provider()
        return ResearchBacktestProviderContextRead(
            configured=True,
            provider=self.settings.backtesting_provider,
            base_url=provider.base_url,
            capabilities=provider.get_capabilities(),
            ai_context=provider.get_ai_context(),
        )

    def to_read_model(self, record: ExternalBacktestRun) -> ResearchBacktestRead:
        return ResearchBacktestRead(
            id=record.id,
            remote_run_id=record.remote_run_id,
            provider=record.provider,
            status=record.status,
            engine=record.engine,
            spec_version=record.spec_version,
            dataset_version=record.dataset_version,
            strategy_id=record.strategy_id,
            strategy_version_id=record.strategy_version_id,
            research_task_id=record.research_task_id,
            skill_candidate_id=record.skill_candidate_id,
            linked_entity_type=record.linked_entity_type,
            linked_entity_id=record.linked_entity_id,
            target_type=record.target_type,
            target_code=record.target_code,
            target_version=record.target_version,
            requested_by=record.requested_by,
            source_app=record.source_app,
            latest_run_payload=record.latest_run_payload or {},
            summary_metrics=record.summary_metrics or {},
            artifact_refs=list(record.artifact_refs or []),
            backtest_spec=record.backtest_spec or {},
            error_message=record.error_message,
            submitted_at=record.submitted_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            last_synced_at=record.last_synced_at,
            updated_at=record.updated_at,
            remote_urls=self._build_remote_urls(record.remote_run_id),
        )

    def _provider(self):
        if self.provider is not None:
            return self.provider
        if not self.settings.backtesting_enabled:
            raise BacktestingProviderError("Backtesting provider is disabled.")
        if self.settings.backtesting_provider != "remote_service":
            raise BacktestingProviderError(
                f"Unsupported backtesting provider '{self.settings.backtesting_provider}'."
            )
        if not self.settings.backtesting_base_url.strip():
            raise BacktestingProviderError("Backtesting provider is not configured.")
        self.provider = RemoteBacktestingProvider(
            base_url=self.settings.backtesting_base_url,
            api_key=self.settings.backtesting_api_key,
            timeout_seconds=self.settings.backtesting_timeout_seconds,
        )
        return self.provider

    def _get_record(self, session: Session, backtest_id: int) -> ExternalBacktestRun:
        record = self.repository.get(session, backtest_id)
        if record is None:
            raise ResearchBacktestNotFoundError("Backtest link not found.")
        return record

    def _resolve_strategy_context(
        self,
        session: Session,
        *,
        strategy_id: int | None,
        strategy_version_id: int | None,
    ) -> tuple[Strategy | None, StrategyVersion | None]:
        strategy_version = session.get(StrategyVersion, strategy_version_id) if strategy_version_id is not None else None
        if strategy_version_id is not None and strategy_version is None:
            raise ResearchBacktestDependencyError("Strategy version not found.")

        resolved_strategy_id = strategy_version.strategy_id if strategy_version is not None else strategy_id
        if strategy_id is not None and strategy_version is not None and strategy_version.strategy_id != strategy_id:
            raise ResearchBacktestDependencyError("strategy_id does not match strategy_version_id.")

        strategy = session.get(Strategy, resolved_strategy_id) if resolved_strategy_id is not None else None
        if resolved_strategy_id is not None and strategy is None:
            raise ResearchBacktestDependencyError("Strategy not found.")
        return strategy, strategy_version

    @staticmethod
    def _resolve_research_task(session: Session, research_task_id: int | None) -> ResearchTask | None:
        if research_task_id is None:
            return None
        research_task = session.get(ResearchTask, research_task_id)
        if research_task is None:
            raise ResearchBacktestDependencyError("Research task not found.")
        return research_task

    @staticmethod
    def _resolve_skill_candidate(session: Session, skill_candidate_id: int | None) -> MemoryItem | None:
        if skill_candidate_id is None:
            return None
        skill_candidate = session.get(MemoryItem, skill_candidate_id)
        if skill_candidate is None or skill_candidate.memory_type != SKILL_CANDIDATE_MEMORY_TYPE:
            raise ResearchBacktestDependencyError("Skill candidate not found.")
        return skill_candidate

    def _build_remote_spec(
        self,
        *,
        payload: ResearchBacktestCreate,
        strategy: Strategy | None,
        strategy_version: StrategyVersion | None,
        research_task: ResearchTask | None,
        skill_candidate: MemoryItem | None,
    ) -> dict[str, Any]:
        spec = deepcopy(payload.spec)
        source = spec.setdefault("source", {})
        target = spec.setdefault("target", {})
        metadata = spec.setdefault("metadata", {})

        if not isinstance(source, dict) or not isinstance(target, dict) or not isinstance(metadata, dict):
            raise ResearchBacktestDependencyError("spec.source, spec.target and spec.metadata must be objects when provided.")

        source.setdefault("source_app", self.settings.backtesting_source_app)
        if payload.requested_by:
            source["requested_by"] = payload.requested_by
        if payload.reason:
            source["reason"] = payload.reason

        skill_candidate_meta = dict(skill_candidate.meta or {}) if skill_candidate is not None else {}
        skill_candidate_code = (
            str(skill_candidate_meta.get("target_skill_code") or skill_candidate.key).strip()
            if skill_candidate is not None
            else None
        ) or None

        linked_entity_type = payload.linked_entity_type
        linked_entity_id = payload.linked_entity_id
        if linked_entity_type is None and skill_candidate is not None:
            linked_entity_type = "skill_candidate"
            linked_entity_id = str(skill_candidate.id)
        elif linked_entity_type is None and research_task is not None:
            linked_entity_type = "research_task"
            linked_entity_id = str(research_task.id)
        elif linked_entity_type is None and strategy_version is not None:
            linked_entity_type = "strategy_version"
            linked_entity_id = str(strategy_version.id)
        elif linked_entity_type is None and strategy is not None:
            linked_entity_type = "strategy"
            linked_entity_id = str(strategy.id)
        if linked_entity_type is not None:
            source["linked_entity_type"] = linked_entity_type
        if linked_entity_id is not None:
            source["linked_entity_id"] = linked_entity_id

        if skill_candidate is not None:
            target.setdefault("type", "skill_candidate")
            if skill_candidate_code is not None:
                target.setdefault("code", skill_candidate_code)
                target.setdefault("skill_candidate_code", skill_candidate_code)
        elif strategy is not None and strategy_version is not None:
            target.setdefault("type", "strategy_version")
            target.setdefault("code", strategy.code)
            target.setdefault("version", str(strategy_version.version))
            target.setdefault("strategy_code", strategy.code)
        elif research_task is not None:
            target.setdefault("type", "research_task")
            target.setdefault("code", f"research_task:{research_task.id}")

        if research_task is not None:
            target.setdefault("research_task_code", f"research_task:{research_task.id}")

        metadata["trading_research_app"] = {
            "strategy_id": strategy.id if strategy is not None else research_task.strategy_id if research_task else None,
            "strategy_version_id": strategy_version.id if strategy_version is not None else None,
            "research_task_id": research_task.id if research_task is not None else None,
            "skill_candidate_id": skill_candidate.id if skill_candidate is not None else None,
            "skill_candidate_key": skill_candidate.key if skill_candidate is not None else None,
            "linked_entity_type": source.get("linked_entity_type"),
            "linked_entity_id": source.get("linked_entity_id"),
        }
        return spec

    def _apply_remote_snapshot(
        self,
        session: Session,
        record: ExternalBacktestRun,
        *,
        run_payload: dict[str, Any],
        metrics_payload: dict[str, Any] | None = None,
        artifacts_payload: dict[str, Any] | None = None,
    ) -> ExternalBacktestRun:
        record.status = str(run_payload.get("status") or record.status)
        record.engine = run_payload.get("engine") or record.engine
        record.spec_version = run_payload.get("spec_version") or record.spec_version
        record.dataset_version = run_payload.get("dataset_version") or record.dataset_version
        record.requested_by = run_payload.get("requested_by") or record.requested_by
        record.source_app = run_payload.get("source_app") or record.source_app
        record.error_message = run_payload.get("error_message")
        record.latest_run_payload = run_payload
        record.started_at = _parse_remote_datetime(run_payload.get("started_at")) or record.started_at
        record.completed_at = _parse_remote_datetime(run_payload.get("completed_at")) or record.completed_at
        record.last_synced_at = datetime.now(timezone.utc)

        if metrics_payload is not None:
            record.summary_metrics = {
                "dataset_version": metrics_payload.get("dataset_version"),
                "split_summary": metrics_payload.get("split_summary"),
                "scopes": {
                    str(item.get("scope")): item.get("payload", {})
                    for item in metrics_payload.get("items", [])
                    if isinstance(item, dict) and item.get("scope")
                },
            }
            if record.dataset_version is None:
                record.dataset_version = metrics_payload.get("dataset_version")
        if artifacts_payload is not None:
            items = artifacts_payload.get("items", [])
            record.artifact_refs = items if isinstance(items, list) else []

        return self.repository.save(session, record)

    def _sync_record(
        self,
        session: Session,
        record: ExternalBacktestRun,
        *,
        emit_events: bool,
        force_event: bool = False,
    ) -> tuple[ResearchBacktestRead, bool, bool]:
        previous_status = record.status
        previous_dataset_version = record.dataset_version
        previous_metrics = dict(record.summary_metrics or {})
        previous_artifact_count = len(record.artifact_refs or [])

        run_payload = self._provider().get_backtest_run(record.remote_run_id)
        metrics_payload: dict[str, Any] | None = None
        artifacts_payload: dict[str, Any] | None = None
        if str(run_payload.get("status")) == "completed":
            metrics_payload = self._provider().get_backtest_metrics(record.remote_run_id)
            artifacts_payload = self._provider().get_backtest_artifacts(record.remote_run_id)

        self._apply_remote_snapshot(
            session,
            record,
            run_payload=run_payload,
            metrics_payload=metrics_payload,
            artifacts_payload=artifacts_payload,
        )
        changed = (
            previous_status != record.status
            or previous_dataset_version != record.dataset_version
            or previous_metrics != dict(record.summary_metrics or {})
            or previous_artifact_count != len(record.artifact_refs or [])
        )
        reached_terminal = previous_status not in TERMINAL_BACKTEST_STATUSES and record.status in TERMINAL_BACKTEST_STATUSES
        if emit_events and (force_event or changed or reached_terminal):
            self.event_log_service.record(
                session,
                event_type="external_backtest.reconciled",
                entity_type="external_backtest_run",
                entity_id=record.id,
                source="research_backtesting",
                pdca_phase_hint="check",
                payload={
                    "remote_run_id": record.remote_run_id,
                    "previous_status": previous_status,
                    "status": record.status,
                    "dataset_version": record.dataset_version,
                    "changed": changed,
                    "terminal": reached_terminal,
                },
            )
        return self.to_read_model(record), changed, reached_terminal

    def _build_remote_urls(self, remote_run_id: str) -> dict[str, str]:
        base_url = self.settings.backtesting_base_url.rstrip("/")
        return {
            "run": f"{base_url}/api/v1/backtests/{remote_run_id}",
            "metrics": f"{base_url}/api/v1/backtests/{remote_run_id}/metrics",
            "trades": f"{base_url}/api/v1/backtests/{remote_run_id}/trades",
            "equity": f"{base_url}/api/v1/backtests/{remote_run_id}/equity",
            "artifacts": f"{base_url}/api/v1/backtests/{remote_run_id}/artifacts",
        }


def _parse_remote_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
