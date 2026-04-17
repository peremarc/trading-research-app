from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.domains.system.runtime import scheduler_service
from app.domains.system.services import SeedService


# Windows can resolve .js as text/plain depending on local MIME config.
mimetypes.add_type("application/javascript", ".js")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.bootstrap_seed_on_startup:
        with SessionLocal() as session:
            SeedService().seed_initial_data(session)
    scheduler_service.start()
    try:
        yield
    finally:
        scheduler_service.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    frontend_dir = Path(__file__).resolve().parent / "frontend"
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    app.mount("/assets", StaticFiles(directory=frontend_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    async def serve_frontend() -> FileResponse:
        return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
