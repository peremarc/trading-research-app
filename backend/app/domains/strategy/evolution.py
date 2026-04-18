from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.strategy.schemas import (
    CandidateValidationSnapshotRead,
    StrategyActivationEventRead,
    StrategyChangeEventRead,
    StrategyLabBatchResult,
    StrategyPipelineRead,
    StrategyScorecardRead,
)
from app.domains.strategy.services import StrategyEvolutionService, StrategyLabService, StrategyScoringService

strategy_health_router = APIRouter()
strategy_evolution_router = APIRouter()
strategy_lab_router = APIRouter()

strategy_scoring_service = StrategyScoringService()
strategy_evolution_service = StrategyEvolutionService()
strategy_lab_service = StrategyLabService()


@strategy_health_router.get("", response_model=list[StrategyScorecardRead])
async def list_strategy_health(session: Session = Depends(get_db_session)) -> list[StrategyScorecardRead]:
    return strategy_scoring_service.list_latest(session)


@strategy_health_router.get("/pipelines", response_model=list[StrategyPipelineRead])
async def list_strategy_pipelines(session: Session = Depends(get_db_session)) -> list[StrategyPipelineRead]:
    return strategy_scoring_service.list_pipelines(session)


@strategy_health_router.get("/{strategy_id}", response_model=StrategyScorecardRead)
async def get_strategy_health(strategy_id: int, session: Session = Depends(get_db_session)) -> StrategyScorecardRead:
    scorecard = strategy_scoring_service.get_latest(session, strategy_id)
    if scorecard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy scorecard not found")
    return scorecard


@strategy_health_router.get("/{strategy_id}/pipeline", response_model=StrategyPipelineRead)
async def get_strategy_pipeline(strategy_id: int, session: Session = Depends(get_db_session)) -> StrategyPipelineRead:
    try:
        return strategy_scoring_service.get_pipeline(session, strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@strategy_health_router.post("/recalculate", response_model=list[StrategyScorecardRead], status_code=status.HTTP_200_OK)
async def recalculate_strategy_health(session: Session = Depends(get_db_session)) -> list[StrategyScorecardRead]:
    return strategy_scoring_service.recalculate_all(session)


@strategy_evolution_router.get("/changes", response_model=list[StrategyChangeEventRead])
async def list_strategy_change_events(session: Session = Depends(get_db_session)) -> list[StrategyChangeEventRead]:
    return strategy_evolution_service.list_change_events(session)


@strategy_evolution_router.get("/activations", response_model=list[StrategyActivationEventRead])
async def list_strategy_activation_events(session: Session = Depends(get_db_session)) -> list[StrategyActivationEventRead]:
    return strategy_evolution_service.list_activation_events(session)


@strategy_evolution_router.get("/candidate-validations", response_model=list[CandidateValidationSnapshotRead])
async def list_candidate_validations(session: Session = Depends(get_db_session)) -> list[CandidateValidationSnapshotRead]:
    return strategy_evolution_service.list_candidate_validation_summaries(session)


@strategy_lab_router.post("/evolve-success-patterns", response_model=StrategyLabBatchResult, status_code=status.HTTP_200_OK)
async def evolve_success_patterns(session: Session = Depends(get_db_session)) -> StrategyLabBatchResult:
    return StrategyLabBatchResult.model_validate(strategy_lab_service.evolve_from_success_patterns(session))
