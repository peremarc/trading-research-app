from fastapi import APIRouter

from app.api.v1.routers import execution_router, learning_router, market_router, strategy_router, system_router

api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(strategy_router)
api_router.include_router(market_router)
api_router.include_router(execution_router)
api_router.include_router(learning_router)
