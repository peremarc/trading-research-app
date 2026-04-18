from fastapi import APIRouter

from app.domains.learning.api import (
    auto_reviews_router,
    chat_router,
    failure_patterns_router,
    journal_router,
    macro_router,
    memory_router,
    orchestrator_router,
    pdca_router,
    tools_router,
)

router = APIRouter()
router.include_router(journal_router, prefix="/journal", tags=["journal"])
router.include_router(chat_router, prefix="/chat", tags=["chat"])
router.include_router(tools_router, prefix="/agent-tools", tags=["agent-tools"])
router.include_router(memory_router, prefix="/memory", tags=["memory"])
router.include_router(macro_router, prefix="/macro", tags=["macro"])
router.include_router(failure_patterns_router, prefix="/failure-patterns", tags=["failure-patterns"])
router.include_router(auto_reviews_router, prefix="/auto-reviews", tags=["auto-reviews"])
router.include_router(pdca_router, prefix="/pdca", tags=["pdca"])
router.include_router(orchestrator_router, prefix="/orchestrator", tags=["orchestrator"])
