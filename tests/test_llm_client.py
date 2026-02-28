"""Tests for the LLM client — retry logic, cost tracking, JSON parsing."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.client import LLMClient


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings():
    """Provide mock settings with a test API key."""
    with patch("app.llm.client.get_settings") as mock:
        settings = MagicMock()
        settings.deepseek_api_key = "test-key-123"
        mock.return_value = settings
        yield settings


@pytest.fixture
def llm_client(mock_settings):
    """Create an LLMClient with mocked settings."""
    return LLMClient()


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _make_response(text: str, input_tokens: int = 100, output_tokens: int = 50):
    """Create a mock OpenAI response."""
    message = MagicMock()
    message.content = text

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock()
    response.usage.prompt_tokens = input_tokens
    response.usage.completion_tokens = output_tokens
    return response


# ---------------------------------------------------------------------------
#  Tests: complete()
# ---------------------------------------------------------------------------


class TestComplete:
    """Tests for the basic complete() method."""

    @pytest.mark.asyncio
    async def test_successful_completion(self, llm_client):
        """LLM call returns text and cost."""
        response = _make_response("Hello, world!", input_tokens=100, output_tokens=50)
        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(return_value=response)

        text, cost = await llm_client.complete("What is Python?")

        assert text == "Hello, world!"
        assert cost > 0
        llm_client._client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_cost_calculation(self, llm_client):
        """Cost is calculated correctly for DeepSeek chat model."""
        response = _make_response("result", input_tokens=1000, output_tokens=500)
        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(return_value=response)

        _, cost = await llm_client.complete("test", model="deepseek-chat")

        # DeepSeek Chat: $0.14/1M input, $0.28/1M output
        expected = (1000 * 0.14 + 500 * 0.28) / 1_000_000
        assert abs(cost - expected) < 1e-8


# ---------------------------------------------------------------------------
#  Tests: complete_with_json()
# ---------------------------------------------------------------------------


class TestCompleteWithJson:
    """Tests for JSON-parsing completion."""

    @pytest.mark.asyncio
    async def test_parses_clean_json(self, llm_client):
        """Parses clean JSON response."""
        json_str = json.dumps({"summary": "test", "risk_level": "low"})
        response = _make_response(json_str)
        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(return_value=response)

        data, cost = await llm_client.complete_with_json("Summarize this")

        assert isinstance(data, dict)
        assert data["summary"] == "test"

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, llm_client):
        """Strips ```json ... ``` fences from LLM output before parsing."""
        json_str = '```json\n{"key": "value"}\n```'
        response = _make_response(json_str)
        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(return_value=response)

        data, cost = await llm_client.complete_with_json("Return JSON")

        assert isinstance(data, dict)
        assert data["key"] == "value"

    @pytest.mark.asyncio
    async def test_parses_json_array(self, llm_client):
        """Parses JSON array response."""
        json_str = json.dumps([{"line_start": 10, "severity": "high"}])
        response = _make_response(json_str)
        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(return_value=response)

        data, cost = await llm_client.complete_with_json("Find bugs")

        assert isinstance(data, list)
        assert data[0]["severity"] == "high"

    @pytest.mark.asyncio
    async def test_retry_on_invalid_json(self, llm_client):
        """Retries once with strict JSON instruction when first response is invalid."""
        bad_response = _make_response("Not valid JSON at all")
        good_response = _make_response('{"result": "ok"}')

        llm_client._client.chat = MagicMock()
        llm_client._client.chat.completions = MagicMock()
        llm_client._client.chat.completions.create = AsyncMock(
            side_effect=[bad_response, good_response]
        )

        data, cost = await llm_client.complete_with_json("Return JSON")

        assert data["result"] == "ok"
        assert llm_client._client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
#  Tests: JSON parsing edge cases
# ---------------------------------------------------------------------------


class TestJsonParsing:
    """Tests for the static JSON parsing helper."""

    def test_plain_json(self):
        assert LLMClient._try_parse_json('{"a": 1}') == {"a": 1}

    def test_json_with_fences(self):
        assert LLMClient._try_parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_plain_fences(self):
        assert LLMClient._try_parse_json('```\n[1, 2, 3]\n```') == [1, 2, 3]

    def test_invalid_json(self):
        assert LLMClient._try_parse_json("Not JSON") is None

    def test_empty_string(self):
        assert LLMClient._try_parse_json("") is None

    def test_json_number_not_dict_or_list(self):
        assert LLMClient._try_parse_json("42") is None
