from app.domains.learning.repositories import JournalRepository, MemoryRepository
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import JournalService, LearningHistoryMaintenanceService, MemoryService


def test_journal_service_prunes_noisy_entry_types_automatically(session, monkeypatch) -> None:
    monkeypatch.setattr(JournalService, "RETENTION_LIMITS", {"pdca_do": 3})
    service = JournalService()

    for idx in range(5):
        service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_do",
                reasoning=f"cycle {idx}",
                observations={"idx": idx},
                decision="observe",
            ),
        )

    entries = service.list_entries(session)
    assert len(entries) == 3
    assert [entry.observations["idx"] for entry in reversed(entries)] == [2, 3, 4]


def test_memory_service_prunes_exact_and_prefix_scopes_automatically(session, monkeypatch) -> None:
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_EXACT", {("episodic", "pdca_check"): 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_PREFIX", {("strategy_evolution", "strategy:"): 2})
    service = MemoryService()

    for idx in range(4):
        service.create_item(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_check",
                key=f"episodic:{idx}",
                content=f"episodic {idx}",
                importance=0.6,
            ),
        )
    for idx in range(4):
        service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope="strategy:1",
                key=f"evolution:{idx}",
                content=f"evolution {idx}",
                importance=0.8,
            ),
        )
    service.create_item(
        session,
        MemoryItemCreate(
            memory_type="lesson",
            scope="global",
            key="lesson:1",
            content="keep me",
            importance=0.95,
        ),
    )

    items = service.list_items(session)
    exact_items = [item for item in items if item.memory_type == "episodic" and item.scope == "pdca_check"]
    scoped_items = [item for item in items if item.memory_type == "strategy_evolution" and item.scope == "strategy:1"]
    lessons = [item for item in items if item.memory_type == "lesson"]

    assert len(exact_items) == 2
    assert {item.key for item in exact_items} == {"episodic:2", "episodic:3"}
    assert len(scoped_items) == 2
    assert {item.key for item in scoped_items} == {"evolution:2", "evolution:3"}
    assert len(lessons) == 1


def test_learning_history_maintenance_reports_and_applies_bulk_pruning(session, monkeypatch) -> None:
    monkeypatch.setattr(JournalService, "RETENTION_LIMITS", {"pdca_act": 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_EXACT", {("episodic", "pdca_act"): 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_PREFIX", {("strategy_evolution", "strategy:"): 1})

    journal_repository = JournalRepository()
    memory_repository = MemoryRepository()
    for idx in range(4):
        journal_repository.create(
            session,
            JournalEntryCreate(
                entry_type="pdca_act",
                reasoning=f"act {idx}",
                observations={"idx": idx},
                decision="act",
            ),
        )
    for idx in range(4):
        memory_repository.create(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_act",
                key=f"act:{idx}",
                content=f"episodic act {idx}",
                importance=0.5,
            ),
        )
    for idx in range(3):
        memory_repository.create(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope="strategy:2",
                key=f"strategy:{idx}",
                content=f"evolution {idx}",
                importance=0.8,
            ),
        )

    maintenance = LearningHistoryMaintenanceService()
    dry_run = maintenance.trim_history(session, dry_run=True)
    assert dry_run["journal"]["deleted_count"] == 2
    assert dry_run["memory"]["deleted_count"] == 4

    applied = maintenance.trim_history(session, dry_run=False)
    assert applied["deleted_total"] == 6
    assert len(JournalService().list_entries(session)) == 2

    items = MemoryService().list_items(session)
    assert len([item for item in items if item.memory_type == "episodic" and item.scope == "pdca_act"]) == 2
    assert len([item for item in items if item.memory_type == "strategy_evolution" and item.scope == "strategy:2"]) == 1
