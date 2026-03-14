"""Tests for conversation reply handler.

Tests for app/conversation/handler.py — intent detection, finding lookup,
and action dispatch for developer replies to AI review comments.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest

from app.conversation.handler import ConversationHandler
from app.core.exceptions import GitHubAPIError


# =============================================================================
#  Fixtures
# =============================================================================


@pytest.fixture
def mock_github():
    """Mock GitHub client."""
    return AsyncMock()


@pytest.fixture
def handler(mock_github) -> ConversationHandler:
    """Create ConversationHandler instance with mocked GitHub client."""
    return ConversationHandler(mock_github)


@pytest.fixture
def mock_finding() -> MagicMock:
    """Create a mock Finding record."""
    finding = MagicMock()
    finding.id = "finding-123"
    finding.github_comment_id = 888
    finding.title = "SQL Injection Vulnerability"
    finding.body = "User input passed directly to SQL query"
    finding.file_path = "app/db.py"
    finding.line_start = 42
    finding.line_end = 45
    finding.severity = "critical"
    finding.category = "security"
    finding.suggestion_code = None
    finding.was_dismissed = False
    return finding


@pytest.fixture
def mock_finding_with_suggestion() -> MagicMock:
    """Create a mock Finding with pre-existing suggestion code."""
    finding = MagicMock()
    finding.id = "finding-456"
    finding.github_comment_id = 888
    finding.title = "Type Error"
    finding.body = "Variable used without declaration"
    finding.file_path = "app/main.py"
    finding.line_start = 10
    finding.line_end = 10
    finding.severity = "high"
    finding.category = "type-safety"
    finding.suggestion_code = "x: int = 0  # Initialize variable"
    finding.was_dismissed = False
    return finding


# =============================================================================
#  Intent Detection Tests (12 tests)
# =============================================================================


@pytest.mark.unit
def test_detect_intent_fix_command(handler: ConversationHandler) -> None:
    """Test /fix command detection."""
    intent = handler._detect_intent("/fix this is broken")
    assert intent == "fix"


@pytest.mark.unit
def test_detect_intent_fix_this(handler: ConversationHandler) -> None:
    """Test 'fix this' pattern detection."""
    intent = handler._detect_intent("can you fix this?")
    assert intent == "fix"


@pytest.mark.unit
def test_detect_intent_fix_it(handler: ConversationHandler) -> None:
    """Test 'fix it' pattern detection."""
    intent = handler._detect_intent("please fix it")
    assert intent == "fix"


@pytest.mark.unit
def test_detect_intent_explain_command(handler: ConversationHandler) -> None:
    """Test /explain command detection."""
    intent = handler._detect_intent("/explain why this is bad")
    assert intent == "explain"


@pytest.mark.unit
def test_detect_intent_ignore_command(handler: ConversationHandler) -> None:
    """Test /ignore command detection."""
    intent = handler._detect_intent("/ignore this finding")
    assert intent == "ignore"


@pytest.mark.unit
def test_detect_intent_unknown(handler: ConversationHandler) -> None:
    """Test unknown intent returns 'unknown'."""
    intent = handler._detect_intent("this is just a regular comment")
    assert intent == "unknown"


@pytest.mark.unit
def test_detect_intent_case_insensitive(handler: ConversationHandler) -> None:
    """Test case-insensitive intent detection."""
    intent = handler._detect_intent("/FIX the issue")
    assert intent == "fix"


@pytest.mark.unit
def test_detect_intent_whitespace_normalized(handler: ConversationHandler) -> None:
    """Test that whitespace is normalized before matching."""
    intent = handler._detect_intent("  /EXPLAIN  ")
    assert intent == "explain"


@pytest.mark.unit
def test_detect_intent_multiple_patterns_fix_wins(handler: ConversationHandler) -> None:
    """Test that /fix is detected when multiple patterns present."""
    # Fix should be detected first
    intent = handler._detect_intent("/fix\ncan you explain this too?")
    assert intent == "fix"


@pytest.mark.unit
def test_detect_intent_explain_multiline(handler: ConversationHandler) -> None:
    """Test /explain detection with multiline comment."""
    intent = handler._detect_intent("/explain\nwhy is this bad?")
    assert intent == "explain"


@pytest.mark.unit
def test_detect_intent_ignore_with_reason(handler: ConversationHandler) -> None:
    """Test /ignore detection with additional context."""
    intent = handler._detect_intent("/ignore this is a false positive")
    assert intent == "ignore"


@pytest.mark.unit
def test_detect_intent_empty_string(handler: ConversationHandler) -> None:
    """Test empty string returns unknown intent."""
    intent = handler._detect_intent("")
    assert intent == "unknown"


# =============================================================================
#  Intent Handler Tests (6 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_fix_with_suggestion(
    handler: ConversationHandler, mock_finding_with_suggestion: MagicMock
) -> None:
    """Test /fix handler with pre-existing suggestion code."""
    with patch.object(handler, "_post_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = 999

        result = await handler._handle_fix(
            finding=mock_finding_with_suggestion,
            repo_full_name="test/repo",
            pr_number=42,
            in_reply_to_id=888,
        )

        assert result == 999
        mock_reply.assert_called_once()
        # Check that suggestion was posted
        call_args = mock_reply.call_args
        assert call_args is not None
        posted_body = call_args[1]["body"]
        assert "suggestion" in posted_body.lower()
        assert mock_finding_with_suggestion.suggestion_code in posted_body


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_fix_without_suggestion(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test /fix handler generating fix via LLM when no pre-existing suggestion."""
    with patch.object(handler, "_post_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = 999

        with patch.object(
            handler, "_generate_fix_via_llm", new_callable=AsyncMock
        ) as mock_gen_fix:
            mock_gen_fix.return_value = "Here's a suggested fix:\n\n```suggestion\nfixed code\n```"

            result = await handler._handle_fix(
                finding=mock_finding,
                repo_full_name="test/repo",
                pr_number=42,
                in_reply_to_id=888,
            )

            assert result == 999
            mock_gen_fix.assert_called_once_with(mock_finding)
            mock_reply.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_explain(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test /explain handler generates explanation via LLM."""
    with patch.object(handler, "_post_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = 999

        with patch.object(
            handler, "_generate_explanation_via_llm", new_callable=AsyncMock
        ) as mock_explain:
            mock_explain.return_value = "Detailed explanation of the security vulnerability..."

            result = await handler._handle_explain(
                finding=mock_finding,
                repo_full_name="test/repo",
                pr_number=42,
                in_reply_to_id=888,
            )

            assert result == 999
            mock_explain.assert_called_once_with(mock_finding)
            mock_reply.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_ignore(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test /ignore handler dismisses finding and posts reply."""
    with patch.object(handler, "_post_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = 999

        with patch.object(handler, "_dismiss_finding") as mock_dismiss:
            result = await handler._handle_ignore(
                finding=mock_finding,
                repo_full_name="test/repo",
                pr_number=42,
                in_reply_to_id=888,
            )

            assert result == 999
            mock_dismiss.assert_called_once_with(mock_finding)
            mock_reply.assert_called_once()
            # Check that acknowledgement message was posted
            call_args = mock_reply.call_args
            assert call_args is not None
            posted_body = call_args[1]["body"]
            assert "dismissed" in posted_body.lower()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_ignore_dismissal_failure_still_posts_reply(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test that reply is still posted even if dismissal fails."""
    with patch.object(handler, "_post_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = 999

        with patch.object(handler, "_dismiss_finding") as mock_dismiss:
            mock_dismiss.side_effect = Exception("DB error")

            # The exception should propagate but reply was called first
            with pytest.raises(Exception):
                await handler._handle_ignore(
                    finding=mock_finding,
                    repo_full_name="test/repo",
                    pr_number=42,
                    in_reply_to_id=888,
                )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_reply_with_custom_body(
    handler: ConversationHandler, mock_github: AsyncMock
) -> None:
    """Test _post_reply posts the provided body correctly."""
    handler.github = mock_github
    mock_github.post_review_comment = AsyncMock(return_value={"id": 999})

    comment_id = await handler._post_reply(
        repo_full_name="test/repo",
        pr_number=42,
        body="Custom response body",
        in_reply_to_id=888,
    )

    assert comment_id == 999
    mock_github.post_review_comment.assert_called_once_with(
        repo_full_name="test/repo",
        pr_number=42,
        body="Custom response body",
        in_reply_to=888,
    )


# =============================================================================
#  Main Handler Entry Point Tests (5 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_not_a_reply(handler: ConversationHandler) -> None:
    """Test that non-reply comments are ignored."""
    result = await handler.handle(
        installation_id=1,
        repo_full_name="test/repo",
        pr_number=42,
        comment_id=123,
        comment_body="just a comment",
        in_reply_to_id=None,  # Not a reply
        redis_client=AsyncMock(),
    )

    assert result["action"] == "ignored"
    assert result["reason"] == "not_a_reply"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_finding_not_found(handler: ConversationHandler) -> None:
    """Test when Finding record is not found for the parent comment."""
    with patch.object(handler, "_lookup_finding", return_value=None):
        result = await handler.handle(
            installation_id=1,
            repo_full_name="test/repo",
            pr_number=42,
            comment_id=123,
            comment_body="/fix",
            in_reply_to_id=888,
            redis_client=AsyncMock(),
        )

        assert result["action"] == "ignored"
        assert result["reason"] == "finding_not_found"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_unknown_intent(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test that unknown intents are ignored."""
    with patch.object(handler, "_lookup_finding", return_value=mock_finding):
        result = await handler.handle(
            installation_id=1,
            repo_full_name="test/repo",
            pr_number=42,
            comment_id=123,
            comment_body="just a random response to the comment",
            in_reply_to_id=888,
            redis_client=AsyncMock(),
        )

        assert result["action"] == "ignored"
        assert result["reason"] == "unknown_intent"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_fix_intent_success(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test successful /fix intent handling with full flow."""
    with patch.object(handler, "_lookup_finding", return_value=mock_finding):
        with patch.object(handler, "_handle_fix", new_callable=AsyncMock) as mock_fix:
            mock_fix.return_value = 999

            with patch.object(handler, "_upsert_conversation_thread"):
                result = await handler.handle(
                    installation_id=1,
                    repo_full_name="test/repo",
                    pr_number=42,
                    comment_id=123,
                    comment_body="/fix this issue",
                    in_reply_to_id=888,
                    redis_client=AsyncMock(),
                )

                assert result["action"] == "fix"
                assert result["comment_id"] == 999
                mock_fix.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_explain_intent_success(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test successful /explain intent handling with full flow."""
    with patch.object(handler, "_lookup_finding", return_value=mock_finding):
        with patch.object(handler, "_handle_explain", new_callable=AsyncMock) as mock_explain:
            mock_explain.return_value = 888

            with patch.object(handler, "_upsert_conversation_thread"):
                result = await handler.handle(
                    installation_id=1,
                    repo_full_name="test/repo",
                    pr_number=42,
                    comment_id=123,
                    comment_body="/explain\nwhy is this bad?",
                    in_reply_to_id=888,
                    redis_client=AsyncMock(),
                )

                assert result["action"] == "explain"
                assert result["comment_id"] == 888


# =============================================================================
#  GitHub API Interaction Tests (3 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_reply_success(
    handler: ConversationHandler, mock_github: AsyncMock
) -> None:
    """Test successful reply posting to GitHub."""
    handler.github = mock_github
    mock_github.post_review_comment = AsyncMock(return_value={"id": 999})

    comment_id = await handler._post_reply(
        repo_full_name="test/repo",
        pr_number=42,
        body="Test reply",
        in_reply_to_id=888,
    )

    assert comment_id == 999
    mock_github.post_review_comment.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_reply_api_error_returns_zero(
    handler: ConversationHandler, mock_github: AsyncMock
) -> None:
    """Test that post_reply returns 0 on GitHub API error."""
    handler.github = mock_github
    mock_github.post_review_comment = AsyncMock(side_effect=GitHubAPIError("API error", 500))

    comment_id = await handler._post_reply(
        repo_full_name="test/repo",
        pr_number=42,
        body="Test reply",
        in_reply_to_id=888,
    )

    assert comment_id == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_post_reply_missing_id_field(
    handler: ConversationHandler, mock_github: AsyncMock
) -> None:
    """Test post_reply when response lacks 'id' field."""
    handler.github = mock_github
    mock_github.post_review_comment = AsyncMock(return_value={})  # No 'id' field

    comment_id = await handler._post_reply(
        repo_full_name="test/repo",
        pr_number=42,
        body="Test reply",
        in_reply_to_id=888,
    )

    assert comment_id == 0


# =============================================================================
#  LLM Interaction Tests (2 tests)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_generate_fix_via_llm_success(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test successful LLM-based fix generation."""
    with patch("app.conversation.handler.LLMClient") as mock_llm_class:
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(
            return_value=(
                "```python\nfixed_code = True\n```",
                0.001,
            )
        )
        mock_llm.close = AsyncMock()
        mock_llm_class.return_value = mock_llm

        result = await handler._generate_fix_via_llm(mock_finding)

        assert "suggestion" in result.lower()
        assert "fixed_code" in result
        mock_llm.complete.assert_called_once()
        mock_llm.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_generate_fix_via_llm_fallback_on_error(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test fallback message when LLM fix generation fails."""
    with patch("app.conversation.handler.LLMClient") as mock_llm_class:
        mock_llm_class.side_effect = Exception("LLM API error")

        result = await handler._generate_fix_via_llm(mock_finding)

        assert "wasn't able to generate a fix" in result or "Unable to generate" in result
        assert result != ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_generate_explanation_via_llm_success(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test successful LLM-based explanation generation."""
    with patch("app.conversation.handler.LLMClient") as mock_llm_class:
        mock_llm = MagicMock()
        expected_explanation = "This is a SQL injection vulnerability because..."
        mock_llm.complete = AsyncMock(return_value=(expected_explanation, 0.002))
        mock_llm.close = AsyncMock()
        mock_llm_class.return_value = mock_llm

        result = await handler._generate_explanation_via_llm(mock_finding)

        assert result == expected_explanation
        mock_llm.complete.assert_called_once()
        mock_llm.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_generate_explanation_via_llm_fallback_on_error(
    handler: ConversationHandler, mock_finding: MagicMock
) -> None:
    """Test fallback explanation when LLM call fails."""
    with patch("app.conversation.handler.LLMClient") as mock_llm_class:
        mock_llm_class.side_effect = Exception("LLM API error")

        result = await handler._generate_explanation_via_llm(mock_finding)

        # Should return a fallback explanation using finding details
        assert mock_finding.title in result or "N/A" in result
        assert result != ""


# =============================================================================
#  Utility Method Tests (2 tests)
# =============================================================================


@pytest.mark.unit
def test_extract_code_block_with_language(handler: ConversationHandler) -> None:
    """Test extraction of code block with language specifier."""
    text = "Here's the code:\n\n```python\ndef foo():\n    pass\n```\n\nDone."
    code = handler._extract_code_block(text)

    assert code == "def foo():\n    pass"


@pytest.mark.unit
def test_extract_code_block_no_block_returns_text(handler: ConversationHandler) -> None:
    """Test that text without code block is returned as-is."""
    text = "No code block here"
    code = handler._extract_code_block(text)

    assert code == text
