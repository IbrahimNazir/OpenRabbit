"""LLM client wrapping the OpenAI SDK (configured for DeepSeek).

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

import openai

from app.config import get_settings
from app.core.exceptions import LLMError, LLMParseError, LLMRateLimitError

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (input / output)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
}

# Defaults
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
REQUEST_TIMEOUT = 120.0

# Retry constants per ADR-0014
RATE_LIMIT_WAIT_SECONDS = 60
SERVER_ERROR_BACKOFF = [5, 15, 45]
MAX_RETRIES = 3


class LLMClient:
    """Async OpenAI LLM client with retry logic and cost tracking."""

    def __init__(self) -> None:
        settings = get_settings()
        print('credemtials: ',settings.deepseek_api_key)
        self._client = openai.AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
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
        """Send a prompt to DeepSeek and return (response_text, cost_usd).

        Retries on rate limits (429) and server errors (5xx) per ADR-0014.
        """
        start_time = time.monotonic()
        last_error: Exception | None = None

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                )

                # Extract text from response
                text = response.choices[0].message.content or ""

                # Calculate cost
                input_tokens = response.usage.prompt_tokens if response.usage else 0
                output_tokens = response.usage.completion_tokens if response.usage else 0
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

            except openai.RateLimitError as e:
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

            except openai.APIStatusError as e:
                last_error = e
                if e.status_code and e.status_code >= 500 and attempt < MAX_RETRIES:
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
                        f"OpenAI API error: {e.status_code} — {e.message}"
                    ) from e

            except openai.APIConnectionError as e:
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
        input_price, output_price = MODEL_PRICING.get(model, (0.14, 0.28))
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
