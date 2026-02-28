"""LLM client wrapping the Anthropic SDK.

Implements ADR-0014: retry logic, cost tracking, JSON parsing with fallback.

Usage:
    client = LLMClient()
    text, cost = await client.complete("Summarize this diff", ...)
    data, cost = await client.complete_with_json("Return JSON findings", ...)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import anthropic

from app.config import get_settings
from app.core.exceptions import LLMError, LLMParseError, LLMRateLimitError

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (input / output) — updated for current models.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-5-20251001": (3.00, 15.00),
    # Fallback / aliases
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}

# Defaults
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
REQUEST_TIMEOUT = 120.0

# Retry constants per ADR-0014
RATE_LIMIT_WAIT_SECONDS = 60
SERVER_ERROR_BACKOFF = [5, 15, 45]
MAX_RETRIES = 3


class LLMClient:
    """Async Anthropic LLM client with retry logic and cost tracking."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=REQUEST_TIMEOUT,
        )

    async def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> tuple[str, float]:
        """Send a prompt to Claude and return (response_text, cost_usd).

        Retries on rate limits (429) and server errors (5xx) per ADR-0014.
        """
        start_time = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system if system else anthropic.NOT_GIVEN,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Extract text from response
                text = ""
                for block in response.content:
                    if block.type == "text":
                        text += block.text

                # Calculate cost
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cost_usd = self._calculate_cost(model, input_tokens, output_tokens)

                duration_ms = int((time.monotonic() - start_time) * 1000)
                logger.info(
                    "LLM call completed",
                    extra={
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": f"{cost_usd:.6f}",
                        "duration_ms": duration_ms,
                        "attempt": attempt + 1,
                    },
                )

                return text, cost_usd

            except anthropic.RateLimitError as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "LLM rate limit hit — waiting %ds before retry",
                        RATE_LIMIT_WAIT_SECONDS,
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
                else:
                    raise LLMRateLimitError(
                        f"Rate limit exceeded after {MAX_RETRIES + 1} attempts"
                    ) from e

            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code >= 500 and attempt < MAX_RETRIES:
                    wait = SERVER_ERROR_BACKOFF[min(attempt, len(SERVER_ERROR_BACKOFF) - 1)]
                    logger.warning(
                        "LLM server error %d — retrying in %ds",
                        e.status_code,
                        wait,
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(wait)
                else:
                    raise LLMError(
                        f"Anthropic API error: {e.status_code} — {e.message}"
                    ) from e

            except anthropic.APIConnectionError as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = SERVER_ERROR_BACKOFF[min(attempt, len(SERVER_ERROR_BACKOFF) - 1)]
                    logger.warning(
                        "LLM connection error — retrying in %ds",
                        wait,
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(wait)
                else:
                    raise LLMError(f"Connection failed after {MAX_RETRIES + 1} attempts") from e

        # Should not reach here, but satisfy the type checker.
        raise LLMError("Unexpected retry loop exit") from last_error

    async def complete_with_json(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> tuple[dict[str, Any] | list[Any], float]:
        """Send a prompt and parse the response as JSON.

        If the first attempt returns invalid JSON, retries once with
        'Return ONLY valid JSON' appended to the prompt.
        """
        total_cost = 0.0

        for attempt in range(2):
            effective_prompt = prompt
            if attempt == 1:
                effective_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Return ONLY valid JSON. "
                    "No markdown, no explanation, no code fences — raw JSON only."
                )

            text, cost = await self.complete(
                effective_prompt,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            total_cost += cost

            parsed = self._try_parse_json(text)
            if parsed is not None:
                return parsed, total_cost

            if attempt == 0:
                logger.warning(
                    "LLM returned invalid JSON — retrying with strict instruction",
                    extra={"response_preview": text[:200]},
                )

        raise LLMParseError(f"Failed to parse JSON after 2 attempts. Last response: {text[:300]}")

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate USD cost from token usage."""
        input_price, output_price = MODEL_PRICING.get(model, (3.00, 15.00))
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | list[Any] | None:
        """Attempt to parse JSON from LLM output, stripping markdown fences."""
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        cleaned = text.strip()
        fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
        match = fence_pattern.match(cleaned)
        if match:
            cleaned = match.group(1).strip()

        try:
            result = json.loads(cleaned)
            if isinstance(result, (dict, list)):
                return result
            return None
        except (json.JSONDecodeError, ValueError):
            return None
