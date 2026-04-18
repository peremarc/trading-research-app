from fastapi import APIRouter

from app.domains.execution.api import exits_router, positions_router, trade_reviews_router

router = APIRouter()
router.include_router(positions_router, prefix="/positions", tags=["positions"])
router.include_router(exits_router, prefix="/exits", tags=["exits"])
router.include_router(trade_reviews_router, prefix="/trade-reviews", tags=["trade-reviews"])
