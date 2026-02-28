"""Health-check endpoint.

Docker Compose health checks and load balancers hit this endpoint
to verify the application is running and responsive.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return a simple health status.

    Returns 200 with ``{"status": "healthy"}`` if the API is responding.
    Future: add Redis/Postgres connectivity checks.
    """
    return {"status": "healthy"}
