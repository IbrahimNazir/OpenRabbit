"""Celery application configuration.

Implements ADR-0013: dual-queue architecture with Redis broker.
- ``fast_lane`` queue: normal PRs (concurrency=4)
- ``slow_lane`` queue: large PRs >50 files (concurrency=1)

Start workers with:
    celery -A app.tasks.celery_app worker -Q fast_lane,slow_lane --loglevel=info
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_init, worker_shutdown

from app.config import get_settings

logger = logging.getLogger(__name__)


def _create_celery_app() -> Celery:
    """Build and configure the Celery application."""
    settings = get_settings()

    app = Celery(
        "openrabbit",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )

    app.conf.update(
        # Serialization
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",

        # Time limits per ADR-0013
        task_soft_time_limit=180,
        task_hard_time_limit=300,

        # Retry defaults
        task_default_retry_delay=30,
        task_max_retries=3,

        # Queue routing
        task_default_queue="fast_lane",
        task_queues={
            "fast_lane": {"exchange": "fast_lane", "routing_key": "fast_lane"},
            "slow_lane": {"exchange": "slow_lane", "routing_key": "slow_lane"},
        },

        # Monitoring
        worker_send_task_events=True,
        task_send_sent_event=True,

        # Prefetch — process one task at a time per worker process
        worker_prefetch_multiplier=1,

        # Timezone
        timezone="UTC",
        enable_utc=True,
    )

    # Auto-discover tasks in app.tasks package
    app.autodiscover_tasks(["app.tasks"])

    return app


celery_app = _create_celery_app()


# ---------------------------------------------------------------------------
#  Worker lifecycle signals
# ---------------------------------------------------------------------------


@worker_init.connect
def _on_worker_init(**kwargs: object) -> None:
    """Initialize sync database engine when Celery worker starts."""
    from app.models.database import init_sync_db

    init_sync_db()
    logger.info("Celery worker initialized — sync DB engine ready")


@worker_shutdown.connect
def _on_worker_shutdown(**kwargs: object) -> None:
    """Clean up resources when Celery worker stops."""
    logger.info("Celery worker shutting down")
