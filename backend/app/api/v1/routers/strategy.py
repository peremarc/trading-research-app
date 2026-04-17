from fastapi import APIRouter

from app.domains.strategy.api import screeners_router, strategies_router, watchlists_router
from app.domains.strategy.evolution import strategy_evolution_router, strategy_health_router, strategy_lab_router

router = APIRouter()
router.include_router(strategies_router, prefix="/strategies", tags=["strategies"])
router.include_router(screeners_router, prefix="/screeners", tags=["screeners"])
router.include_router(watchlists_router, prefix="/watchlists", tags=["watchlists"])
router.include_router(strategy_health_router, prefix="/strategy-health", tags=["strategy-health"])
router.include_router(strategy_evolution_router, prefix="/strategy-evolution", tags=["strategy-evolution"])
router.include_router(strategy_lab_router, prefix="/strategy-lab", tags=["strategy-lab"])
