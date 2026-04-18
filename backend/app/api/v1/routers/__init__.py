from app.api.v1.routers.execution import router as execution_router
from app.api.v1.routers.learning import router as learning_router
from app.api.v1.routers.market import router as market_router
from app.api.v1.routers.strategy import router as strategy_router
from app.api.v1.routers.system import router as system_router

__all__ = [
    "execution_router",
    "learning_router",
    "market_router",
    "strategy_router",
    "system_router",
]
