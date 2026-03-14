"""FastAPI application with lifespan: init DB, sync config, import logs."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import async_session, init_db
from app.dependencies import verify_api_key
from app.routers import (
    analytics,
    config_router,
    coordinator,
    events,
    health,
    logs,
    oauth,
    plan_usage,
    plans,
    projects,
    prompts,
    queue,
    runs,
    system,
    webhook,
)
from app.services.config_sync import sync_config_to_db
from app.services.log_importer import import_historical_runs
from app.services.stale_run_reaper import reap_stale_runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Interval (seconds) between periodic log re-scans
LOG_RESCAN_INTERVAL = 30

# Interval (seconds) between stale-run checks
STALE_RUN_CHECK_INTERVAL = 15


async def _periodic_log_import() -> None:
    """Background task: periodically re-scan log directory for new runs."""
    while True:
        await asyncio.sleep(LOG_RESCAN_INTERVAL)
        try:
            async with async_session() as db:
                imported = await import_historical_runs(db)
                if imported > 0:
                    logger.info("Periodic log import: %d new runs imported", imported)
        except Exception:
            logger.exception("Error in periodic log import")


async def _periodic_stale_run_check() -> None:
    """Background task: detect and reap runs left in 'running' state after agent dies."""
    while True:
        await asyncio.sleep(STALE_RUN_CHECK_INTERVAL)
        try:
            async with async_session() as db:
                reaped = await reap_stale_runs(db)
                if reaped > 0:
                    logger.info("Stale run reaper: marked %d orphaned runs as interrupted", reaped)
        except Exception:
            logger.exception("Error in stale run reaper")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create tables, sync config, import historical runs, start background tasks."""
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

    # 4. Reap any runs stuck in 'running' from a previous crash
    async with async_session() as db:
        reaped = await reap_stale_runs(db)
        if reaped > 0:
            logger.info("Startup reaper: marked %d orphaned runs as interrupted", reaped)

    # 5. Start periodic background tasks
    rescan_task = asyncio.create_task(_periodic_log_import())
    reaper_task = asyncio.create_task(_periodic_stale_run_check())
    logger.info("Started periodic log rescan (every %ds)", LOG_RESCAN_INTERVAL)
    logger.info("Started stale run reaper (every %ds)", STALE_RUN_CHECK_INTERVAL)

    yield

    # Cancel background tasks on shutdown
    rescan_task.cancel()
    reaper_task.cancel()
    for task in (rescan_task, reaper_task):
        with suppress(asyncio.CancelledError):
            await task
    logger.info("Shutting down dashboard backend")


app = FastAPI(
    title="Claude Agent Station",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for local frontend development
# Note: allow_credentials=True requires explicit origins (not "*") per the CORS spec.
# Configure via STATION_ALLOWED_ORIGINS env var for custom origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
# Exempt: health (monitoring), webhook (has own auth), logs.ws_router (WebSocket — has inline auth)
app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(logs.ws_router)

# Protected routers: require API key when STATION_API_KEY is configured
_auth = [Depends(verify_api_key)]
app.include_router(analytics.router, dependencies=_auth)
app.include_router(projects.router, dependencies=_auth)
app.include_router(runs.router, dependencies=_auth)
app.include_router(logs.ws_router)  # WS route: handles auth internally (HTTPBearer incompatible with WS)
app.include_router(logs.router, dependencies=_auth)
app.include_router(config_router.router, dependencies=_auth)
app.include_router(system.router, dependencies=_auth)
app.include_router(oauth.router, dependencies=_auth)
app.include_router(plans.router, dependencies=_auth)
app.include_router(events.router, dependencies=_auth)
app.include_router(coordinator.router, dependencies=_auth)
app.include_router(plan_usage.router, dependencies=_auth)
app.include_router(prompts.router, dependencies=_auth)
app.include_router(queue.router, dependencies=_auth)

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
