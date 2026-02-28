"""FastAPI application entry point.

Start with:
    uvicorn app.main:app --reload

The app skeleton follows ADR-0002 (FastAPI + Uvicorn) with:
- Lifespan events for DB + Redis initialization
- CORS middleware configured
- Router includes for webhooks, health, and admin
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

# Enforce Python 3.12+ per ADR-0001.
assert sys.version_info >= (3, 12), "OpenRabbit requires Python 3.12+"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize and tear down shared resources."""
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("OpenRabbit starting up")

    # TODO (Day 1 Task 1.4): Initialize async DB pool
    # await init_db()

    # TODO (Day 1 Task 1.4): Initialize Redis connection
    # await init_redis()

    yield

    # Shutdown
    logger.info("OpenRabbit shutting down")
    # TODO: close_db(), close_redis()


# ---------------------------------------------------------------------------
#  FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenRabbit",
    description="AI-powered GitHub PR reviewer — self-hosted, open-source",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS — permissive for development; restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
#  Router Registration
# ---------------------------------------------------------------------------

# Import routers lazily to avoid circular-import issues.
from app.api.webhooks import router as webhook_router  # noqa: E402
from app.api.health import router as health_router  # noqa: E402
from app.api.admin import router as admin_router  # noqa: E402

app.include_router(webhook_router, prefix="/api/webhooks")
app.include_router(health_router)
app.include_router(admin_router, prefix="/admin")
