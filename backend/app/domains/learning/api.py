from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.learning.schemas import (
    AgentToolCallRequest,
    AgentToolCallResponse,
    AgentToolDefinitionRead,
    AutoReviewBatchResult,
    BotChatRequest,
    BotChatResponse,
    ChatConversationCreate,
    ChatConversationDetailRead,
    ChatConversationRead,
    ChatConversationTurnResponse,
    ChatConversationUpdate,
    ChatLLMPresetRead,
    ChatMessageCreate,
    DailyPlanRequest,
    FailurePatternRead,
    JournalEntryCreate,
    JournalEntryRead,
    LearningWorkflowActionRequest,
    LearningWorkflowActionResult,
    LearningWorkflowRead,
    KnowledgeClaimCreate,
    KnowledgeClaimEvidenceCreate,
    KnowledgeClaimEvidenceRead,
    KnowledgeClaimRead,
    KnowledgeClaimReviewQueueItemRead,
    KnowledgeClaimReviewRequest,
    KnowledgeClaimReviewResult,
    MarketStateSnapshotRead,
    MacroContextRead,
    MacroSignalCreate,
    MacroSignalRead,
    MemoryItemCreate,
    MemoryItemRead,
    OrchestratorActResponse,
    OrchestratorDoResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    OperatorDisagreementRead,
    OperatorDisagreementClusterPromoteGapResult,
    OperatorDisagreementClusterPromoteResult,
    OperatorDisagreementClusterRead,
    OperatorDisagreementSummaryRead,
    PDCACycleCreate,
    PDCACycleRead,
    SkillCandidateRead,
    SkillCandidateValidationRequest,
    SkillCandidateValidationResult,
    SkillDefinitionRead,
    SkillDashboardRead,
    SkillGapRead,
    SkillGapReviewRequest,
    SkillProvenanceRead,
    SkillRevisionRead,
    SkillValidationCompareRead,
    SkillValidationRecordRead,
    SkillValidationSummaryRead,
    TickerTraceRead,
)
from app.domains.learning.claims import ClaimEvidenceSeed, ClaimSeed, KnowledgeClaimService
from app.domains.learning.conversations import ChatConversationService
from app.domains.learning.operator_feedback import OperatorDisagreementService
from app.domains.learning.skills import SkillCatalogService, SkillGapService, SkillLifecycleService
from app.domains.learning.workflows import LearningWorkflowService
from app.domains.learning.services import (
    AutoReviewService,
    BotChatService,
    FailureAnalysisService,
    JournalService,
    MemoryService,
    OrchestratorService,
    PDCACycleService,
    TickerDecisionTraceService,
)
from app.domains.learning.macro import MacroContextService
from app.domains.learning.world_state import MarketStateService
from app.domains.learning.tools import AgentToolError, AgentToolGatewayService

journal_router = APIRouter()
memory_router = APIRouter()
macro_router = APIRouter()
failure_patterns_router = APIRouter()
auto_reviews_router = APIRouter()
pdca_router = APIRouter()
orchestrator_router = APIRouter()
chat_router = APIRouter()
tools_router = APIRouter()
skills_router = APIRouter()
claims_router = APIRouter()
workflows_router = APIRouter()
feedback_router = APIRouter()

journal_service = JournalService()
ticker_trace_service = TickerDecisionTraceService()
memory_service = MemoryService()
failure_analysis_service = FailureAnalysisService()
auto_review_service = AutoReviewService()
pdca_service = PDCACycleService()
orchestrator_service = OrchestratorService()
bot_chat_service = BotChatService()
chat_conversation_service = ChatConversationService()
macro_context_service = MacroContextService()
market_state_service = MarketStateService()
agent_tool_gateway_service = AgentToolGatewayService()
skill_catalog_service = SkillCatalogService()
skill_lifecycle_service = SkillLifecycleService(catalog_service=skill_catalog_service)
knowledge_claim_service = KnowledgeClaimService()
learning_workflow_service = LearningWorkflowService()
operator_disagreement_service = OperatorDisagreementService()


def _build_skill_provenance(
    session: Session,
    *,
    origin_entity_type: str,
    origin_entity_id: int,
    claim_id: int | None = None,
    candidate_id: int | None = None,
    revision_id: int | None = None,
) -> SkillProvenanceRead:
    claim = knowledge_claim_service.get_claim(session, claim_id) if claim_id is not None else None
    candidate = skill_lifecycle_service.get_candidate(session, candidate_id=candidate_id) if candidate_id is not None else None
    revision = skill_lifecycle_service.get_revision(session, revision_id=revision_id) if revision_id is not None else None

    if revision is not None and candidate is None:
        candidate_ref = revision.get("candidate_id")
        if isinstance(candidate_ref, int):
            candidate = skill_lifecycle_service.get_candidate(session, candidate_id=candidate_ref)

    if candidate is not None and claim is None:
        source_claim_id = as_int(as_dict(candidate.get("meta")).get("source_claim_id"))
        if source_claim_id is not None:
            claim = knowledge_claim_service.get_claim(session, source_claim_id)

    if candidate is not None and revision is None:
        active_revision_id = as_int(as_dict(candidate.get("meta")).get("active_revision_id"))
        if active_revision_id is not None:
            revision = skill_lifecycle_service.get_revision(session, revision_id=active_revision_id)

    if claim is not None and candidate is None:
        linked_candidate_id = as_int(as_dict(claim.meta).get("linked_skill_candidate_id"))
        if linked_candidate_id is not None:
            candidate = skill_lifecycle_service.get_candidate(session, candidate_id=linked_candidate_id)
        else:
            candidate = skill_lifecycle_service.find_candidate_by_source_claim_id(session, claim_id=claim.id)
        if candidate is not None and revision is None:
            active_revision_id = as_int(as_dict(candidate.get("meta")).get("active_revision_id"))
            if active_revision_id is not None:
                revision = skill_lifecycle_service.get_revision(session, revision_id=active_revision_id)

    return SkillProvenanceRead(
        origin_entity_type=origin_entity_type,
        origin_entity_id=origin_entity_id,
        claim=KnowledgeClaimRead.model_validate(claim) if claim is not None else None,
        candidate=SkillCandidateRead.model_validate(candidate) if candidate is not None else None,
        revision=SkillRevisionRead.model_validate(revision) if revision is not None else None,
    )


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


@journal_router.get("", response_model=list[JournalEntryRead])
async def list_journal_entries(session: Session = Depends(get_db_session)) -> list[JournalEntryRead]:
    return journal_service.list_entries(session)


@journal_router.get("/ticker-trace/{ticker}", response_model=TickerTraceRead)
async def get_ticker_trace(
    ticker: str,
    limit: int = Query(default=24, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> TickerTraceRead:
    return ticker_trace_service.get_trace(session, ticker, limit=limit)


@journal_router.post("", response_model=JournalEntryRead, status_code=status.HTTP_201_CREATED)
async def create_journal_entry(payload: JournalEntryCreate, session: Session = Depends(get_db_session)) -> JournalEntryRead:
    return journal_service.create_entry(session, payload)


@memory_router.get("", response_model=list[MemoryItemRead])
async def list_memory_items(session: Session = Depends(get_db_session)) -> list[MemoryItemRead]:
    return memory_service.list_items(session)


@memory_router.get("/context", response_model=list[MemoryItemRead])
async def retrieve_memory_context(
    scope: str,
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> list[MemoryItemRead]:
    return memory_service.retrieve_scope(session, scope=scope, limit=limit)


@memory_router.post("", response_model=MemoryItemRead, status_code=status.HTTP_201_CREATED)
async def create_memory_item(payload: MemoryItemCreate, session: Session = Depends(get_db_session)) -> MemoryItemRead:
    return memory_service.create_item(session, payload)


@feedback_router.get("", response_model=list[OperatorDisagreementRead], status_code=status.HTTP_200_OK)
async def list_operator_disagreements(
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> list[OperatorDisagreementRead]:
    return [
        OperatorDisagreementRead.model_validate(item)
        for item in operator_disagreement_service.list_items(session, limit=limit)
    ]


@feedback_router.get("/summary", response_model=OperatorDisagreementSummaryRead, status_code=status.HTTP_200_OK)
async def get_operator_disagreement_summary(
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> OperatorDisagreementSummaryRead:
    return OperatorDisagreementSummaryRead.model_validate(operator_disagreement_service.summarize(session, limit=limit))


@feedback_router.get("/clusters", response_model=list[OperatorDisagreementClusterRead], status_code=status.HTTP_200_OK)
async def list_operator_disagreement_clusters(
    sync: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    min_count: int = Query(default=2, ge=1, le=20),
    session: Session = Depends(get_db_session),
) -> list[OperatorDisagreementClusterRead]:
    items = (
        operator_disagreement_service.sync_clusters(session, limit=limit, min_count=min_count)
        if sync
        else operator_disagreement_service.list_clusters(session, limit=limit)
    )
    return [OperatorDisagreementClusterRead.model_validate(item) for item in items]


@feedback_router.post(
    "/clusters/{cluster_id}/promote",
    response_model=OperatorDisagreementClusterPromoteResult,
    status_code=status.HTTP_200_OK,
)
async def promote_operator_disagreement_cluster(
    cluster_id: int,
    session: Session = Depends(get_db_session),
) -> OperatorDisagreementClusterPromoteResult:
    try:
        result = operator_disagreement_service.promote_cluster_to_claim(session, cluster_id=cluster_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return OperatorDisagreementClusterPromoteResult(
        cluster=OperatorDisagreementClusterRead.model_validate(result["cluster"]),
        claim=KnowledgeClaimRead.model_validate(result["claim"]),
    )


@feedback_router.post(
    "/clusters/{cluster_id}/promote-gap",
    response_model=OperatorDisagreementClusterPromoteGapResult,
    status_code=status.HTTP_200_OK,
)
async def promote_operator_disagreement_cluster_to_gap(
    cluster_id: int,
    session: Session = Depends(get_db_session),
) -> OperatorDisagreementClusterPromoteGapResult:
    try:
        result = operator_disagreement_service.promote_cluster_to_skill_gap(session, cluster_id=cluster_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return OperatorDisagreementClusterPromoteGapResult(
        cluster=OperatorDisagreementClusterRead.model_validate(result["cluster"]),
        gap=SkillGapRead.model_validate(result["gap"]),
    )


@claims_router.get("", response_model=list[KnowledgeClaimRead], status_code=status.HTTP_200_OK)
async def list_knowledge_claims(
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> list[KnowledgeClaimRead]:
    return [
        KnowledgeClaimRead.model_validate(item)
        for item in knowledge_claim_service.list_claims(session, scope=scope, status=status, limit=limit)
    ]


@claims_router.post("", response_model=KnowledgeClaimRead, status_code=status.HTTP_201_CREATED)
async def create_knowledge_claim(
    payload: KnowledgeClaimCreate,
    session: Session = Depends(get_db_session),
) -> KnowledgeClaimRead:
    claim = knowledge_claim_service.create_claim(
        session,
        ClaimSeed(
            scope=payload.scope,
            key=payload.key,
            claim_type=payload.claim_type,
            claim_text=payload.claim_text,
            linked_ticker=payload.linked_ticker,
            strategy_version_id=payload.strategy_version_id,
            status=payload.status,
            confidence=payload.confidence,
            freshness_state=payload.freshness_state,
            meta=payload.meta,
        ),
    )
    return KnowledgeClaimRead.model_validate(claim)


@claims_router.get("/review-queue", response_model=list[KnowledgeClaimReviewQueueItemRead], status_code=status.HTTP_200_OK)
async def list_knowledge_claim_review_queue(
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[KnowledgeClaimReviewQueueItemRead]:
    return [
        KnowledgeClaimReviewQueueItemRead.model_validate(item)
        for item in knowledge_claim_service.list_review_queue(session, limit=limit)
    ]


@claims_router.get("/{claim_id}", response_model=KnowledgeClaimRead, status_code=status.HTTP_200_OK)
async def get_knowledge_claim(
    claim_id: int,
    session: Session = Depends(get_db_session),
) -> KnowledgeClaimRead:
    claim = knowledge_claim_service.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge claim not found.")
    return KnowledgeClaimRead.model_validate(claim)


@claims_router.get("/{claim_id}/evidence", response_model=list[KnowledgeClaimEvidenceRead], status_code=status.HTTP_200_OK)
async def list_knowledge_claim_evidence(
    claim_id: int,
    session: Session = Depends(get_db_session),
) -> list[KnowledgeClaimEvidenceRead]:
    claim = knowledge_claim_service.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge claim not found.")
    return [
        KnowledgeClaimEvidenceRead.model_validate(item)
        for item in knowledge_claim_service.list_evidence(session, claim_id=claim_id)
    ]


@claims_router.get("/{claim_id}/provenance", response_model=SkillProvenanceRead, status_code=status.HTTP_200_OK)
async def get_knowledge_claim_provenance(
    claim_id: int,
    session: Session = Depends(get_db_session),
) -> SkillProvenanceRead:
    claim = knowledge_claim_service.get_claim(session, claim_id)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge claim not found.")
    return _build_skill_provenance(
        session,
        origin_entity_type="claim",
        origin_entity_id=claim_id,
        claim_id=claim_id,
    )


@claims_router.post("/{claim_id}/evidence", response_model=KnowledgeClaimEvidenceRead, status_code=status.HTTP_200_OK)
async def add_knowledge_claim_evidence(
    claim_id: int,
    payload: KnowledgeClaimEvidenceCreate,
    session: Session = Depends(get_db_session),
) -> KnowledgeClaimEvidenceRead:
    try:
        evidence = knowledge_claim_service.add_evidence(
            session,
            claim_id=claim_id,
            seed=ClaimEvidenceSeed(
                source_type=payload.source_type,
                source_key=payload.source_key,
                stance=payload.stance,
                summary=payload.summary,
                evidence_payload=payload.evidence_payload,
                strength=payload.strength,
                observed_at=payload.observed_at,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return KnowledgeClaimEvidenceRead.model_validate(evidence)


@claims_router.post("/{claim_id}/review", response_model=KnowledgeClaimReviewResult, status_code=status.HTTP_200_OK)
async def review_knowledge_claim(
    claim_id: int,
    payload: KnowledgeClaimReviewRequest,
    session: Session = Depends(get_db_session),
) -> KnowledgeClaimReviewResult:
    try:
        claim, evidence, promoted_skill_candidate = knowledge_claim_service.review_claim(
            session,
            claim_id=claim_id,
            outcome=payload.outcome,
            summary=payload.summary,
            source_key=payload.source_key,
            strength=payload.strength,
            evidence_payload=payload.evidence_payload,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return KnowledgeClaimReviewResult(
        claim=KnowledgeClaimRead.model_validate(claim),
        evidence=KnowledgeClaimEvidenceRead.model_validate(evidence) if evidence is not None else None,
        promoted_skill_candidate=(
            SkillCandidateRead.model_validate(promoted_skill_candidate)
            if promoted_skill_candidate is not None
            else None
        ),
    )


@claims_router.post("/{claim_id}/promote", response_model=SkillCandidateRead, status_code=status.HTTP_200_OK)
async def promote_knowledge_claim_to_skill_candidate(
    claim_id: int,
    session: Session = Depends(get_db_session),
) -> SkillCandidateRead:
    try:
        candidate = knowledge_claim_service.maybe_promote_claim_to_skill_candidate(
            session,
            claim_id=claim_id,
            force=True,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Claim could not be promoted to a skill candidate under the current bridge policy.",
        )
    return SkillCandidateRead.model_validate(candidate)


@workflows_router.get("", response_model=list[LearningWorkflowRead], status_code=status.HTTP_200_OK)
async def list_learning_workflows(
    sync: bool = Query(default=False),
    include_resolved: bool = Query(default=True),
    limit: int = Query(default=10, ge=1, le=50),
    history_limit: int = Query(default=4, ge=1, le=20),
    session: Session = Depends(get_db_session),
) -> list[LearningWorkflowRead]:
    return [
        learning_workflow_service.to_read_model(item, history_limit=history_limit)
        for item in learning_workflow_service.list_workflows(
            session,
            sync=sync,
            include_resolved=include_resolved,
            limit=limit,
        )
    ]


@workflows_router.get("/{workflow_id}", response_model=LearningWorkflowRead, status_code=status.HTTP_200_OK)
async def get_learning_workflow(
    workflow_id: int,
    history_limit: int = Query(default=12, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> LearningWorkflowRead:
    workflow = learning_workflow_service.get_workflow(session, workflow_id=workflow_id)
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning workflow not found.")
    return learning_workflow_service.to_read_model(workflow, history_limit=history_limit)


@workflows_router.post("/sync", response_model=list[LearningWorkflowRead], status_code=status.HTTP_200_OK)
async def sync_learning_workflows(session: Session = Depends(get_db_session)) -> list[LearningWorkflowRead]:
    return [
        learning_workflow_service.to_read_model(item, history_limit=6)
        for item in learning_workflow_service.sync_default_workflows(session)
    ]


@workflows_router.post("/{workflow_id}/actions", response_model=LearningWorkflowActionResult, status_code=status.HTTP_200_OK)
async def apply_learning_workflow_action(
    workflow_id: int,
    payload: LearningWorkflowActionRequest,
    session: Session = Depends(get_db_session),
) -> LearningWorkflowActionResult:
    try:
        workflow, effect = learning_workflow_service.apply_action(
            session,
            workflow_id=workflow_id,
            item_type=payload.item_type,
            entity_id=payload.entity_id,
            action=payload.action,
            summary=payload.summary,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return LearningWorkflowActionResult(
        workflow=learning_workflow_service.to_read_model(workflow, history_limit=12),
        effect=effect,
    )


@skills_router.get("/catalog", response_model=list[SkillDefinitionRead], status_code=status.HTTP_200_OK)
async def list_skill_catalog(session: Session = Depends(get_db_session)) -> list[SkillDefinitionRead]:
    return [SkillDefinitionRead.model_validate(item) for item in skill_lifecycle_service.list_catalog(session)]


@skills_router.get("/candidates", response_model=list[SkillCandidateRead], status_code=status.HTTP_200_OK)
async def list_skill_candidates(session: Session = Depends(get_db_session)) -> list[SkillCandidateRead]:
    return [SkillCandidateRead.model_validate(item) for item in skill_lifecycle_service.list_candidates(session)]


@skills_router.get("/candidates/{candidate_id}", response_model=SkillCandidateRead, status_code=status.HTTP_200_OK)
async def get_skill_candidate(candidate_id: int, session: Session = Depends(get_db_session)) -> SkillCandidateRead:
    candidate = skill_lifecycle_service.get_candidate(session, candidate_id=candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill candidate not found.")
    return SkillCandidateRead.model_validate(candidate)


@skills_router.get("/candidates/{candidate_id}/provenance", response_model=SkillProvenanceRead, status_code=status.HTTP_200_OK)
async def get_skill_candidate_provenance(candidate_id: int, session: Session = Depends(get_db_session)) -> SkillProvenanceRead:
    candidate = skill_lifecycle_service.get_candidate(session, candidate_id=candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill candidate not found.")
    return _build_skill_provenance(
        session,
        origin_entity_type="skill_candidate",
        origin_entity_id=candidate_id,
        candidate_id=candidate_id,
    )


@skills_router.get("/revisions", response_model=list[SkillRevisionRead], status_code=status.HTTP_200_OK)
async def list_active_skill_revisions(
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> list[SkillRevisionRead]:
    return [
        SkillRevisionRead.model_validate(item)
        for item in skill_lifecycle_service.list_revisions(session, include_inactive=include_inactive)
    ]


@skills_router.get("/revisions/{revision_id}", response_model=SkillRevisionRead, status_code=status.HTTP_200_OK)
async def get_skill_revision(revision_id: int, session: Session = Depends(get_db_session)) -> SkillRevisionRead:
    revision = skill_lifecycle_service.get_revision(session, revision_id=revision_id)
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill revision not found.")
    return SkillRevisionRead.model_validate(revision)


@skills_router.get("/validations", response_model=list[SkillValidationRecordRead], status_code=status.HTTP_200_OK)
async def list_skill_validation_records(
    candidate_id: int | None = Query(default=None, ge=1),
    revision_id: int | None = Query(default=None, ge=1),
    skill_code: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[SkillValidationRecordRead]:
    return [
        SkillValidationRecordRead.model_validate(item)
        for item in skill_lifecycle_service.list_validation_records(
            session,
            candidate_id=candidate_id,
            revision_id=revision_id,
            skill_code=skill_code,
            limit=limit,
        )
    ]


@skills_router.get("/validations/summary", response_model=SkillValidationSummaryRead, status_code=status.HTTP_200_OK)
async def summarize_skill_validation_records(
    candidate_id: int | None = Query(default=None, ge=1),
    revision_id: int | None = Query(default=None, ge=1),
    skill_code: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> SkillValidationSummaryRead:
    return SkillValidationSummaryRead.model_validate(
        skill_lifecycle_service.summarize_validation_records(
            session,
            candidate_id=candidate_id,
            revision_id=revision_id,
            skill_code=skill_code,
            limit=limit,
        )
    )


@skills_router.get("/validations/compare", response_model=SkillValidationCompareRead, status_code=status.HTTP_200_OK)
async def compare_skill_validation_records(
    candidate_id: int | None = Query(default=None, ge=1),
    revision_id: int | None = Query(default=None, ge=1),
    skill_code: str | None = Query(default=None),
    baseline_validation_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> SkillValidationCompareRead:
    return SkillValidationCompareRead.model_validate(
        skill_lifecycle_service.compare_validation_records(
            session,
            candidate_id=candidate_id,
            revision_id=revision_id,
            skill_code=skill_code,
            baseline_validation_id=baseline_validation_id,
            limit=limit,
        )
    )


@skills_router.get("/validations/{validation_id}", response_model=SkillValidationRecordRead, status_code=status.HTTP_200_OK)
async def get_skill_validation_record(
    validation_id: int,
    session: Session = Depends(get_db_session),
) -> SkillValidationRecordRead:
    record = skill_lifecycle_service.get_validation_record(session, validation_record_id=validation_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill validation record not found.")
    return SkillValidationRecordRead.model_validate(record)


@skills_router.get("/revisions/{revision_id}/provenance", response_model=SkillProvenanceRead, status_code=status.HTTP_200_OK)
async def get_skill_revision_provenance(revision_id: int, session: Session = Depends(get_db_session)) -> SkillProvenanceRead:
    revision = skill_lifecycle_service.get_revision(session, revision_id=revision_id)
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill revision not found.")
    return _build_skill_provenance(
        session,
        origin_entity_type="skill_revision",
        origin_entity_id=revision_id,
        revision_id=revision_id,
    )


@skills_router.get("/dashboard", response_model=SkillDashboardRead, status_code=status.HTTP_200_OK)
async def get_skill_dashboard(session: Session = Depends(get_db_session)) -> SkillDashboardRead:
    return SkillDashboardRead.model_validate(skill_lifecycle_service.build_dashboard(session))


@skills_router.get("/gaps", response_model=list[SkillGapRead], status_code=status.HTTP_200_OK)
async def list_skill_gaps(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[SkillGapRead]:
    return [SkillGapRead.model_validate(item) for item in SkillGapService().list_gaps(session, limit=limit)]


@skills_router.get("/gaps/{gap_id}", response_model=SkillGapRead, status_code=status.HTTP_200_OK)
async def get_skill_gap(gap_id: int, session: Session = Depends(get_db_session)) -> SkillGapRead:
    gap = SkillGapService().get_gap(session, gap_id=gap_id)
    if gap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill gap not found.")
    return SkillGapRead.model_validate(gap)


@skills_router.post("/gaps/{gap_id}/review", response_model=SkillGapRead, status_code=status.HTTP_200_OK)
async def review_skill_gap(
    gap_id: int,
    payload: SkillGapReviewRequest,
    session: Session = Depends(get_db_session),
) -> SkillGapRead:
    try:
        gap = SkillGapService().review_gap(
            session,
            gap_id=gap_id,
            outcome=payload.outcome,
            summary=payload.summary,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SkillGapRead.model_validate(gap)


@skills_router.post("/gaps/{gap_id}/promote", response_model=SkillCandidateRead, status_code=status.HTTP_200_OK)
async def promote_skill_gap_to_candidate(
    gap_id: int,
    session: Session = Depends(get_db_session),
) -> SkillCandidateRead:
    try:
        candidate = SkillGapService().promote_gap_to_candidate(session, gap_id=gap_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SkillCandidateRead.model_validate(candidate)


@skills_router.post(
    "/candidates/{candidate_id}/validate",
    response_model=SkillCandidateValidationResult,
    status_code=status.HTTP_200_OK,
)
async def validate_skill_candidate(
    candidate_id: int,
    payload: SkillCandidateValidationRequest,
    session: Session = Depends(get_db_session),
) -> SkillCandidateValidationResult:
    validation_mode = str(payload.validation_mode or "").strip().lower()
    validation_outcome = str(payload.validation_outcome or "").strip().lower()
    if validation_mode not in {"paper", "replay"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="validation_mode must be paper or replay.")
    if validation_outcome not in {"approve", "reject"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="validation_outcome must be approve or reject.")
    try:
        result = skill_lifecycle_service.validate_candidate(
            session,
            candidate_id=candidate_id,
            validation_mode=validation_mode,
            validation_outcome=validation_outcome,
            summary=payload.summary,
            sample_size=payload.sample_size,
            win_rate=payload.win_rate,
            avg_pnl_pct=payload.avg_pnl_pct,
            max_drawdown_pct=payload.max_drawdown_pct,
            evidence=payload.evidence,
            activate=payload.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SkillCandidateValidationResult.model_validate(result)


@macro_router.get("/signals", response_model=list[MacroSignalRead], status_code=status.HTTP_200_OK)
async def list_macro_signals(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[MacroSignalRead]:
    return macro_context_service.list_signals(session, limit=limit)


@macro_router.post("/signals", response_model=MacroSignalRead, status_code=status.HTTP_201_CREATED)
async def create_macro_signal(
    payload: MacroSignalCreate,
    session: Session = Depends(get_db_session),
) -> MacroSignalRead:
    return macro_context_service.create_signal(session, payload)


@macro_router.get("/context", response_model=MacroContextRead, status_code=status.HTTP_200_OK)
async def get_macro_context(
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> MacroContextRead:
    return macro_context_service.get_context(session, limit=limit)


@macro_router.get("/state-snapshots", response_model=list[MarketStateSnapshotRead], status_code=status.HTTP_200_OK)
async def list_market_state_snapshots(
    limit: int = Query(default=20, ge=1, le=100),
    pdca_phase: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> list[MarketStateSnapshotRead]:
    return [MarketStateSnapshotRead.model_validate(item) for item in market_state_service.list_snapshots(session, limit=limit, pdca_phase=pdca_phase)]


@macro_router.get("/state-snapshots/latest", response_model=MarketStateSnapshotRead, status_code=status.HTTP_200_OK)
async def get_latest_market_state_snapshot(
    pdca_phase: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> MarketStateSnapshotRead:
    snapshot = market_state_service.get_latest_snapshot(session, pdca_phase=pdca_phase)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No market-state snapshot is available.")
    return MarketStateSnapshotRead.model_validate(snapshot)


@failure_patterns_router.get("", response_model=list[FailurePatternRead])
async def list_failure_patterns(session: Session = Depends(get_db_session)) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns(session)


@failure_patterns_router.get("/{strategy_id}", response_model=list[FailurePatternRead])
async def list_failure_patterns_for_strategy(
    strategy_id: int,
    session: Session = Depends(get_db_session),
) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns_for_strategy(session, strategy_id)


@auto_reviews_router.post("/losses/generate", response_model=AutoReviewBatchResult, status_code=status.HTTP_200_OK)
async def generate_loss_reviews(session: Session = Depends(get_db_session)) -> AutoReviewBatchResult:
    return auto_review_service.generate_pending_loss_reviews(session)


@pdca_router.get("/cycles", response_model=list[PDCACycleRead])
async def list_cycles(session: Session = Depends(get_db_session)) -> list[PDCACycleRead]:
    return pdca_service.list_cycles(session)


@pdca_router.post("/cycles", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
async def create_cycle(payload: PDCACycleCreate, session: Session = Depends(get_db_session)) -> PDCACycleRead:
    return pdca_service.create_cycle(session, payload)


@pdca_router.post("/run-daily", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
async def run_daily_plan(
    cycle_date: date | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PDCACycleRead:
    return pdca_service.create_daily_plan(session, cycle_date or date.today())


@orchestrator_router.post("/plan", response_model=OrchestratorPlanResponse, status_code=status.HTTP_201_CREATED)
async def plan_daily_cycle(
    payload: DailyPlanRequest,
    session: Session = Depends(get_db_session),
) -> OrchestratorPlanResponse:
    return orchestrator_service.plan_daily_cycle(session, payload)


@orchestrator_router.post("/do", response_model=OrchestratorDoResponse, status_code=status.HTTP_200_OK)
async def run_do_phase(session: Session = Depends(get_db_session)) -> OrchestratorDoResponse:
    return orchestrator_service.run_do_phase(session)


@orchestrator_router.post("/check", response_model=OrchestratorPhaseResponse, status_code=status.HTTP_200_OK)
async def run_check_phase(session: Session = Depends(get_db_session)) -> OrchestratorPhaseResponse:
    return orchestrator_service.run_check_phase(session)


@orchestrator_router.post("/act", response_model=OrchestratorActResponse, status_code=status.HTTP_200_OK)
async def run_act_phase(session: Session = Depends(get_db_session)) -> OrchestratorActResponse:
    return orchestrator_service.run_act_phase(session)


@chat_router.post("", response_model=BotChatResponse, status_code=status.HTTP_200_OK)
async def chat_with_bot(payload: BotChatRequest, session: Session = Depends(get_db_session)) -> BotChatResponse:
    return bot_chat_service.reply(session, payload.message)


@chat_router.get("/presets", response_model=list[ChatLLMPresetRead], status_code=status.HTTP_200_OK)
async def list_chat_llm_presets() -> list[ChatLLMPresetRead]:
    return chat_conversation_service.list_presets()


@chat_router.get("/conversations", response_model=list[ChatConversationRead], status_code=status.HTTP_200_OK)
async def list_chat_conversations(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=60, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> list[ChatConversationRead]:
    return [
        ChatConversationRead.model_validate(item)
        for item in chat_conversation_service.list_conversations(
            session,
            include_archived=include_archived,
            limit=limit,
        )
    ]


@chat_router.post("/conversations", response_model=ChatConversationRead, status_code=status.HTTP_201_CREATED)
async def create_chat_conversation(
    payload: ChatConversationCreate,
    session: Session = Depends(get_db_session),
) -> ChatConversationRead:
    try:
        conversation = chat_conversation_service.create_conversation(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ChatConversationRead.model_validate(conversation)


@chat_router.get("/conversations/{conversation_id}", response_model=ChatConversationDetailRead, status_code=status.HTTP_200_OK)
async def get_chat_conversation(
    conversation_id: int,
    session: Session = Depends(get_db_session),
) -> ChatConversationDetailRead:
    try:
        return chat_conversation_service.get_conversation_detail(session, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@chat_router.patch("/conversations/{conversation_id}", response_model=ChatConversationRead, status_code=status.HTTP_200_OK)
async def update_chat_conversation(
    conversation_id: int,
    payload: ChatConversationUpdate,
    session: Session = Depends(get_db_session),
) -> ChatConversationRead:
    try:
        conversation = chat_conversation_service.update_conversation(session, conversation_id, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return ChatConversationRead.model_validate(conversation)


@chat_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatConversationTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_chat_message(
    conversation_id: int,
    payload: ChatMessageCreate,
    session: Session = Depends(get_db_session),
) -> ChatConversationTurnResponse:
    try:
        return chat_conversation_service.add_message(session, conversation_id, payload)
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


@chat_router.post("/conversations/{conversation_id}/archive", response_model=ChatConversationRead, status_code=status.HTTP_200_OK)
async def archive_chat_conversation(
    conversation_id: int,
    session: Session = Depends(get_db_session),
) -> ChatConversationRead:
    try:
        conversation = chat_conversation_service.archive_conversation(session, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ChatConversationRead.model_validate(conversation)


@tools_router.get("", response_model=list[AgentToolDefinitionRead], status_code=status.HTTP_200_OK)
async def list_agent_tools() -> list[AgentToolDefinitionRead]:
    return [AgentToolDefinitionRead.model_validate(item) for item in agent_tool_gateway_service.list_tools()]


@tools_router.post("/execute", response_model=AgentToolCallResponse, status_code=status.HTTP_200_OK)
async def execute_agent_tool(
    payload: AgentToolCallRequest,
    session: Session = Depends(get_db_session),
) -> AgentToolCallResponse:
    try:
        result = agent_tool_gateway_service.execute(session, payload.tool_name, payload.arguments)
    except AgentToolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AgentToolCallResponse(tool_name=payload.tool_name, result=result)
