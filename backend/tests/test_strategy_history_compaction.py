from datetime import datetime, timedelta, timezone

from app.db.models.position import Position
from app.db.models.strategy import StrategyVersion
from app.db.models.strategy_evolution import StrategyChangeEvent
from app.domains.strategy.repositories import StrategyRepository
from app.domains.strategy.schemas import StrategyCreate, StrategyVersionCreate
from app.domains.strategy.services import (
    StrategyEvolutionService,
    StrategyLabService,
    StrategyMaintenanceService,
    StrategyService,
)


def _create_strategy(session, code: str, *, hypothesis: str = "Base hypothesis for bounded variants.") -> object:
    return StrategyService().create_strategy(
        session,
        StrategyCreate(
            code=code,
            name=code.replace("_", " ").title(),
            market="US_EQUITIES",
            horizon="days_weeks",
            bias="long",
            status="paper",
            initial_version=StrategyVersionCreate(
                hypothesis=hypothesis,
                general_rules={"trend": "up"},
                parameters={},
                state="approved",
                is_baseline=True,
            ),
        ),
    )


def test_success_pattern_evolution_keeps_hypothesis_compact_across_generations(session) -> None:
    strategy = _create_strategy(session, "bounded_success_pattern_strategy")
    evolution_service = StrategyEvolutionService()

    first_result = evolution_service.evolve_from_success_pattern(
        session,
        strategy_id=strategy.id,
        source_version_id=strategy.current_version_id,
        success_summary={"trade_count": 3, "avg_pnl_pct": 7.5, "avg_drawdown_pct": -1.3},
    )
    first_version = session.get(StrategyVersion, first_result["new_version_id"])
    assert first_version is not None

    second_result = evolution_service.evolve_from_success_pattern(
        session,
        strategy_id=strategy.id,
        source_version_id=first_version.id,
        success_summary={"trade_count": 4, "avg_pnl_pct": 8.1, "avg_drawdown_pct": -1.1},
    )
    second_version = session.get(StrategyVersion, second_result["new_version_id"])
    assert second_version is not None

    assert first_version.parameters["base_hypothesis"] == "Base hypothesis for bounded variants."
    assert second_version.parameters["base_hypothesis"] == "Base hypothesis for bounded variants."
    assert first_version.parameters["evolution_lineage_depth"] == 1
    assert second_version.parameters["evolution_lineage_depth"] == 2
    assert first_version.hypothesis.count("Variant note [success_pattern]:") == 1
    assert second_version.hypothesis.count("Variant note [success_pattern]:") == 1
    assert len(second_version.hypothesis) - len(first_version.hypothesis) < 90


def test_strategy_lab_skips_success_variant_without_new_trades_even_after_other_events(session) -> None:
    strategy = _create_strategy(session, "success_pattern_guard_strategy")
    now = datetime.now(timezone.utc)
    for idx in range(2):
        session.add(
            Position(
                ticker=f"WIN{idx}",
                strategy_version_id=strategy.current_version_id,
                account_mode="paper",
                side="long",
                status="closed",
                entry_date=now - timedelta(minutes=20 - idx),
                entry_price=100.0 + idx,
                stop_price=95.0 + idx,
                target_price=110.0 + idx,
                size=10,
                thesis="Winning trade used to trigger success-pattern evolution.",
                exit_date=now - timedelta(minutes=5 - idx),
                exit_price=108.0 + idx,
                exit_reason="target",
                pnl_realized=80.0 + idx,
                pnl_unrealized=0.0,
                pnl_pct=5.0 + idx,
                max_drawdown_pct=-1.0,
                max_runup_pct=6.5,
                review_status="done",
                entry_context={"execution_mode": "default"},
                close_context={"source": "test"},
            )
        )
    session.commit()

    lab_service = StrategyLabService()
    first_batch = lab_service.evolve_from_success_patterns(session)
    assert first_batch["generated_variants"] == 1

    other_strategy = _create_strategy(session, "unrelated_strategy_for_event_noise")
    for idx in range(6):
        session.add(
            StrategyChangeEvent(
                strategy_id=other_strategy.id,
                source_version_id=other_strategy.current_version_id,
                new_version_id=None,
                trade_review_id=None,
                change_reason=f"Unrelated strategy event {idx}",
                proposed_change=None,
                change_summary={"trigger": "noise"},
                applied_automatically=True,
            )
        )
    session.commit()

    second_batch = lab_service.evolve_from_success_patterns(session)
    strategy_versions = StrategyRepository().get(session, strategy.id).versions

    assert second_batch["generated_variants"] == 0
    assert len(strategy_versions) == 2


def test_strategy_maintenance_compacts_old_historical_hypotheses(session) -> None:
    strategy = _create_strategy(session, "strategy_history_compaction_case", hypothesis="Compact base hypothesis.")
    repository = StrategyRepository()
    repeated_note = "Candidate refinement from successful trade pattern: 3 winning trades with avg pnl 97.72%."
    long_hypothesis = f"Compact base hypothesis.\n\n{repeated_note} " * 30

    old_version = repository.create_version(
        session,
        strategy.id,
        StrategyVersionCreate(
            hypothesis=long_hypothesis,
            general_rules={"mode": "historical"},
            parameters={
                "base_hypothesis": "Compact base hypothesis.",
                "evolution_trigger": "success_pattern",
                "evolution_note": repeated_note,
            },
            state="approved",
            lifecycle_stage="approved",
            is_baseline=False,
        ),
    )
    current_version = repository.create_version(
        session,
        strategy.id,
        StrategyVersionCreate(
            hypothesis="Compact base hypothesis.\n\nVariant note [success_pattern]: latest active note.",
            general_rules={"mode": "current"},
            parameters={
                "base_hypothesis": "Compact base hypothesis.",
                "evolution_trigger": "success_pattern",
                "evolution_note": "latest active note.",
            },
            state="approved",
            lifecycle_stage="active",
            is_baseline=False,
        ),
    )
    current_hypothesis_before = current_version.hypothesis

    maintenance_service = StrategyMaintenanceService()
    dry_run = maintenance_service.compact_historical_hypotheses(session, dry_run=True, keep_recent=1, max_chars=220)
    assert dry_run["compacted_versions"] == 1
    assert old_version.id in dry_run["compacted_version_ids"]

    applied = maintenance_service.compact_historical_hypotheses(session, dry_run=False, keep_recent=1, max_chars=220)
    refreshed_old = session.get(StrategyVersion, old_version.id)
    refreshed_current = session.get(StrategyVersion, current_version.id)

    assert applied["compacted_versions"] == 1
    assert refreshed_old is not None
    assert len(refreshed_old.hypothesis) <= 220
    assert refreshed_old.parameters["hypothesis_compaction_version"] == 1
    assert refreshed_current is not None
    assert refreshed_current.hypothesis == current_hypothesis_before
