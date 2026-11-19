"""SmartStock AI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.channels.meta import MetaClient
from app.config import Settings, normalize_database_url
from app.db import apply_migrations_retry, open_pool
from app.llm.client import LLMClient
from app.demo.routes import router as demo_router
from app.legal.routes import router as legal_router
from app.webhook.routes import register


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.database_url:
        settings.database_url = normalize_database_url(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        app.state.settings = settings
        app.state.http = httpx.AsyncClient(timeout=30)
        app.state.meta = MetaClient(settings, app.state.http)
        app.state.llm = LLMClient(settings, app.state.http)
        app.state.db = None
        app.state.scheduler = None
        if settings.database_url:
            await asyncio.to_thread(apply_migrations_retry, settings.database_url)
            app.state.db = await open_pool(settings.database_url)
            if settings.seed_demo:
                from app.demo.seed import seed_demo

                await asyncio.to_thread(seed_demo, settings.database_url)
            if settings.scheduler_enabled:
                from app.scheduler.jobs import start_scheduler

                app.state.scheduler = start_scheduler(app)
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.shutdown(wait=False)
            if app.state.db is not None:
                await app.state.db.close()
            await app.state.http.aclose()

    app = FastAPI(title="SmartStock AI", lifespan=lifespan)
    register(app)
    app.include_router(demo_router)
    app.include_router(legal_router)
    return app


app = create_app()
