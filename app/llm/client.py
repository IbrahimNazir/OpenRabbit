"""LLM client supporting multiple providers (Gemini, DeepSeek, etc.).

Implements ADR-0014: retry logic, cost tracking, JSON parsing with fallback.

Supports:
  - Gemini (free API)
  - DeepSeek (OpenAI-compatible API)
  - Anthropic (Claude)

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

try:
    import google.generativeai as genai
    from google.api_core.exceptions import ResourceExhausted
except ImportError:
    genai = None  # type: ignore
    ResourceExhausted = None  # type: ignore

try:
    import openai
except ImportError:
    openai = None  # type: ignore

from app.config import get_settings
from app.core.exceptions import LLMError, LLMParseError, LLMRateLimitError

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (input / output)
# Gemini Pro 1.5: free tier (some quotas)
# DeepSeek: paid API
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.0-flash": (0.0, 0.0),  # Free tier
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-3-5-sonnet": (3.0, 15.0),
}

# Defaults
DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
REQUEST_TIMEOUT = 120.0

# Retry constants per ADR-0014
RATE_LIMIT_WAIT_SECONDS = 60
SERVER_ERROR_BACKOFF = [5, 15, 45]
MAX_RETRIES = 3


class LLMClient:
    """Multi-provider async LLM client with retry logic and cost tracking.
    
    Supports Gemini (free), DeepSeek, and Anthropic APIs.
    Provider is selected via LLM_PROVIDER environment variable.
    Falls back to DeepSeek if Gemini quota is exceeded.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.provider = settings.llm_provider.lower()
        self.model = DEFAULT_MODEL
        self._clients_initialized = False
        
        # Initialize all available clients
        self._init_clients()

    def _init_clients(self) -> None:
        """Initialize all available LLM clients."""
        if self._clients_initialized:
            return
            
        settings = get_settings()
        
        # Gemini client
        self._gemini_client = None
        if genai and settings.gemini_api_key:
            try:
                genai.configure(api_key=settings.gemini_api_key)
                self._gemini_client = genai
                logger.info("Gemini client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini client: {e}")
        
# DeepSeek client (uses OpenAI-compatible SDK)
        self._openai_client = None
        if openai and settings.deepseek_api_key:
            try:
                self._openai_client = openai.AsyncOpenAI(
                    api_key=settings.deepseek_api_key,
                    base_url="https://api.deepseek.com",
                    timeout=REQUEST_TIMEOUT,
                )
                logger.info("DeepSeek (OpenAI) client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize DeepSeek/OpenAI client: {e}")
        
        # Anthropic client (placeholder)
        self._anthropic_client = None
        if settings.anthropic_api_key:
            logger.info("Anthropic API key configured (not yet implemented)")
        
        self._clients_initialized = True
        
        # Set default provider and model
        if self.provider == "gemini" and self._gemini_client:
            self.model = "gemini-2.0-flash"
        elif self.provider == "deepseek" and self._openai_client:
            self.model = "deepseek-chat"
        elif self._openai_client:
            # Fallback to DeepSeek if primary provider not available
            self.provider = "deepseek"
            self.model = "deepseek-chat"
            logger.info("Falling back to DeepSeek as primary provider")
        else:
            raise LLMError("No LLM providers available. Check API keys and installations.")

    async def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> tuple[str, float]:
        """Send a prompt to the configured LLM and return (response_text, cost_usd).

        Retries on rate limits and server errors per ADR-0014.
        Falls back to DeepSeek if Gemini quota exceeded.
        """
        effective_model = model or self.model
        start_time = time.monotonic()

        # Try primary provider first
        try:
            if self.provider == "gemini" and self._gemini_client:
                logger.info("Trying Gemini provider")
                return await self._complete_gemini(
                    prompt,
                    system=system,
                    model=effective_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    start_time=start_time,
                )
            elif self.provider == "deepseek" and self._openai_client:
                logger.info("Trying DeepSeek provider")
                return await self._complete_deepseek(
                    prompt,
                    system=system,
                    model=effective_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    start_time=start_time,
                )
            else:
                raise LLMError(f"Primary provider {self.provider} not available")
        except LLMRateLimitError as e:
            # If primary provider has rate limit/quota issues, try fallback
            logger.warning(f"Primary provider {self.provider} rate limited, trying fallback: {e}")
            if self.provider == "gemini" and self._openai_client:
                logger.info("Falling back to DeepSeek due to Gemini quota")
                return await self._complete_deepseek(
                    prompt,
                    system=system,
                    model="deepseek-chat",
                    max_tokens=max_tokens,
                    temperature=temperature,
                    start_time=start_time,
                )
            logger.error("No fallback provider available")
            raise  # Re-raise if no fallback available

    async def complete_with_json(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
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
                    extra={"response_preview": text[:200], "provider": self.provider},
                )

        raise LLMParseError(f"Failed to parse JSON after 2 attempts. Last response: {text[:300]}")

    # ------------------------------------------------------------------
    #  Gemini Implementation
    # ------------------------------------------------------------------

    async def _complete_gemini(
        self,
        prompt: str,
        *,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        start_time: float,
    ) -> tuple[str, float]:
        """Call Gemini API with retries."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Build message with system prompt
                full_prompt = prompt
                if system:
                    full_prompt = f"{system}\n\n{prompt}"

                # Call Gemini via sync wrapper (Gemini SDK is sync)
                client = self._gemini_client
                model_obj = client.GenerativeModel(model)

                # Run sync call in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: model_obj.generate_content(
                        full_prompt,
                        generation_config={
                            "max_output_tokens": max_tokens,
                            "temperature": temperature,
                        },
                    ),
                )

                text = response.text or ""

                # Gemini doesn't provide token usage in free tier
                cost_usd = 0.0
                duration_ms = int((time.monotonic() - start_time) * 1000)

                logger.info(
                    "LLM call completed",
                    extra={
                        "provider": "gemini",
                        "model": model,
                        "duration_ms": duration_ms,
                        "attempt": attempt + 1,
                    },
                )

                return text, cost_usd

            except Exception as e:
                last_error = e
                # Check for specific error types
                error_str = str(e).lower()
                if any(x in error_str for x in ["rate_limit", "too many requests", "quota", "429"]) or isinstance(e, ResourceExhausted):
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            "Gemini rate limit/quota hit — waiting %ds before retry",
                            RATE_LIMIT_WAIT_SECONDS,
                            extra={"attempt": attempt + 1},
                        )
                        await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
                    else:
                        raise LLMRateLimitError(
                            f"Rate limit/quota exceeded after {MAX_RETRIES + 1} attempts"
                        ) from e
                elif any(x in error_str for x in ["500", "502", "503", "service"]):
                    if attempt < MAX_RETRIES:
                        wait = SERVER_ERROR_BACKOFF[
                            min(attempt, len(SERVER_ERROR_BACKOFF) - 1)
                        ]
                        logger.warning(
                            "Gemini server error — retrying in %ds",
                            wait,
                            extra={"attempt": attempt + 1},
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise LLMError(f"Gemini API error after retries: {str(e)}") from e
                else:
                    raise LLMError(f"Gemini API error: {str(e)}") from e

        raise LLMError("Unexpected retry loop exit") from last_error

    # ------------------------------------------------------------------
    #  DeepSeek Implementation (OpenAI-compatible)
    # ------------------------------------------------------------------

    async def _complete_deepseek(
        self,
        prompt: str,
        *,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        start_time: float,
    ) -> tuple[str, float]:
        """Call DeepSeek API (via OpenAI SDK) with retries."""
        last_error: Exception | None = None

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._openai_client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                )

                text = response.choices[0].message.content or ""

                # Calculate cost
                input_tokens = response.usage.prompt_tokens if response.usage else 0
                output_tokens = response.usage.completion_tokens if response.usage else 0
                cost_usd = self._calculate_cost(model, input_tokens, output_tokens)

                duration_ms = int((time.monotonic() - start_time) * 1000)
                logger.info(
                    "LLM call completed",
                    extra={
                        "provider": "deepseek",
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
                        "DeepSeek rate limit hit — waiting %ds before retry",
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
                    wait = SERVER_ERROR_BACKOFF[
                        min(attempt, len(SERVER_ERROR_BACKOFF) - 1)
                    ]
                    logger.warning(
                        "DeepSeek server error %d — retrying in %ds",
                        e.status_code,
                        wait,
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(wait)
                else:
                    raise LLMError(
                        f"DeepSeek API error: {e.status_code} — {e.message}"
                    ) from e

            except openai.APIConnectionError as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = SERVER_ERROR_BACKOFF[
                        min(attempt, len(SERVER_ERROR_BACKOFF) - 1)
                    ]
                    logger.warning(
                        "DeepSeek connection error — retrying in %ds",
                        wait,
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(wait)
                else:
                    raise LLMError(
                        f"Connection failed after {MAX_RETRIES + 1} attempts"
                    ) from e

        raise LLMError("Unexpected retry loop exit") from last_error

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

    async def close(self) -> None:
        """Close any open client connections."""
        # DeepSeek client doesn't need explicit closing
        # Gemini client doesn't need explicit closing
        pass
