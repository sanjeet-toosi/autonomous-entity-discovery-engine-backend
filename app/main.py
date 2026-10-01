from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.router import api_router
from app.config import get_settings
from app.core.database import dispose_engine
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis
from app.events.websocket import hub
from app.graph.neo4j_store import get_neo4j
from app.llm.router import get_router
from app.runs.orchestrator import orchestrator

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    router = get_router()
    log.info("starting", extra={"extra": {"llm_provider": router.provider_name, "model": router.model_name, "neo4j": get_neo4j().enabled}})
    await hub.start()
    await orchestrator.start()
    try:
        yield
    finally:
        await orchestrator.stop()
        await hub.stop()
        await get_neo4j().close()
        await close_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Business Graph Control Plane", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.include_router(api_router)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return ORJSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return app


app = create_app()
