from __future__ import annotations

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domains.market.schemas import ResearchTaskCreate, WorkQueueRead
from app.domains.execution.schemas import AutoExitBatchResult


class JournalEntryCreate(BaseModel):
    entry_type: str
    ticker: str | None = None
    strategy_id: int | None = None
    strategy_version_id: int | None = None
    position_id: int | None = None
    pdca_cycle_id: int | None = None
    market_context: dict = Field(default_factory=dict)
    hypothesis: str | None = None
    observations: dict = Field(default_factory=dict)
    reasoning: str | None = None
    decision: str | None = None
    expectations: str | None = None
    outcome: str | None = None
    lessons: str | None = None


class JournalEntryRead(BaseModel):
    id: int
    entry_type: str
    event_time: datetime
    ticker: str | None
    strategy_id: int | None
    strategy_version_id: int | None
    position_id: int | None
    pdca_cycle_id: int | None
    market_context: dict
    hypothesis: str | None
    observations: dict
    reasoning: str | None
    decision: str | None
    expectations: str | None
    outcome: str | None
    lessons: str | None

    model_config = {"from_attributes": True}


class TickerTraceSummaryRead(BaseModel):
    ticker: str
    total_signals: int = 0
    total_journal_entries: int = 0
    total_positions: int = 0
    open_positions: int = 0
    latest_signal_id: int | None = None
    latest_signal_at: datetime | None = None
    latest_signal_status: str | None = None
    latest_signal_type: str | None = None
    latest_decision: str | None = None
    latest_decision_source: str | None = None
    latest_guard_reason: str | None = None
    latest_llm_status: str | None = None
    latest_llm_provider: str | None = None
    latest_primary_skill: str | None = None
    latest_active_skill_revision: str | None = None
    latest_available_runtime_skill_count: int | None = None
    latest_loaded_runtime_skill_count: int | None = None
    latest_available_runtime_claim_count: int | None = None
    latest_loaded_runtime_claim_count: int | None = None
    latest_available_runtime_distillation_count: int | None = None
    latest_loaded_runtime_distillation_count: int | None = None
    latest_runtime_budget_truncated: bool = False
    latest_score: float | None = None
    latest_timing_total_ms: float | None = None
    latest_timing_slowest_stage: str | None = None
    latest_timing_slowest_stage_ms: float | None = None


class TickerTraceEventRead(BaseModel):
    timestamp: datetime
    event_kind: str
    title: str
    summary: str
    status: str | None = None
    decision: str | None = None
    decision_source: str | None = None
    llm_status: str | None = None
    llm_provider: str | None = None
    signal_id: int | None = None
    position_id: int | None = None
    journal_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class TickerTraceRead(BaseModel):
    ticker: str
    summary: TickerTraceSummaryRead
    events: list[TickerTraceEventRead] = Field(default_factory=list)


class MemoryItemCreate(BaseModel):
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict = Field(default_factory=dict)
    importance: float = 0.5


class MemoryItemRead(BaseModel):
    id: int
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict
    importance: float
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RuntimeMemoryInspectionSourceRead(BaseModel):
    source_type: str
    signal_id: int | None = None
    position_id: int | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    timestamp: datetime | None = None
    summary: str | None = None


class RuntimeMemoryInspectionRead(BaseModel):
    ticker: str | None = None
    strategy_version_id: int | None = None
    requested_skill_codes: list[str] = Field(default_factory=list)
    resolved_skill_context: dict = Field(default_factory=dict)
    skill_context_source: RuntimeMemoryInspectionSourceRead
    runtime_skills: list[dict] = Field(default_factory=list)
    runtime_claims: list[dict] = Field(default_factory=list)
    runtime_distillations: list[dict] = Field(default_factory=list)
    context_budget: dict = Field(default_factory=dict)


class MemoryDistillationRequest(BaseModel):
    dry_run: bool = True
    include_claim_reviews: bool = True
    include_operator_feedback: bool = True
    include_skill_gaps: bool = True
    include_skill_candidates: bool = True
    claim_limit: int = Field(default=200, ge=1, le=500)
    disagreement_limit: int = Field(default=200, ge=1, le=500)
    skill_gap_limit: int = Field(default=200, ge=1, le=500)
    skill_candidate_limit: int = Field(default=200, ge=1, le=500)
    min_group_size: int = Field(default=2, ge=2, le=20)


class MemoryDistillationDigestRead(BaseModel):
    distillation_type: str
    key: str
    scope: str
    content: str
    importance: float
    action: str
    memory_id: int | None = None
    source_count: int = 0
    source_ids: list[int] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


class MemoryDistillationSectionRead(BaseModel):
    distillation_type: str
    digest_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    digests: list[MemoryDistillationDigestRead] = Field(default_factory=list)


class MemoryDistillationResult(BaseModel):
    dry_run: bool
    created_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    sections: list[MemoryDistillationSectionRead] = Field(default_factory=list)


class MemoryDistillationReviewRequest(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=4000)
    keep_entity_id: int | None = None


class MemoryDistillationReviewResult(BaseModel):
    digest: MemoryItemRead
    effect: dict = Field(default_factory=dict)


class KnowledgeClaimCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=160)
    claim_type: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=4000)
    linked_ticker: str | None = Field(default=None, max_length=12)
    strategy_version_id: int | None = None
    status: str = Field(default="provisional", max_length=24)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    freshness_state: str = Field(default="current", max_length=20)
    meta: dict = Field(default_factory=dict)


class KnowledgeClaimRead(BaseModel):
    id: int
    claim_type: str
    scope: str
    key: str
    claim_text: str
    status: str
    confidence: float
    freshness_state: str
    linked_ticker: str | None
    strategy_version_id: int | None
    evidence_count: int
    support_count: int
    contradiction_count: int
    meta: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    last_reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}


class KnowledgeClaimEvidenceCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=40)
    source_key: str = Field(min_length=1, max_length=160)
    stance: str = Field(default="support", max_length=20)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_payload: dict = Field(default_factory=dict)
    strength: float = Field(default=0.6, ge=0.0, le=1.0)
    observed_at: datetime | None = None


class KnowledgeClaimEvidenceRead(BaseModel):
    id: int
    claim_id: int
    source_type: str
    source_key: str
    stance: str
    summary: str
    evidence_payload: dict = Field(default_factory=dict)
    strength: float
    observed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeClaimReviewQueueItemRead(BaseModel):
    claim_id: int
    review_reason: str
    review_priority: int
    claim_text: str
    status: str
    freshness_state: str
    confidence: float
    linked_ticker: str | None = None
    strategy_version_id: int | None = None
    support_count: int = 0
    contradiction_count: int = 0
    evidence_count: int = 0


class KnowledgeClaimReviewRequest(BaseModel):
    outcome: str = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=4000)
    source_key: str | None = Field(default=None, max_length=160)
    strength: float = Field(default=0.65, ge=0.0, le=1.0)
    evidence_payload: dict = Field(default_factory=dict)


class KnowledgeClaimReviewResult(BaseModel):
    claim: KnowledgeClaimRead
    evidence: KnowledgeClaimEvidenceRead | None = None
    promoted_skill_candidate: SkillCandidateRead | None = None


class SkillDefinitionRead(BaseModel):
    code: str
    name: str
    category: str
    phases: list[str] = Field(default_factory=list)
    objective: str
    description: str
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    priority: int
    dependencies: list[str] = Field(default_factory=list)
    incompatible_with: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SkillRevisionRead(BaseModel):
    id: int
    skill_code: str | None = None
    candidate_id: int | None = None
    validation_record_id: int | None = None
    activation_status: str
    validation_mode: str | None = None
    validation_outcome: str | None = None
    revision_summary: str
    source_trade_review_id: int | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    created_at: datetime
    meta: dict = Field(default_factory=dict)


class SkillPortableArtifactRead(BaseModel):
    artifact_version: str
    artifact_type: str
    skill_code: str | None = None
    target_skill_code: str | None = None
    exported_at: datetime | None = None
    document: dict = Field(default_factory=dict)
    skill_md: str
    yaml_text: str


class SkillPortableImportRequest(BaseModel):
    format: str = Field(min_length=1, max_length=24)
    content: str = Field(min_length=1, max_length=200000)
    import_as: str = Field(default="candidate", min_length=1, max_length=24)
    scope: str | None = Field(default=None, max_length=50)
    key: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4000)
    target_skill_code: str | None = Field(default=None, max_length=120)
    candidate_action: str | None = Field(default=None, max_length=40)
    ticker: str | None = Field(default=None, max_length=12)
    strategy_version_id: int | None = None
    candidate_id: int | None = Field(default=None, ge=1)


class SkillPortableImportResult(BaseModel):
    format: str
    import_as: str
    document: dict = Field(default_factory=dict)
    candidate: SkillCandidateRead | None = None
    revision: SkillRevisionRead | None = None
    journal_entry_id: int | None = None


class SkillCandidateRead(BaseModel):
    id: int
    scope: str
    key: str
    summary: str
    target_skill_code: str | None = None
    candidate_action: str | None = None
    candidate_status: str
    activation_status: str | None = None
    validation_required: bool = True
    source_type: str | None = None
    source_trade_review_id: int | None = None
    latest_validation_record_id: int | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    created_at: datetime
    importance: float
    meta: dict = Field(default_factory=dict)


class SkillProposalRead(BaseModel):
    id: int
    scope: str
    key: str
    summary: str
    proposal_type: str
    proposal_status: str = "pending"
    target_skill_code: str | None = None
    candidate_action: str | None = None
    source_type: str | None = None
    source_claim_id: int | None = None
    source_gap_id: int | None = None
    source_workflow_id: int | None = None
    source_workflow_run_id: int | None = None
    source_workflow_artifact_id: int | None = None
    source_operator_disagreement_cluster_id: int | None = None
    linked_skill_candidate_id: int | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    created_at: datetime
    importance: float
    meta: dict = Field(default_factory=dict)


class SkillProposalReviewRequest(BaseModel):
    outcome: str = Field(min_length=1, max_length=24)
    summary: str = Field(min_length=1, max_length=4000)


class SkillProposalReviewResult(BaseModel):
    proposal: SkillProposalRead
    candidate: SkillCandidateRead | None = None


class SkillCandidateValidationRequest(BaseModel):
    validation_mode: str
    validation_outcome: str
    summary: str | None = None
    sample_size: int | None = Field(default=None, ge=1)
    win_rate: float | None = None
    avg_pnl_pct: float | None = None
    max_drawdown_pct: float | None = None
    evidence: dict = Field(default_factory=dict)
    activate: bool = True


class SkillCandidateValidationResult(BaseModel):
    candidate: SkillCandidateRead
    revision: SkillRevisionRead | None = None
    validation_record: SkillValidationRecordRead | None = None
    journal_entry_id: int
    activation_status: str


class SkillProvenanceRead(BaseModel):
    origin_entity_type: str
    origin_entity_id: int
    claim: KnowledgeClaimRead | None = None
    candidate: SkillCandidateRead | None = None
    revision: SkillRevisionRead | None = None


class SkillValidationRecordRead(BaseModel):
    id: int
    candidate_id: int
    revision_id: int | None = None
    skill_code: str | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    validation_mode: str
    validation_outcome: str
    summary: str
    run_id: str | None = None
    artifact_url: str | None = None
    evidence_note: str | None = None
    sample_size: int | None = None
    win_rate: float | None = None
    avg_pnl_pct: float | None = None
    max_drawdown_pct: float | None = None
    created_at: datetime
    evidence_payload: dict = Field(default_factory=dict)


class SkillValidationMetricDeltaRead(BaseModel):
    current: float | None = None
    previous: float | None = None
    delta: float | None = None


class SkillValidationSummaryRead(BaseModel):
    scope_type: str
    scope_value: str
    record_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    latest_validation_id: int | None = None
    previous_validation_id: int | None = None
    latest_run_id: str | None = None
    avg_win_rate: float | None = None
    avg_avg_pnl_pct: float | None = None
    avg_max_drawdown_pct: float | None = None
    best_win_rate: float | None = None
    best_avg_pnl_pct: float | None = None
    worst_max_drawdown_pct: float | None = None
    win_rate_delta: SkillValidationMetricDeltaRead = Field(default_factory=SkillValidationMetricDeltaRead)
    avg_pnl_pct_delta: SkillValidationMetricDeltaRead = Field(default_factory=SkillValidationMetricDeltaRead)
    max_drawdown_pct_delta: SkillValidationMetricDeltaRead = Field(default_factory=SkillValidationMetricDeltaRead)


class SkillValidationCompareRowRead(BaseModel):
    validation_id: int
    created_at: datetime
    validation_mode: str
    validation_outcome: str
    run_id: str | None = None
    sample_size: int | None = None
    win_rate: float | None = None
    avg_pnl_pct: float | None = None
    max_drawdown_pct: float | None = None
    win_rate_delta_vs_base: float | None = None
    avg_pnl_pct_delta_vs_base: float | None = None
    max_drawdown_pct_delta_vs_base: float | None = None
    is_base: bool = False


class SkillValidationCompareRead(BaseModel):
    scope_type: str
    scope_value: str
    baseline_validation_id: int | None = None
    baseline_run_id: str | None = None
    custom_baseline_applied: bool = False
    row_count: int = 0
    rows: list[SkillValidationCompareRowRead] = Field(default_factory=list)


class SkillGapRead(BaseModel):
    id: int
    scope: str
    key: str
    summary: str
    gap_type: str
    status: str = "open"
    ticker: str | None = None
    strategy_version_id: int | None = None
    position_id: int | None = None
    source_type: str | None = None
    source_trade_review_id: int | None = None
    linked_skill_code: str | None = None
    target_skill_code: str | None = None
    candidate_action: str | None = None
    created_at: datetime
    importance: float
    meta: dict = Field(default_factory=dict)


class SkillGapReviewRequest(BaseModel):
    outcome: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=4000)


class OperatorDisagreementClusterPromoteGapResult(BaseModel):
    cluster: OperatorDisagreementClusterRead
    gap: SkillGapRead


class SkillDashboardRead(BaseModel):
    catalog: list[SkillDefinitionRead] = Field(default_factory=list)
    proposals: list[SkillProposalRead] = Field(default_factory=list)
    candidates: list[SkillCandidateRead] = Field(default_factory=list)
    active_revisions: list[SkillRevisionRead] = Field(default_factory=list)
    gaps: list[SkillGapRead] = Field(default_factory=list)
    distillations: list[MemoryItemRead] = Field(default_factory=list)


class LearningWorkflowItemRead(BaseModel):
    item_type: str
    entity_id: int | None = None
    title: str
    status: str
    priority: str | None = None
    action_hint: str | None = None
    payload: dict = Field(default_factory=dict)


class LearningWorkflowHistoryEntryRead(BaseModel):
    timestamp: datetime
    event_type: str
    summary: str
    change_class: str | None = None
    resolution_class: str | None = None
    resolution_outcome: str | None = None
    item_type: str | None = None
    entity_id: int | None = None
    action: str | None = None
    status_before: str | None = None
    status_after: str | None = None
    open_item_count_after: int | None = None
    added_items: list[str] = Field(default_factory=list)
    removed_items: list[str] = Field(default_factory=list)
    effect: dict = Field(default_factory=dict)


class LearningWorkflowArtifactRead(BaseModel):
    id: int
    workflow_run_id: int
    artifact_type: str
    entity_type: str | None = None
    entity_id: int | None = None
    title: str | None = None
    summary: str | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class LearningWorkflowRunRead(BaseModel):
    id: int
    workflow_id: int
    run_kind: str
    trigger_source: str | None = None
    status: str
    summary: str | None = None
    input_payload: dict = Field(default_factory=dict)
    context_payload: dict = Field(default_factory=dict)
    output_payload: dict = Field(default_factory=dict)
    artifact_count: int = 0
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    artifacts: list[LearningWorkflowArtifactRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class LearningWorkflowRead(BaseModel):
    id: int
    workflow_type: str
    scope: str
    title: str
    status: str
    priority: str
    summary: str | None = None
    context: dict = Field(default_factory=dict)
    items: list[LearningWorkflowItemRead] = Field(default_factory=list)
    item_count: int = 0
    open_item_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None = None
    resolved_at: datetime | None = None
    history: list[LearningWorkflowHistoryEntryRead] = Field(default_factory=list)
    recent_runs: list[LearningWorkflowRunRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class LearningWorkflowSkillGapCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=50)
    key: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    gap_type: str = Field(min_length=1, max_length=80)
    ticker: str | None = Field(default=None, max_length=12)
    strategy_version_id: int | None = None
    position_id: int | None = None
    status: str = Field(default="open", max_length=24)
    linked_skill_code: str | None = Field(default=None, max_length=120)
    target_skill_code: str | None = Field(default=None, max_length=120)
    candidate_action: str | None = Field(default=None, max_length=40)
    source_type: str | None = Field(default=None, max_length=40)
    importance: float = Field(default=0.72, ge=0.0, le=1.0)
    evidence: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


class LearningWorkflowSkillCandidateCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=50)
    key: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    target_skill_code: str | None = Field(default=None, max_length=120)
    candidate_action: str | None = Field(default=None, max_length=40)
    candidate_status: str = Field(default="draft", max_length=24)
    activation_status: str | None = Field(default=None, max_length=24)
    validation_required: bool = True
    source_type: str | None = Field(default=None, max_length=40)
    source_trade_review_id: int | None = None
    ticker: str | None = Field(default=None, max_length=12)
    strategy_version_id: int | None = None
    importance: float = Field(default=0.72, ge=0.0, le=1.0)
    meta: dict = Field(default_factory=dict)


class LearningWorkflowActionRequest(BaseModel):
    item_type: str = Field(min_length=1, max_length=40)
    entity_id: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=4000)
    claims: list[KnowledgeClaimCreate] = Field(default_factory=list)
    research_tasks: list[ResearchTaskCreate] = Field(default_factory=list)
    skill_gaps: list[LearningWorkflowSkillGapCreate] = Field(default_factory=list)
    skill_candidates: list[LearningWorkflowSkillCandidateCreate] = Field(default_factory=list)


class LearningWorkflowActionResult(BaseModel):
    workflow: LearningWorkflowRead
    effect: dict = Field(default_factory=dict)


class OperatorDisagreementRead(BaseModel):
    id: int
    disagreement_type: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    action: str | None = None
    summary: str
    ticker: str | None = None
    strategy_version_id: int | None = None
    position_id: int | None = None
    source: str | None = None
    journal_entry_id: int | None = None
    importance: float
    created_at: datetime
    details: dict = Field(default_factory=dict)


class OperatorDisagreementBucketRead(BaseModel):
    label: str
    count: int = 0
    last_seen_at: datetime | None = None


class OperatorDisagreementSummaryRead(BaseModel):
    total_events: int = 0
    by_disagreement_type: list[OperatorDisagreementBucketRead] = Field(default_factory=list)
    by_entity_type: list[OperatorDisagreementBucketRead] = Field(default_factory=list)
    by_ticker: list[OperatorDisagreementBucketRead] = Field(default_factory=list)
    by_target_skill_code: list[OperatorDisagreementBucketRead] = Field(default_factory=list)


class OperatorDisagreementClusterRead(BaseModel):
    id: int
    cluster_key: str
    status: str = "open"
    disagreement_type: str | None = None
    entity_type: str | None = None
    ticker: str | None = None
    strategy_version_id: int | None = None
    target_skill_code: str | None = None
    claim_key: str | None = None
    event_count: int = 0
    last_seen_at: datetime | None = None
    sample_summaries: list[str] = Field(default_factory=list)
    source_memory_ids: list[int] = Field(default_factory=list)
    source_journal_ids: list[int] = Field(default_factory=list)
    promoted_claim_id: int | None = None
    promoted_skill_gap_id: int | None = None
    importance: float
    created_at: datetime
    meta: dict = Field(default_factory=dict)


class OperatorDisagreementClusterPromoteResult(BaseModel):
    cluster: OperatorDisagreementClusterRead
    claim: KnowledgeClaimRead


class FailurePatternRead(BaseModel):
    id: int
    strategy_id: int
    strategy_version_id: int | None
    failure_mode: str
    pattern_signature: str
    occurrences: int
    avg_loss_pct: float | None
    evidence: dict
    recommended_action: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AutoReviewResult(BaseModel):
    position_id: int
    generated: bool
    review_id: int | None = None
    reason: str


class AutoReviewBatchResult(BaseModel):
    generated_reviews: int
    skipped_positions: int
    results: list[AutoReviewResult]


class PDCACycleCreate(BaseModel):
    cycle_date: date
    phase: str
    status: str = "pending"
    summary: str | None = None
    context: dict = Field(default_factory=dict)


class PDCACycleRead(BaseModel):
    id: int
    cycle_date: date
    phase: str
    status: str
    summary: str | None
    context: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class DailyPlanRequest(BaseModel):
    cycle_date: date
    market_context: dict = Field(default_factory=dict)


class OrchestratorPlanResponse(BaseModel):
    cycle_id: int
    phase: str
    status: str
    summary: str
    market_context: dict
    market_state_snapshot: MarketStateSnapshotRead | None = None
    work_queue: WorkQueueRead | None = None


class OrchestratorPhaseResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    market_state_snapshot: MarketStateSnapshotRead | None = None


class ExecutionCandidateResult(BaseModel):
    ticker: str
    watchlist_item_id: int
    analysis_run_id: int
    signal_id: int | None = None
    trade_signal_id: int | None = None
    decision: str
    score: float
    position_id: int | None = None


class OrchestratorDoResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    generated_analyses: int
    opened_positions: int
    candidates: list[ExecutionCandidateResult]
    market_state_snapshot: MarketStateSnapshotRead | None = None
    exits: AutoExitBatchResult | None = None
    discovery: dict | None = None


class OrchestratorActResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    generated_variants: int
    market_state_snapshot: MarketStateSnapshotRead | None = None


class BotChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class BotChatResponse(BaseModel):
    topic: str
    reply: str
    suggested_prompts: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


class ChatLLMPresetRead(BaseModel):
    key: str
    label: str
    provider: str
    model: str | None = None
    reasoning_effort: str | None = None
    ready: bool = False
    availability_error: str | None = None


class ChatConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    topic: str | None = Field(default=None, max_length=40)
    labels: list[str] = Field(default_factory=list)
    linked_ticker: str | None = Field(default=None, max_length=12)
    linked_hypothesis_id: int | None = None
    linked_strategy_id: int | None = None
    preferred_llm: str | None = Field(default=None, max_length=40)


class ChatConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    labels: list[str] | None = None
    status: str | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=4000)
    preferred_llm: str | None = Field(default=None, max_length=40)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    message_type: str = Field(default="chat", max_length=40)
    llm_preset: str | None = Field(default=None, max_length=40)


class ChatMessageRead(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    message_type: str
    context: dict = Field(default_factory=dict)
    actions_taken: list[dict] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatConversationRead(BaseModel):
    id: int
    title: str
    topic: str
    status: str
    summary: str | None = None
    labels: list[str] = Field(default_factory=list)
    linked_ticker: str | None = None
    linked_hypothesis_id: int | None = None
    linked_strategy_id: int | None = None
    preferred_llm: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    model_config = {"from_attributes": True}


class ChatConversationDetailRead(ChatConversationRead):
    messages: list[ChatMessageRead] = Field(default_factory=list)


class ChatConversationTurnResponse(BaseModel):
    conversation: ChatConversationRead
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class MacroSignalCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    regime: str = Field(default="neutral", min_length=1, max_length=40)
    relevance: str = Field(default="general", min_length=1, max_length=60)
    tickers: list[str] = Field(default_factory=list)
    timeframe: str | None = Field(default=None, max_length=60)
    scenario: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=120)
    evidence: dict = Field(default_factory=dict)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class MacroSignalRead(BaseModel):
    id: int
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict
    importance: float
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MacroIndicatorRead(BaseModel):
    key: str
    label: str
    value: float | None = None
    unit: str | None = None
    previous_value: float | None = None
    change: float | None = None
    change_pct: float | None = None
    as_of: str | None = None
    source: str
    status: str = "available"
    interpretation: str | None = None
    detail: str | None = None


class MacroContextRead(BaseModel):
    summary: str
    active_regimes: list[str] = Field(default_factory=list)
    relevance_tags: list[str] = Field(default_factory=list)
    tracked_tickers: list[str] = Field(default_factory=list)
    signals: list[dict] = Field(default_factory=list)
    indicators: list[MacroIndicatorRead] = Field(default_factory=list)


class MarketStateSnapshotRead(BaseModel):
    id: int
    trigger: str
    pdca_phase: str | None
    execution_mode: str
    benchmark_ticker: str
    regime_label: str
    regime_confidence: float | None
    summary: str
    snapshot_payload: dict
    source_context: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentToolDefinitionRead(BaseModel):
    name: str
    category: str
    description: str
    input_schema: dict = Field(default_factory=dict)


class AgentToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)


class AgentToolCallResponse(BaseModel):
    tool_name: str
    result: dict = Field(default_factory=dict)
