"""Pre-LLM Gatekeeper Filter Engine.

Implements ADR-0011: a cheap, deterministic, rule-based filter that decides
whether a webhook event should proceed to the review pipeline and, if so,
which queue to use (fast_lane vs. slow_lane).

Applied *before* any database writes or task enqueueing — in the API
gateway itself.  ~40-65% of events are filtered out here, saving
significant LLM API cost.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


# =============================================================================
#  Constants
# =============================================================================

BOT_LOGINS: frozenset[str] = frozenset(
    {
        "dependabot[bot]",
        "dependabot-preview[bot]",
        "renovate[bot]",
        "snyk-bot",
        "github-actions[bot]",
        "imgbot[bot]",
        "whitesource-bolt-for-github[bot]",
        "semantic-release-bot",
        "allcontributors[bot]",
    }
)

NO_REVIEW_PATTERNS: tuple[str, ...] = (
    # Documentation
    "*.md",
    "*.rst",
    "*.txt",
    "*.adoc",
    "*.wiki",
    # Images and media
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.webp",
    # Lockfiles (generated, never hand-written)
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "*.lock",
    "*.sum",
    "Cargo.lock",
    "poetry.lock",
    "Gemfile.lock",
    "composer.lock",
    "packages.lock.json",
    # Build artifacts
    "*.min.js",
    "*.min.css",
    "*.map",
    # IDE and config
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    "*.iml",
)

LARGE_PR_THRESHOLD: int = 50


# =============================================================================
#  Result data class
# =============================================================================


@dataclass
class FilterResult:
    """Result of the gatekeeper filter evaluation."""

    should_process: bool
    reason: str
    queue: Literal["fast_lane", "slow_lane", "skip"]


# =============================================================================
#  Filter Engine
# =============================================================================


class FilterEngine:
    """Evaluates webhook payloads against a set of ordered filter rules.

    Rules are applied in order — first match wins:
    1. Bot author → skip
    2. ``skip-ai-review`` label → skip
    3. Draft PR → skip
    4. All files match no-review patterns → skip
    5. Large PR (>50 files) → slow_lane
    6. Otherwise → fast_lane
    """

    def should_review(
        self,
        payload: dict,
        changed_files: list[str] | None = None,
    ) -> FilterResult:
        """Determine whether a PR webhook should be reviewed.

        Args:
            payload: The raw GitHub webhook JSON payload.
            changed_files: List of changed file paths (from the diff or API).
                           If None, file-based filters are skipped.

        Returns:
            A ``FilterResult`` indicating the decision.
        """
        pr = payload.get("pull_request", {})

        # --- Rule 1: Bot author ---
        author: str = pr.get("user", {}).get("login", "")
        if author in BOT_LOGINS or author.endswith("[bot]"):
            result = FilterResult(False, f"Bot PR from {author}", "skip")
            logger.info("Filter: %s", result.reason, extra={"author": author})
            return result

        # --- Rule 2: Label override ---
        labels: list[str] = [label["name"] for label in pr.get("labels", [])]
        if "skip-ai-review" in labels:
            result = FilterResult(False, "skip-ai-review label present", "skip")
            logger.info("Filter: %s", result.reason)
            return result

        # --- Rule 3: Draft PR ---
        if pr.get("draft", False):
            result = FilterResult(False, "Draft PR — awaiting ready-for-review", "skip")
            logger.info("Filter: %s", result.reason)
            return result

        # --- Rule 4: All files match no-review patterns ---
        if changed_files is not None:
            reviewable = self.get_reviewable_files(changed_files)
            if not reviewable:
                result = FilterResult(
                    False,
                    f"All {len(changed_files)} files match no-review patterns",
                    "skip",
                )
                logger.info("Filter: %s", result.reason)
                return result

            # --- Rule 5: Large PR → slow lane ---
            if len(changed_files) > LARGE_PR_THRESHOLD:
                result = FilterResult(
                    True,
                    f"Large PR: {len(changed_files)} files → slow lane",
                    "slow_lane",
                )
                logger.info("Filter: %s", result.reason)
                return result

            # Default: proceed with fast lane.
            result = FilterResult(
                True,
                f"Reviewable PR: {len(reviewable)} code files",
                "fast_lane",
            )
            logger.info("Filter: %s", result.reason)
            return result

        # No file list provided — default to fast lane.
        return FilterResult(True, "No file list — defaulting to fast_lane", "fast_lane")

    @staticmethod
    def get_reviewable_files(changed_files: list[str]) -> list[str]:
        """Filter out files that should not be reviewed.

        Removes: documentation, images, lockfiles, build artifacts,
        IDE config, vendor directories, and generated files.

        Args:
            changed_files: List of file paths from the diff.

        Returns:
            List of file paths that should be reviewed.
        """
        reviewable: list[str] = []

        for filepath in changed_files:
            filename = filepath.rsplit("/", maxsplit=1)[-1]  # basename

            # Check against no-review glob patterns.
            skip = False
            for pattern in NO_REVIEW_PATTERNS:
                if fnmatch.fnmatch(filename, pattern):
                    skip = True
                    break

            # Also skip vendor / generated / hidden directories.
            if not skip:
                parts = filepath.split("/")
                vendor_dirs = {"vendor", "node_modules", ".git", "__pycache__", "dist", "build"}
                if any(part in vendor_dirs for part in parts):
                    skip = True

            if not skip:
                reviewable.append(filepath)

        return reviewable
