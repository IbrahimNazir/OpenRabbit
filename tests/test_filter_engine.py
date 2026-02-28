"""Tests for the FilterEngine (Gatekeeper) per ADR-0011.

Covers:
- Bot PR detection
- Custom [bot] suffix detection
- skip-ai-review label
- Draft PR filtering
- Documentation-only PRs
- Lockfile-only PRs
- Large PR → slow_lane routing
- Normal PR → fast_lane routing
- get_reviewable_files() filtering
"""

from __future__ import annotations

import pytest

from app.core.filter_engine import FilterEngine, FilterResult


@pytest.fixture()
def engine() -> FilterEngine:
    """Create a FilterEngine instance."""
    return FilterEngine()


def _make_payload(
    author: str = "developer",
    labels: list[str] | None = None,
    draft: bool = False,
) -> dict:
    """Build a minimal PR webhook payload for filter testing."""
    return {
        "pull_request": {
            "user": {"login": author},
            "labels": [{"name": lbl} for lbl in (labels or [])],
            "draft": draft,
        },
    }


# =============================================================================
#  Bot Detection
# =============================================================================


class TestBotDetection:
    """Test that known bot accounts are filtered out."""

    @pytest.mark.parametrize(
        "bot_login",
        [
            "dependabot[bot]",
            "renovate[bot]",
            "snyk-bot",
            "github-actions[bot]",
            "imgbot[bot]",
        ],
    )
    def test_known_bots_skipped(self, engine: FilterEngine, bot_login: str) -> None:
        payload = _make_payload(author=bot_login)
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is False
        assert result.queue == "skip"
        assert bot_login in result.reason or "Bot" in result.reason

    def test_custom_bot_suffix_skipped(self, engine: FilterEngine) -> None:
        """Any author ending in [bot] should be skipped."""
        payload = _make_payload(author="my-custom-ci[bot]")
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is False
        assert result.queue == "skip"

    def test_normal_author_not_skipped(self, engine: FilterEngine) -> None:
        """Sanity check: a normal author passes the bot filter."""
        payload = _make_payload(author="john-developer")
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is True


# =============================================================================
#  Label Override
# =============================================================================


class TestLabelOverride:
    """Test skip-ai-review label."""

    def test_skip_label_present(self, engine: FilterEngine) -> None:
        payload = _make_payload(labels=["skip-ai-review"])
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is False
        assert result.queue == "skip"
        assert "skip-ai-review" in result.reason

    def test_other_labels_ignored(self, engine: FilterEngine) -> None:
        payload = _make_payload(labels=["bug", "enhancement"])
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is True


# =============================================================================
#  Draft PR
# =============================================================================


class TestDraftPR:
    """Test draft PR filtering."""

    def test_draft_pr_skipped(self, engine: FilterEngine) -> None:
        payload = _make_payload(draft=True)
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is False
        assert result.queue == "skip"
        assert "Draft" in result.reason

    def test_non_draft_pr_not_skipped(self, engine: FilterEngine) -> None:
        payload = _make_payload(draft=False)
        result = engine.should_review(payload, changed_files=["app/main.py"])
        assert result.should_process is True


# =============================================================================
#  File-Based Filtering
# =============================================================================


class TestFilePatterns:
    """Test filtering based on changed file patterns."""

    def test_docs_only_skipped(self, engine: FilterEngine) -> None:
        """PR with only .md files should be skipped."""
        payload = _make_payload()
        result = engine.should_review(
            payload, changed_files=["README.md", "docs/guide.md", "CHANGELOG.md"]
        )
        assert result.should_process is False
        assert "no-review patterns" in result.reason

    def test_lockfile_only_skipped(self, engine: FilterEngine) -> None:
        """PR with only lockfiles should be skipped."""
        payload = _make_payload()
        result = engine.should_review(
            payload, changed_files=["package-lock.json"]
        )
        assert result.should_process is False

    def test_images_only_skipped(self, engine: FilterEngine) -> None:
        """PR with only image files should be skipped."""
        payload = _make_payload()
        result = engine.should_review(
            payload, changed_files=["logo.png", "banner.svg"]
        )
        assert result.should_process is False

    def test_mixed_files_not_skipped(self, engine: FilterEngine) -> None:
        """PR with code + docs should still be reviewed."""
        payload = _make_payload()
        result = engine.should_review(
            payload, changed_files=["README.md", "app/main.py"]
        )
        assert result.should_process is True

    def test_code_only_reviewed(self, engine: FilterEngine) -> None:
        """PR with only code files → fast lane."""
        payload = _make_payload()
        result = engine.should_review(
            payload, changed_files=["app/main.py", "app/config.py"]
        )
        assert result.should_process is True
        assert result.queue == "fast_lane"


# =============================================================================
#  Large PR Routing
# =============================================================================


class TestLargePR:
    """Test large PR → slow_lane routing."""

    def test_large_pr_slow_lane(self, engine: FilterEngine) -> None:
        """PR with >50 files → slow_lane."""
        payload = _make_payload()
        files = [f"app/file_{i}.py" for i in range(55)]
        result = engine.should_review(payload, changed_files=files)
        assert result.should_process is True
        assert result.queue == "slow_lane"
        assert "slow lane" in result.reason

    def test_normal_pr_fast_lane(self, engine: FilterEngine) -> None:
        """PR with <50 files → fast_lane."""
        payload = _make_payload()
        files = [f"app/file_{i}.py" for i in range(10)]
        result = engine.should_review(payload, changed_files=files)
        assert result.should_process is True
        assert result.queue == "fast_lane"


# =============================================================================
#  get_reviewable_files()
# =============================================================================


class TestGetReviewableFiles:
    """Test the file filtering utility."""

    def test_filters_markdown(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["app/main.py", "README.md", "docs/guide.rst"]
        )
        assert result == ["app/main.py"]

    def test_filters_lockfiles(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["app/main.py", "package-lock.json", "yarn.lock", "poetry.lock"]
        )
        assert result == ["app/main.py"]

    def test_filters_images(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["app/main.py", "logo.png", "icon.svg"]
        )
        assert result == ["app/main.py"]

    def test_filters_vendor_dirs(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["app/main.py", "vendor/lib/util.js", "node_modules/pkg/index.js"]
        )
        assert result == ["app/main.py"]

    def test_filters_build_artifacts(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["app/main.py", "dist/bundle.min.js", "static/app.min.css"]
        )
        assert result == ["app/main.py"]

    def test_empty_list(self) -> None:
        result = FilterEngine.get_reviewable_files([])
        assert result == []

    def test_all_filtered(self) -> None:
        result = FilterEngine.get_reviewable_files(
            ["README.md", "package-lock.json", "logo.png"]
        )
        assert result == []

    def test_preserves_code_files(self) -> None:
        code_files = ["app/main.py", "src/index.ts", "lib/utils.go", "handler.rs"]
        result = FilterEngine.get_reviewable_files(code_files)
        assert result == code_files
