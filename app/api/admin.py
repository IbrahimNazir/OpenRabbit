"""Admin endpoints — stubs for Day 20.

These endpoints provide basic observability and monitoring.
Protected by the ADMIN_SECRET header in production.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["admin"])


@router.get("/stats")
async def admin_stats() -> dict[str, str]:
    """Return basic system statistics.

    Placeholder — will be implemented on Day 20 with:
    - Active workers count
    - Reviews today (count, success rate, avg cost)
    - Recent reviews table
    - Error log
    """
    return {"status": "admin endpoints coming on Day 20"}
