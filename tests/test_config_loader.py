"""Tests for the config loader — .openrabbit.yaml parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config_loader import (
    ReviewConfig,
    load_review_config,
    should_ignore_file,
    _parse_config,
)


class TestParseConfig:
    """Tests for YAML config parsing."""

    def test_valid_config(self):
        yaml_content = """\
review:
  enabled: true
  custom_guidelines: |
    - Use double quotes
    - Handle errors
  ignore_patterns:
    - 'tests/**'
    - '*.generated.ts'
  severity_threshold: medium
  style: false
"""
        config = _parse_config(yaml_content)
        assert config.enabled is True
        assert "double quotes" in config.custom_guidelines
        assert "tests/**" in config.ignore_patterns
        assert config.severity_threshold == "medium"
        assert config.style_review is False

    def test_empty_config(self):
        config = _parse_config("")
        assert config.enabled is True  # defaults

    def test_invalid_yaml(self):
        config = _parse_config("{{invalid yaml::")
        assert config.enabled is True  # defaults

    def test_missing_review_key(self):
        config = _parse_config("other_key: value")
        assert config.enabled is True  # defaults

    def test_disabled_review(self):
        config = _parse_config("review:\n  enabled: false")
        assert config.enabled is False


class TestShouldIgnoreFile:
    """Tests for the file ignore pattern matching."""

    def test_matches_pattern(self):
        config = ReviewConfig(ignore_patterns=["tests/**", "*.generated.ts"])
        assert should_ignore_file("tests/test_main.py", config) is True

    def test_no_match(self):
        config = ReviewConfig(ignore_patterns=["tests/**"])
        assert should_ignore_file("app/main.py", config) is False

    def test_empty_patterns(self):
        config = ReviewConfig(ignore_patterns=[])
        assert should_ignore_file("anything.py", config) is False


class TestLoadReviewConfig:
    """Tests for loading config from GitHub."""

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """Returns defaults when .openrabbit.yaml doesn't exist."""
        mock_github = MagicMock()
        mock_github.get_file_content = AsyncMock(side_effect=Exception("404"))

        config = await load_review_config(mock_github, "test/repo", "abc123")
        assert config.enabled is True
        assert config.custom_guidelines == ""

    @pytest.mark.asyncio
    async def test_file_found(self):
        """Parses config when file exists."""
        yaml_content = "review:\n  severity_threshold: high\n  style: false"
        mock_github = MagicMock()
        mock_github.get_file_content = AsyncMock(return_value=yaml_content)

        config = await load_review_config(mock_github, "test/repo", "abc123")
        assert config.severity_threshold == "high"
        assert config.style_review is False
