"""Domain-specific exceptions for OpenRabbit.

Every external integration failure should raise one of these exceptions
so that calling code can handle failures precisely. Never raise bare
Exception or use generic error types.
"""

from __future__ import annotations


# =============================================================================
# Webhook / Security
# =============================================================================


class InvalidWebhookSignatureError(Exception):
    """HMAC-SHA256 signature verification failed for an incoming webhook."""


# =============================================================================
# GitHub API
# =============================================================================


class GitHubError(Exception):
    """Base exception for all GitHub API failures."""


class GitHubAuthError(GitHubError):
    """App JWT is invalid — check GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_APP_ID."""


class GitHubTokenExpiredError(GitHubError):
    """Installation access token has expired or been revoked."""


class GitHubInstallationNotFoundError(GitHubError):
    """Installation ID does not exist — may have been uninstalled."""


class GitHubRateLimitError(GitHubError):
    """GitHub API rate limit exceeded for this installation."""

    def __init__(self, message: str = "Rate limit exceeded", reset_at: str = "") -> None:
        self.reset_at = reset_at
        super().__init__(message)


class GitHubAPIError(GitHubError):
    """Generic GitHub API error with status code context."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.status_code = status_code
        super().__init__(message)


# =============================================================================
# LLM / AI
# =============================================================================


class LLMError(Exception):
    """Base exception for LLM integration failures."""


class LLMParseError(LLMError):
    """LLM returned a response that could not be parsed (invalid JSON, etc.)."""


class LLMRateLimitError(LLMError):
    """LLM API rate limit exceeded."""


# =============================================================================
# Diff Parsing
# =============================================================================


class DiffParseError(Exception):
    """Failed to parse a unified diff into structured data."""
