"""Admin endpoints — observability and monitoring.

Protected by the ADMIN_SECRET header in production.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["admin"])


@router.get("/stats")
async def admin_stats() -> dict[str, str]:
    """Return basic system statistics.

    Placeholder — will be expanded on Day 20 with:
    - Active workers count
    - Reviews today (count, success rate, avg cost)
    - Recent reviews table
    - Error log
    """
    return {"status": "admin endpoints coming on Day 20"}


@router.get("/repos/{repo_id}/index-status")
async def get_index_status(repo_id: int, request: Request) -> dict[str, Any]:
    """Return current repository indexing progress.

    Reads from the ``index_progress:{repo_id}`` Redis key written by
    ``RepositoryIndexer``.

    Returns:
        Progress dict with ``status``, ``total``, ``done``, ``pct_complete``,
        ``chunks_total`` fields.  Returns ``not_indexed`` when no progress key exists.
    """
    redis = request.app.state.redis
    key = f"index_progress:{repo_id}"

    try:
        raw = await redis.get(key)
    except Exception:
        return {
            "status": "error",
            "error": "Redis unavailable",
            "total": 0,
            "done": 0,
            "pct_complete": 0,
        }

    if not raw:
        return {
            "status": "not_indexed",
            "total": 0,
            "done": 0,
            "pct_complete": 0,
            "chunks_total": 0,
        }

    data: dict[str, Any] = json.loads(raw)
    total = data.get("total", 0)
    done = data.get("done", 0)
    pct = round(done / total * 100, 1) if total > 0 else 0

    return {**data, "pct_complete": pct}
