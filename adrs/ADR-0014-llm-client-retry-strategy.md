# ADR-0014: LLM Client Retry Strategy

**Status:** Accepted
**Date:** 2025-03-01
**Context:** Day 3 — Reliable Anthropic API integration with cost tracking

## Decision

Wrap the Anthropic Python SDK in an `LLMClient` class with:

### Retry Logic
| Error Type | Wait | Max Retries |
|-----------|------|------------|
| `RateLimitError` (429) | 60s fixed | 3 |
| `APIError` (5xx) | 5s, 15s, 45s exponential | 3 |
| `APIConnectionError` | 5s, 15s, 45s exponential | 3 |
| JSON parse failure | 0s (append "Return ONLY valid JSON") | 1 |

### Cost Tracking
Calculate cost per call from `usage.input_tokens` and `usage.output_tokens`:
- Haiku 3.5: $0.80 / $4.00 per 1M tokens (input/output)
- Sonnet 3.5: $3.00 / $15.00 per 1M tokens

### Timeout
- 120 second request timeout for all LLM calls.

### Logging
Every LLM call logs: `model`, `input_tokens`, `output_tokens`, `cost_usd`, `duration_ms`.

## Consequences
- Automatic recovery from transient API failures.
- Per-review cost is tracked and logged for monitoring.
- JSON parsing with retry avoids pipeline failures from malformed LLM responses.
