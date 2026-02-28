"""Repository-level configuration loader.

Fetches and parses ``.openrabbit.yaml`` from a repository's root to
customize review behavior per-repo.  Falls back to sensible defaults
if the file does not exist.

Example ``.openrabbit.yaml``::

    review:
      enabled: true
      language_rules:
        python: true
        javascript: true
      custom_guidelines: |
        - We use double quotes for strings
        - All async functions must have error handling
      ignore_patterns:
        - 'tests/**'
        - '*.generated.ts'
      severity_threshold: medium
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field

import yaml

from app.core.github_client import GitHubClient

logger = logging.getLogger(__name__)


@dataclass
class ReviewConfig:
    """Parsed review configuration from ``.openrabbit.yaml``."""

    enabled: bool = True
    custom_guidelines: str = ""
    ignore_patterns: list[str] = field(default_factory=list)
    severity_threshold: str = "low"  # low | medium | high | critical
    style_review: bool = True
    language_rules: dict[str, bool] = field(default_factory=dict)


async def load_review_config(
    github_client: GitHubClient,
    repo_full_name: str,
    ref: str,
) -> ReviewConfig:
    """Fetch and parse the repo's ``.openrabbit.yaml`` configuration.

    Args:
        github_client: Authenticated GitHub API client.
        repo_full_name: ``owner/repo`` format.
        ref: Git ref (SHA or branch) to fetch the config at.

    Returns:
        A ``ReviewConfig`` with values from the file or defaults.
    """
    try:
        content = await github_client.get_file_content(
            repo_full_name, ".openrabbit.yaml", ref
        )
        return _parse_config(content)
    except Exception:
        # File doesn't exist or can't be fetched — use defaults
        logger.debug(
            "No .openrabbit.yaml found — using defaults",
            extra={"repo": repo_full_name, "ref": ref[:8]},
        )
        return ReviewConfig()


def _parse_config(content: str) -> ReviewConfig:
    """Parse YAML content into a ReviewConfig dataclass."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        logger.warning("Invalid .openrabbit.yaml — using defaults")
        return ReviewConfig()

    if not isinstance(data, dict):
        return ReviewConfig()

    review = data.get("review", {})
    if not isinstance(review, dict):
        return ReviewConfig()

    return ReviewConfig(
        enabled=review.get("enabled", True),
        custom_guidelines=review.get("custom_guidelines", ""),
        ignore_patterns=review.get("ignore_patterns", []),
        severity_threshold=review.get("severity_threshold", "low"),
        style_review=review.get("style", True),
        language_rules=review.get("language_rules", {}),
    )


def should_ignore_file(file_path: str, config: ReviewConfig) -> bool:
    """Check if a file should be ignored based on config ignore_patterns.

    Args:
        file_path: The file path to check.
        config: The review configuration.

    Returns:
        True if the file matches any ignore pattern.
    """
    for pattern in config.ignore_patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False
