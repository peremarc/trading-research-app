from fastapi import APIRouter

from app.domains.market.api import (
    analysis_router,
    calendar_router,
    market_data_router,
    news_router,
    research_router,
    signals_router,
    trade_signals_router,
    work_queue_router,
)

router = APIRouter()
router.include_router(analysis_router, prefix="/analysis", tags=["analysis"])
router.include_router(market_data_router, prefix="/market-data", tags=["market-data"])
router.include_router(signals_router, prefix="/signals", tags=["signals"])
router.include_router(trade_signals_router, prefix="/trade-signals", tags=["trade-signals"])
router.include_router(research_router, prefix="/research", tags=["research"])
router.include_router(news_router, prefix="/news", tags=["news"])
router.include_router(calendar_router, prefix="/calendar", tags=["calendar"])
router.include_router(work_queue_router, prefix="/work-queue", tags=["work-queue"])
