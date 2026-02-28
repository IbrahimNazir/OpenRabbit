"""GitHub API client for OpenRabbit.

Wraps the GitHub REST API with:
- JWT-based GitHub App authentication (ADR-0003)
- Installation Access Token caching in Redis (ADR-0012)
- PR diff fetching, file content retrieval, review posting
- Rate limit monitoring and automatic token refresh on 403

All methods use httpx.AsyncClient.  Sync callers in Celery workers
should use asyncio.run() or a dedicated event loop.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from jose import jwt as jose_jwt

from app.config import get_settings
from app.core.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubInstallationNotFoundError,
    GitHubRateLimitError,
    GitHubTokenExpiredError,
)

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# Common headers for every GitHub API request.
_BASE_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
}


class GitHubClient:
    """Async GitHub API client scoped to one installation.

    Each client instance is bound to a specific ``installation_id`` and
    caches its access token in Redis with a 55-minute TTL (tokens expire
    in 60 minutes; the 5-minute buffer avoids clock-skew problems).
    """

    CACHE_KEY_PREFIX = "github:token:"
    TOKEN_TTL_SECONDS = 55 * 60  # 55 minutes

    def __init__(self, installation_id: int, redis: Any) -> None:
        self.installation_id = installation_id
        self.redis = redis

    # ------------------------------------------------------------------
    #  Authentication
    # ------------------------------------------------------------------

    def _generate_app_jwt(self) -> str:
        """Generate a short-lived JWT for GitHub App-level authentication.

        Valid for ~9 minutes (GitHub maximum is 10).  Uses RS256 signing
        with the App's private key.
        """
        settings = get_settings()
        private_key = settings.github_private_key
        if not private_key:
            raise GitHubAuthError(
                "GitHub App private key is empty — check GITHUB_APP_PRIVATE_KEY_PATH"
            )

        now = int(time.time())
        payload = {
            "iat": now - 60,     # issued-at: 60s in the past for clock drift
            "exp": now + 540,    # expires in 9 minutes
            "iss": settings.github_app_id,
        }
        return jose_jwt.encode(payload, private_key, algorithm="RS256")

    async def get_access_token(self) -> str:
        """Get a valid installation access token, using Redis cache when available."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"

        # Try cache first.
        if self.redis is not None:
            cached = await self.redis.get(cache_key)
            if cached:
                return cached if isinstance(cached, str) else cached.decode("utf-8")

        # Cache miss — exchange JWT for a fresh installation token.
        token = await self._fetch_fresh_token()

        # Cache with 55-minute TTL.
        if self.redis is not None:
            await self.redis.setex(cache_key, self.TOKEN_TTL_SECONDS, token)

        return token

    async def _fetch_fresh_token(self) -> str:
        """Exchange App JWT for an installation-scoped access token."""
        app_jwt = self._generate_app_jwt()

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    **_BASE_HEADERS,
                },
            )

            if response.status_code == 401:
                raise GitHubAuthError(
                    "App JWT is invalid — check GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_APP_ID"
                )
            if response.status_code == 404:
                raise GitHubInstallationNotFoundError(
                    f"Installation {self.installation_id} not found — may have been uninstalled"
                )
            if response.status_code >= 400:
                raise GitHubAPIError(
                    f"Failed to get installation token: {response.text}",
                    status_code=response.status_code,
                )

            data: dict = response.json()
            logger.info(
                "Fresh installation token obtained",
                extra={"installation_id": self.installation_id},
            )
            return data["token"]

    async def _invalidate_token(self) -> None:
        """Force token refresh on next request (e.g., after 403 from GitHub API)."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"
        if self.redis is not None:
            await self.redis.delete(cache_key)

    # ------------------------------------------------------------------
    #  Core request method
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        accept: str | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Make an authenticated GitHub API request with automatic token refresh."""
        token = await self.get_access_token()

        request_headers = {
            "Authorization": f"Bearer {token}",
            **_BASE_HEADERS,
        }
        if accept:
            request_headers["Accept"] = accept
        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                headers=request_headers,
                json=json_body,
            )

            # Check rate limits on every response.
            await self._check_rate_limit(response)

            # Handle 403: token may be revoked — invalidate cache and retry once.
            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
                if remaining == "0":
                    reset_at = response.headers.get("X-RateLimit-Reset", "")
                    raise GitHubRateLimitError(
                        f"Rate limit exceeded. Resets at: {reset_at}",
                        reset_at=reset_at,
                    )
                # Token revoked — invalidate and retry.
                logger.warning(
                    "GitHub 403 — invalidating cached token and retrying",
                    extra={"installation_id": self.installation_id},
                )
                await self._invalidate_token()
                token = await self.get_access_token()
                request_headers["Authorization"] = f"Bearer {token}"
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    json=json_body,
                )

            return response

    async def _check_rate_limit(self, response: httpx.Response) -> None:
        """Log rate limit status from every GitHub API response."""
        remaining_str = response.headers.get("X-RateLimit-Remaining")
        if remaining_str is None:
            return

        remaining = int(remaining_str)
        limit = int(response.headers.get("X-RateLimit-Limit", -1))
        reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))

        if remaining < 100:
            logger.warning(
                "GitHub rate limit low",
                extra={
                    "installation_id": self.installation_id,
                    "remaining": remaining,
                    "limit": limit,
                    "resets_in_seconds": max(0, reset_ts - int(time.time())),
                },
            )

        # Store in Redis for admin monitoring.
        if self.redis is not None:
            await self.redis.setex(
                f"github:rate_limit:{self.installation_id}",
                300,  # 5-minute TTL
                json.dumps({"remaining": remaining, "limit": limit, "reset": reset_ts}),
            )

    # ------------------------------------------------------------------
    #  Public API methods
    # ------------------------------------------------------------------

    async def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the raw unified diff text for a pull request.

        Uses ``Accept: application/vnd.github.v3.diff`` to get the raw
        diff instead of JSON.

        Returns:
            The full unified diff as a string.

        Raises:
            GitHubAPIError: If the PR or repo is not found.
        """
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}"
        response = await self._request("GET", url, accept="application/vnd.github.v3.diff")

        if response.status_code == 404:
            raise GitHubAPIError(
                f"PR #{pr_number} not found in {repo_full_name}",
                status_code=404,
            )
        response.raise_for_status()

        return response.text

    async def get_file_content(
        self, repo_full_name: str, file_path: str, ref: str
    ) -> str:
        """Return decoded file content at a given SHA/branch.

        Args:
            repo_full_name: ``owner/repo`` format.
            file_path: Path within the repository.
            ref: Git ref (SHA, branch name, or tag).

        Returns:
            Decoded file content as a string.
        """
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{file_path}"
        response = await self._request(
            "GET",
            url,
            accept="application/vnd.github.v3.raw",
            headers={"ref": ref} if ref else None,
        )

        if response.status_code == 404:
            raise GitHubAPIError(
                f"File {file_path} not found at ref {ref} in {repo_full_name}",
                status_code=404,
            )
        response.raise_for_status()

        return response.text

    async def post_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        comments: list[dict[str, Any]],
        body: str,
    ) -> dict:
        """Post a pull request review with inline comments.

        Args:
            repo_full_name: ``owner/repo`` format.
            pr_number: PR number.
            head_sha: Commit SHA the review is for.
            comments: List of ``{path, position, body}`` dicts.
            body: Top-level review body text.

        Returns:
            The created review as a dict.
        """
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload: dict[str, Any] = {
            "commit_id": head_sha,
            "body": body,
            "event": "COMMENT",
            "comments": comments,
        }
        response = await self._request("POST", url, json_body=payload)

        if response.status_code == 422:
            logger.warning(
                "GitHub rejected review — likely invalid position",
                extra={
                    "repo": repo_full_name,
                    "pr_number": pr_number,
                    "response": response.text[:500],
                },
            )
            raise GitHubAPIError(
                f"GitHub rejected review for PR #{pr_number}: {response.text[:200]}",
                status_code=422,
            )
        response.raise_for_status()

        return response.json()

    async def post_review_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        in_reply_to: int | None = None,
    ) -> dict:
        """Post a top-level PR comment or reply to an existing comment.

        Args:
            repo_full_name: ``owner/repo`` format.
            pr_number: PR number.
            body: Comment body (markdown).
            in_reply_to: If replying, the ``comment_id`` to reply to.

        Returns:
            The created comment as a dict.
        """
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
        payload: dict[str, Any] = {"body": body}

        if in_reply_to is not None:
            # Use the review comment reply endpoint instead.
            url = (
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/"
                f"comments/{in_reply_to}/replies"
            )

        response = await self._request("POST", url, json_body=payload)
        response.raise_for_status()

        return response.json()
