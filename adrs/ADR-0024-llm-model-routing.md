# ADR-0024: LLM Model Routing via Complexity Score

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

Not all hunks need the same LLM quality. Sending a 3-line configuration change to the most capable model wastes money. Sending a cryptographic implementation to the cheapest model produces unreliable results.

The current codebase supports Gemini, Groq, and DeepSeek providers. Anthropic is configured but not yet implemented. The model cascade from the architecture document references Claude Haiku/Sonnet/Opus, but the actual routing must work with whatever provider is configured.

## Decision

Use a **complexity score** (0–10) to route each file analysis to the appropriate model tier:

```
Complexity Score:
+ Lines changed: 1-10 = +1, 11-30 = +3, 31+ = +5
+ Security-critical file (auth/payment/crypto/jwt/token patterns) = +4
+ Function signature changed = +3
+ File has >3 hunks = +2

Routing:
0-3  → fast model (cheap, default provider model)
4-7  → main model (default provider model — same for now)
8-10 → main model with explicit security focus in prompt
```

In Phase 2, both tiers use the default provider model (provider-agnostic). When Anthropic is wired up, the `FAST_MODEL` constant will map to `claude-haiku-4-5-20251001` and `MAIN_MODEL` to `claude-sonnet-4-5-20251001`.

The pipeline stage functions accept an optional `model` parameter; passing `None` uses the LLMClient default.

## Consequences

**Positive:**
- Future-proof: model constants can be updated to Anthropic IDs without changing stage logic
- Complexity scoring is deterministic and cheap (no LLM call)
- Security-critical files always receive more thorough analysis

**Negative:**
- Score thresholds are heuristic — may need tuning based on empirical results
- "Same model for all tiers" in Phase 2 means no actual cost savings until Anthropic is integrated
