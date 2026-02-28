"""GitHub webhook receiver.

POST /api/webhooks/github — receives all GitHub App webhook events.
Follows the strict pattern from ADR-0002 and ADR-0004:
  1. Read raw body (before JSON parsing)
  2. Verify HMAC-SHA256 signature (FIRST operation, no exceptions)
  3. Parse payload
  4. Route event
  5. Return 200 immediately

NO database queries, file I/O, or HTTP calls in this handler.
All heavy processing is dispatched to Celery tasks.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, Request, Depends

from app.config import Settings, get_settings
from app.core.security import verify_github_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/github", status_code=200)
async def receive_github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str = Header(default="unknown"),
    x_github_delivery: str = Header(default=""),
    config: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Receive and validate a GitHub webhook event.

    Flow:
        1. HMAC signature verification (< 1ms)
        2. JSON payload parsing (< 1ms)
        3. Event routing → stub task dispatch
        4. Return 200 OK immediately

    Returns:
        {"status": "accepted"} on success.

    Raises:
        HTTPException(403): if signature is missing or invalid.
    """
    # Step 1: Read raw body BEFORE parsing — needed for HMAC verification.
    # FastAPI's request.body() is a stream; must be read once before JSON parsing.
    body = await request.body()

    # Step 2: Verify signature — FIRST OPERATION, NO EXCEPTIONS.
    verify_github_signature(body, config.github_webhook_secret, x_hub_signature_256)

    # Step 3: Parse the validated payload.
    payload: dict = json.loads(body)
    action: str = payload.get("action", "")

    logger.info(
        "Webhook received",
        extra={
            "event": x_github_event,
            "action": action,
            "delivery_id": x_github_delivery,
        },
    )

    # Step 4: Route by event type.
    if x_github_event == "installation":
        _handle_installation_event(payload, action)

    elif x_github_event == "pull_request":
        _handle_pull_request_event(payload, action)

    elif x_github_event == "pull_request_review_comment":
        _handle_review_comment_event(payload, action)

    else:
        logger.debug(
            "Unhandled event type — returning 200 (no-op)",
            extra={"event": x_github_event, "action": action},
        )

    return {"status": "accepted"}


# ---------------------------------------------------------------------------
#  Event handlers  — thin routing functions, no business logic
# ---------------------------------------------------------------------------


def _handle_installation_event(payload: dict, action: str) -> None:
    """Handle GitHub App installation events."""
    installation_id: int = payload.get("installation", {}).get("id", 0)
    account: str = payload.get("installation", {}).get("account", {}).get("login", "unknown")

    if action == "created":
        repos = payload.get("repositories", [])
        logger.info(
            "New installation created",
            extra={
                "installation_id": installation_id,
                "account": account,
                "repo_count": len(repos),
            },
        )
        # TODO (Day 4): Create Installation + Repository DB records,
        #               enqueue index_repository task for each repo.

    elif action == "deleted":
        logger.info(
            "Installation deleted",
            extra={"installation_id": installation_id, "account": account},
        )
        # TODO (Day 4): Mark Installation as inactive.

    elif action in ("added", "removed"):
        repos_added = payload.get("repositories_added", [])
        repos_removed = payload.get("repositories_removed", [])
        logger.info(
            "Installation repositories changed",
            extra={
                "installation_id": installation_id,
                "added": len(repos_added),
                "removed": len(repos_removed),
            },
        )

    else:
        logger.debug("Installation event no-op", extra={"action": action})


def _handle_pull_request_event(payload: dict, action: str) -> None:
    """Handle pull request events (opened, synchronize, reopened)."""
    pr_actions_to_review = {"opened", "synchronize", "reopened"}

    if action not in pr_actions_to_review:
        logger.debug("PR event no-op", extra={"action": action})
        return

    pr = payload.get("pull_request", {})
    installation_id: int = payload.get("installation", {}).get("id", 0)
    repo_full_name: str = payload.get("repository", {}).get("full_name", "")
    repo_id: int = payload.get("repository", {}).get("id", 0)
    pr_number: int = pr.get("number", 0)
    head_sha: str = pr.get("head", {}).get("sha", "")
    base_sha: str = pr.get("base", {}).get("sha", "")
    pr_title: str = pr.get("title", "")
    author: str = pr.get("user", {}).get("login", "")

    logger.info(
        "PR event received — review candidate",
        extra={
            "action": action,
            "installation_id": installation_id,
            "repo": repo_full_name,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "author": author,
            "head_sha": head_sha[:8],
            "base_sha": base_sha[:8],
        },
    )

    # TODO (Day 3): Dispatch Celery task:
    # run_pr_review.apply_async(
    #     args=[installation_id, repo_full_name, repo_id, pr_number, head_sha, base_sha],
    #     queue="fast_lane",
    # )


def _handle_review_comment_event(payload: dict, action: str) -> None:
    """Handle pull request review comment events (replies like 'Fix this')."""
    if action != "created":
        logger.debug("Review comment event no-op", extra={"action": action})
        return

    comment = payload.get("comment", {})
    comment_id: int = comment.get("id", 0)
    comment_body: str = comment.get("body", "")
    in_reply_to_id: int | None = comment.get("in_reply_to_id")
    pr_number: int = payload.get("pull_request", {}).get("number", 0)
    repo_full_name: str = payload.get("repository", {}).get("full_name", "")

    logger.info(
        "PR review comment received",
        extra={
            "repo": repo_full_name,
            "pr_number": pr_number,
            "comment_id": comment_id,
            "in_reply_to_id": in_reply_to_id,
            "body_preview": comment_body[:80],
        },
    )

    # TODO (Day 18): Dispatch reply handler:
    # handle_pr_reply.apply_async(args=[...], queue="fast_lane")
