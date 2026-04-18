from fastapi import APIRouter

from app.domains.system.api import bootstrap_router, events_router, health_router, scheduler_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(bootstrap_router, prefix="/bootstrap", tags=["bootstrap"])
router.include_router(scheduler_router, prefix="/scheduler", tags=["scheduler"])
router.include_router(events_router, prefix="/events", tags=["events"])
