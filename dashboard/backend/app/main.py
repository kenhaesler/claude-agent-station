"""FastAPI application with lifespan: init DB, sync config, import logs."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session
from app.services.config_sync import sync_config_to_db
from app.services.log_importer import import_historical_runs

from app.routers import (
    health,
    projects,
    runs,
    logs,
    config_router,
    system,
    webhook,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create tables, sync config, import historical runs."""
    logger.info("Starting Claude Agent Station dashboard backend")
    logger.info("DB: %s", settings.db_path)
    logger.info("Log dir: %s", settings.log_dir)
    logger.info("Config: %s", settings.config_path)

    # 1. Create tables
    await init_db()
    logger.info("Database initialized")

    # 2. Sync config JSON -> DB
    async with async_session() as db:
        count = await sync_config_to_db(db)
        logger.info("Config sync complete: %d projects", count)

    # 3. Import historical runs
    async with async_session() as db:
        imported = await import_historical_runs(db)
        logger.info("Log import complete: %d new runs", imported)

    yield

    logger.info("Shutting down dashboard backend")


app = FastAPI(
    title="Claude Agent Station",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(health.router)
app.include_router(projects.router)
app.include_router(runs.router)
app.include_router(logs.router)
app.include_router(config_router.router)
app.include_router(system.router)
app.include_router(webhook.router)

# Serve frontend static files (must be last, catches all non-API routes)
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
    logger.info("Serving frontend from %s", _frontend_dist)
else:
    @app.get("/")
    async def root():
        return JSONResponse({
            "name": "Claude Agent Station",
            "version": "0.1.0",
            "endpoints": {
                "health": "/api/health",
                "projects": "/api/projects",
                "runs": "/api/runs",
                "config": "/api/config",
                "system": "/api/system/status",
                "docs": "/docs",
            },
        })
