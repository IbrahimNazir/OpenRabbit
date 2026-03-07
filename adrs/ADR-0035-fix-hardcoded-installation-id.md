# ADR-0035: Remove Hardcoded installation_id from Webhook Handler

## Status
Accepted

## Context
In `app/api/webhooks.py` line 165, the `installation_id` is hardcoded:

```python
installation_id: int = 113171699  # Hardcoded correct installation ID
```

This was a temporary workaround that bypasses the actual webhook payload. The correct `installation_id` is already present in the payload at `payload["installation"]["id"]`.

A corresponding log line on line 180 logs the "wrong" `webhook_installation_id` which becomes irrelevant once we read from the payload.

## Decision
- Replace the hardcoded value with `payload.get("installation", {}).get("id", 0)` — the same pattern used elsewhere in the codebase (e.g., `_handle_installation_event`).
- Remove the `webhook_installation_id` key from the log `extra` dict since there is no longer a discrepancy to track.

## Consequences
- The webhook handler becomes multi-tenant: every installation gets the correct token.
- No more silent failures when new installations send PR webhooks.
