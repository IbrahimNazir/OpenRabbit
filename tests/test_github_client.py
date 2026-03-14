"""Tests for GitHub API client.

Tests for app/core/github_client.py — authentication, token management,
PR diff/file fetching, and review posting.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any
import asyncio

import pytest
import httpx

from app.core.github_client import GitHubClient
from app.core.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubTokenExpiredError,
    GitHubInstallationNotFoundError,
    GitHubRateLimitError,
)


# =============================================================================
#  Fixtures
# =============================================================================


@pytest.fixture
def mock_http_client() -> AsyncMock:
    """Mock httpx.AsyncClient for GitHub API calls."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for token caching."""
    return AsyncMock()


@pytest.fixture
def github_client(mock_http_client: AsyncMock, mock_redis: AsyncMock) -> GitHubClient:
    """Create GitHubClient with mocked HTTP and Redis."""
    client = GitHubClient(
        installation_id=123,
        redis=mock_redis,
        client=mock_http_client,
    )
    return client


# =============================================================================
#  JWT Generation Tests (3 tests)
# =============================================================================


@pytest.mark.unit
def test_generate_app_jwt_success() -> None:
    """Test successful JWT generation."""
    with patch("app.core.github_client.get_settings") as mock_settings:
        mock_settings.return_value.github_private_key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA2a2rwplg8WFU4qrLnG5f8nA5V8xYlB3F5mX0Z9Z8Z9Z8Z9Z8\n"
            "-----END RSA PRIVATE KEY-----"
        )
        mock_settings.return_value.github_app_id = 123

        with patch("app.core.github_client.jose_jwt.encode") as mock_encode:
            mock_encode.return_value = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."

            client = GitHubClient(installation_id=123, redis=None)
            jwt_token = client._generate_app_jwt()

            assert jwt_token == "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
            mock_encode.assert_called_once()


@pytest.mark.unit
def test_generate_app_jwt_missing_private_key() -> None:
    """Test JWT generation fails when private key is missing."""
    with patch("app.core.github_client.get_settings") as mock_settings:
        mock_settings.return_value.github_private_key = None
        mock_settings.return_value.github_app_id = 123

        client = GitHubClient(installation_id=123, redis=None)

        with pytest.raises(GitHubAuthError):
            client._generate_app_jwt()


@pytest.mark.unit
def test_generate_app_jwt_missing_app_id() -> None:
    """Test JWT generation fails when app ID is missing."""
    with patch("app.core.github_client.get_settings") as mock_settings:
        mock_settings.return_value.github_private_key = "-----BEGIN RSA PRIVATE KEY-----\n..."
        mock_settings.return_value.github_app_id = None

        client = GitHubClient(installation_id=123, redis=None)

        with pytest.raises(GitHubAuthError):
            client._generate_app_jwt()


# =============================================================================
#  Token Management Tests (4 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_access_token_from_cache(
    github_client: GitHubClient, mock_redis: AsyncMock
) -> None:
    """Test token is retrieved from Redis cache."""
    cached_token = "ghs_cached_token_123"
    mock_redis.get = AsyncMock(return_value=cached_token)

    token = await github_client.get_access_token()

    assert token == cached_token
    mock_redis.get.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_access_token_cache_miss_fetches_fresh(
    github_client: GitHubClient, mock_redis: AsyncMock, mock_http_client: AsyncMock
) -> None:
    """Test fresh token is fetched and cached on cache miss."""
    mock_redis.get = AsyncMock(return_value=None)  # Cache miss

    fresh_token = "ghs_fresh_token_456"
    mock_response = AsyncMock()
    mock_response.status_code = 201
    mock_response.json = MagicMock(return_value={"token": fresh_token})
    mock_http_client.post = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "_generate_app_jwt", return_value="jwt_token"):
        token = await github_client.get_access_token()

    assert token == fresh_token
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_access_token_redis_timeout_disables_cache(
    github_client: GitHubClient, mock_redis: AsyncMock, mock_http_client: AsyncMock
) -> None:
    """Test Redis timeout disables caching and fetches fresh token."""
    mock_redis.get = AsyncMock(side_effect=asyncio.TimeoutError())

    fresh_token = "ghs_timeout_token_789"
    mock_response = AsyncMock()
    mock_response.status_code = 201
    mock_response.json = MagicMock(return_value={"token": fresh_token})
    mock_http_client.post = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "_generate_app_jwt", return_value="jwt_token"):
        token = await github_client.get_access_token()

    assert token == fresh_token
    assert github_client.redis is None  # Cache disabled


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invalidate_token_removes_from_cache(
    github_client: GitHubClient, mock_redis: AsyncMock
) -> None:
    """Test token invalidation removes from Redis cache."""
    mock_redis.delete = AsyncMock()

    await github_client._invalidate_token()

    mock_redis.delete.assert_called_once()


# =============================================================================
#  PR Diff Tests (3 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_pr_diff_success(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test successful PR diff retrieval."""
    diff_content = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@"
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = diff_content
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.get_pr_diff("owner/repo", 42)

    assert result == diff_content


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_pr_diff_not_found(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test PR diff returns 404 when PR not found."""
    mock_response = AsyncMock()
    mock_response.status_code = 404
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            with pytest.raises(GitHubAPIError):
                await github_client.get_pr_diff("owner/repo", 999)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_pr_diff_server_error(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test PR diff handles 500 server error."""
    mock_response = AsyncMock()
    mock_response.status_code = 500
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)
    )
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await github_client.get_pr_diff("owner/repo", 42)


# =============================================================================
#  File Content Tests (2 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_file_content_success(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test successful file content retrieval."""
    file_content = "def hello():\n    return 'world'"
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = file_content
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.get_file_content(
                "owner/repo", "src/main.py", "abc123"
            )

    assert result == file_content


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_file_content_not_found(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test file content returns 404 when file not found."""
    mock_response = AsyncMock()
    mock_response.status_code = 404
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            with pytest.raises(GitHubAPIError):
                await github_client.get_file_content(
                    "owner/repo", "nonexistent.py", "abc123"
                )


# =============================================================================
#  Review Posting Tests (3 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_success(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test successful review posting."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"id": 999, "state": "COMMENTED"})
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.post_review(
                repo_full_name="owner/repo",
                pr_number=42,
                head_sha="abc123",
                comments=[{"path": "file.py", "position": 10, "body": "Comment"}],
                body="Review body",
            )

    assert result["id"] == 999
    assert result["state"] == "COMMENTED"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_unprocessable_entity(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test review posting fails with 422 Unprocessable Entity."""
    mock_response = AsyncMock()
    mock_response.status_code = 422
    mock_response.text = "Invalid position in diff"
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            with pytest.raises(GitHubAPIError):
                await github_client.post_review(
                    repo_full_name="owner/repo",
                    pr_number=42,
                    head_sha="abc123",
                    comments=[],
                    body="Review",
                )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_empty_comments(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test review posting with no comments (body only)."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"id": 888})
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.post_review(
                repo_full_name="owner/repo",
                pr_number=42,
                head_sha="abc123",
                comments=[],
                body="Review with no inline comments",
            )

    assert result["id"] == 888


# =============================================================================
#  Review Comment Tests (3 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_comment_success(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test successful review comment posting."""
    mock_response = AsyncMock()
    mock_response.status_code = 201
    mock_response.json = MagicMock(return_value={"id": 888, "body": "Comment"})
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.post_review_comment(
                repo_full_name="owner/repo",
                pr_number=42,
                body="Test comment",
            )

    assert result["id"] == 888


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_comment_as_reply(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test posting a reply to an existing comment."""
    mock_response = AsyncMock()
    mock_response.status_code = 201
    mock_response.json = MagicMock(return_value={"id": 777, "body": "Reply"})
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.post_review_comment(
                repo_full_name="owner/repo",
                pr_number=42,
                body="Reply to comment",
                in_reply_to=666,  # Reply to comment 666
            )

    assert result["id"] == 777
    # Verify the reply endpoint was used
    call_args = mock_http_client.request.call_args
    assert "comments/666/replies" in call_args[0][1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_review_comment_markdown_formatting(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test review comment with markdown formatting."""
    markdown_body = "**Bold** and *italic* and `code`"
    mock_response = AsyncMock()
    mock_response.status_code = 201
    mock_response.json = MagicMock(return_value={"id": 555})
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
            result = await github_client.post_review_comment(
                repo_full_name="owner/repo",
                pr_number=42,
                body=markdown_body,
            )

    assert result["id"] == 555


# =============================================================================
#  Review Comment Building Tests (3 tests)
# =============================================================================


@pytest.mark.unit
def test_build_review_comment_single_line() -> None:
    """Test building single-line comment structure."""
    client = GitHubClient(installation_id=123, redis=None)
    comment = client.build_review_comment(
        file_path="app/main.py",
        body="Single-line comment",
        line_start=10,
        line_end=10,
        diff_position=5,
        position_map={10: 5},
    )

    assert comment["path"] == "app/main.py"
    assert comment["body"] == "Single-line comment"
    assert comment["position"] == 5


@pytest.mark.unit
def test_build_review_comment_multi_line() -> None:
    """Test building multi-line comment structure."""
    client = GitHubClient(installation_id=123, redis=None)
    position_map = {10: 5, 11: 6, 12: 7}
    comment = client.build_review_comment(
        file_path="app/main.py",
        body="Multi-line comment",
        line_start=10,
        line_end=12,
        diff_position=5,
        position_map=position_map,
    )

    assert comment["path"] == "app/main.py"
    assert comment["body"] == "Multi-line comment"
    assert comment["start_line"] == 10
    assert comment["line"] == 12
    assert comment["start_side"] == "RIGHT"
    assert comment["side"] == "RIGHT"


@pytest.mark.unit
def test_build_review_comment_multi_line_fallback_missing_position() -> None:
    """Test multi-line comment falls back to single-line if position not in map."""
    client = GitHubClient(installation_id=123, redis=None)
    position_map = {10: 5}  # Missing line 12
    comment = client.build_review_comment(
        file_path="app/main.py",
        body="Fallback comment",
        line_start=10,
        line_end=12,
        diff_position=5,
        position_map=position_map,
    )

    # Should fall back to single-line format
    assert "position" in comment
    assert "start_line" not in comment


# =============================================================================
#  Token Refresh Tests (2 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_token_refresh_on_403(
    github_client: GitHubClient, mock_http_client: AsyncMock, mock_redis: AsyncMock
) -> None:
    """Test automatic token refresh on 403 Forbidden."""
    # First request returns 403, second returns success
    response_403 = AsyncMock()
    response_403.status_code = 403
    response_403.headers = {"X-RateLimit-Remaining": "5"}

    response_success = AsyncMock()
    response_success.status_code = 200
    response_success.text = "content"
    response_success.headers = {"X-RateLimit-Remaining": "5"}

    mock_http_client.request = AsyncMock(
        side_effect=[response_403, response_success]
    )

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.side_effect = ["old_token", "new_token"]
        with patch.object(github_client, "_invalidate_token", new_callable=AsyncMock) as mock_invalidate:
            with patch.object(github_client, "_check_rate_limit", new_callable=AsyncMock):
                result = await github_client.get_pr_diff("owner/repo", 42)

    # Token should be invalidated and refreshed
    mock_invalidate.assert_called_once()
    assert result == "content"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rate_limit_error_on_exhaustion(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test 429 rate limit handling."""
    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "60"}
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=mock_response)
    )
    mock_http_client.request = AsyncMock(return_value=mock_response)

    with patch.object(github_client, "get_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "ghs_token_123"
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.HTTPStatusError):
                await github_client.get_pr_diff("owner/repo", 42)

            # Should have slept before retrying
            mock_sleep.assert_called_once()


# =============================================================================
#  Rate Limit Monitoring Tests (2 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_rate_limit_stores_in_redis(
    github_client: GitHubClient, mock_redis: AsyncMock
) -> None:
    """Test rate limit info is stored in Redis."""
    mock_response = AsyncMock()
    mock_response.headers = {
        "X-RateLimit-Remaining": "50",
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Reset": "1234567890",
    }

    await github_client._check_rate_limit(mock_response)

    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    assert "github:rate_limit" in call_args[0][0]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_rate_limit_logs_warning_when_low(
    github_client: GitHubClient, mock_redis: AsyncMock
) -> None:
    """Test warning is logged when rate limit is low."""
    mock_response = AsyncMock()
    mock_response.headers = {
        "X-RateLimit-Remaining": "50",  # Below 100 threshold
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Reset": "1234567890",
    }

    with patch("app.core.github_client.logger") as mock_logger:
        await github_client._check_rate_limit(mock_response)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "rate limit low" in call_args[0][0].lower()


# =============================================================================
#  Client Lifecycle Tests (1 test)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_aclose_closes_http_client(
    github_client: GitHubClient, mock_http_client: AsyncMock
) -> None:
    """Test aclose properly closes the HTTP client."""
    mock_http_client.aclose = AsyncMock()

    await github_client.aclose()

    mock_http_client.aclose.assert_called_once()
