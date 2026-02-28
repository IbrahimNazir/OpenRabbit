# ADR-0004: HMAC-SHA256 Webhook Signature Validation

| Field | Value |
|-------|-------|
| **ID** | ADR-0004 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | security, webhooks, authentication, hmac |

---

## Context and Problem Statement

OpenRabbit exposes a public HTTP endpoint (`POST /api/webhooks/github`) that must accept webhook payloads from GitHub. Because this endpoint is public, it is reachable by anyone on the internet — not just GitHub. Without verification, an attacker could:

1. Send fake webhook payloads to trigger AI reviews on arbitrary code (costing us LLM API money)
2. Replay captured webhook payloads to re-trigger reviews
3. Inject crafted payloads that manipulate our pipeline logic
4. Enumerate internal repository data by observing our responses

We need a mechanism to cryptographically verify that every incoming webhook payload was genuinely sent by GitHub and has not been tampered with in transit.

---

## Decision Drivers

1. **Cryptographic certainty** — Verification must be mathematically sound, not security-through-obscurity
2. **Latency** — Verification must complete in <1ms (before any other processing)
3. **Constant-time comparison** — Prevent timing attacks that could allow secret enumeration
4. **GitHub standard compliance** — Must work with the standard GitHub webhook signature mechanism
5. **Simplicity** — Every contributor must be able to understand the verification code in under 2 minutes

---

## Considered Options

### Option A: HMAC-SHA256 Signature Verification (CHOSEN)

GitHub computes `HMAC-SHA256(secret, payload_body)` and sends it in the `X-Hub-Signature-256` header as `sha256={hex_digest}`. We compute the same HMAC with our shared secret and compare.

**Security properties:**
- Requires knowledge of the shared secret to forge
- The payload body is part of the signature — any tampering with the body invalidates the signature
- Replay attacks are possible in theory but limited by GitHub's delivery retry window (~1 hour); our idempotency key (see `review_task.py`) handles duplicates

### Option B: IP Allowlist Only

Verify that the request originates from GitHub's published webhook IP ranges.

**Problems:**
- GitHub's IP ranges change over time — requires continuous updates
- Provides no protection if an attacker controls a machine in GitHub's IP range (e.g., GitHub Actions runner abuse)
- Does not protect against payload tampering
- Rejected: insufficient security, high maintenance burden

### Option C: Request Signing with Asymmetric Keys (RSA/Ed25519)

Use public/private key signing where GitHub signs with a private key and we verify with their public key.

**Problems:**
- GitHub does not support this mechanism for webhooks (as of 2025) — HMAC is their specified standard
- Would require us to implement a custom non-standard signature scheme
- Rejected: not supported by GitHub's webhook infrastructure

### Option D: API Key in URL/Header

Include a secret token in the webhook URL (`/api/webhooks/github?token=xxx`) or as a custom header.

**Problems:**
- URL-based secrets appear in server logs and proxy logs
- No protection against payload tampering
- Rejected: weaker than HMAC and exposes the secret in logs

---

## Decision

**Implement HMAC-SHA256 signature verification as the first operation in every webhook handler, before any other processing.**

The verification must happen before: JSON parsing, database queries, task enqueueing, or any other work. A request that fails HMAC verification is dropped immediately with `HTTP 403`.

### Implementation

```python
# app/core/security.py
import hashlib
import hmac
from fastapi import HTTPException, Request

async def verify_github_signature(
    request: Request,
    body: bytes,
    secret: str,
) -> None:
    """
    Verify HMAC-SHA256 signature from GitHub webhook.
    
    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
    We compute:   sha256=HMAC-SHA256(secret, body)
    
    Uses hmac.compare_digest() for constant-time comparison
    to prevent timing attacks.
    
    Raises:
        HTTPException(403): if signature is missing or invalid
    """
    signature_header = request.headers.get("X-Hub-Signature-256")
    
    if not signature_header:
        raise HTTPException(
            status_code=403,
            detail="Missing X-Hub-Signature-256 header"
        )
    
    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=403,
            detail="Invalid signature format — expected sha256= prefix"
        )
    
    received_signature = signature_header[7:]  # strip "sha256=" prefix
    
    # Compute expected HMAC
    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    
    # CRITICAL: Use constant-time comparison to prevent timing attacks.
    # A naive string comparison (==) leaks information about how many
    # characters match, allowing an attacker to enumerate the secret
    # one character at a time via timing measurements.
    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature"
        )
    # If we reach here, the signature is valid — proceed with processing
```

### Webhook Handler Integration

```python
# app/api/webhooks.py
@router.post("/github", status_code=200)
async def receive_github_webhook(
    request: Request,
    config: Settings = Depends(get_settings),
) -> dict:
    # Step 1: Read raw body BEFORE parsing
    # We must read the raw bytes for HMAC — parsing first would discard them
    body = await request.body()
    
    # Step 2: Verify signature — FIRST OPERATION, NO EXCEPTIONS
    await verify_github_signature(request, body, config.github_webhook_secret)
    
    # Step 3: Only now parse the payload
    payload = json.loads(body)
    
    # Step 4: Process (enqueue task) and return 200
    # ...
    return {"status": "accepted"}
```

### Secret Generation

```bash
# Generate a cryptographically strong webhook secret
openssl rand -hex 32
# Output example: a3f8b2c1d4e5f6789012345678901234567890abcdef1234567890abcdef12

# Store in .env
GITHUB_WEBHOOK_SECRET=a3f8b2c1d4e5f6789012345678901234567890abcdef1234567890abcdef12
```

---

## Consequences

### Positive
- Every webhook request is cryptographically verified before any processing occurs
- Fake payloads are rejected in <1ms with zero resource cost (no DB, no LLM, no task queue touched)
- Constant-time comparison prevents timing attacks — the comparison time is independent of how similar the provided signature is to the correct one
- Compatible with GitHub's standard webhook mechanism — no custom configuration needed on the GitHub side
- The verification function has zero external dependencies (only Python stdlib `hmac` and `hashlib`)

### Negative
- The shared secret must be securely stored and rotated if compromised. **Mitigation:** stored only in `.env` (never in code or DB), documented in `SECURITY.md` with rotation instructions
- Replay attacks are theoretically possible within GitHub's retry window. **Mitigation:** our task idempotency key (`review:{repo_id}:{pr_number}:{head_sha}` in Redis) prevents double-processing of identical events

### Neutral
- The `request.body()` must be called once before JSON parsing — FastAPI's request body is a stream and can only be read once. This is why we read raw bytes first, verify, then parse.

---

## Testing Requirements

The following test cases are mandatory for this ADR (see `tests/test_webhooks.py`):

```python
def test_missing_signature_returns_403():
    """No X-Hub-Signature-256 header → 403"""

def test_malformed_signature_returns_403():
    """Header exists but doesn't start with 'sha256=' → 403"""

def test_wrong_signature_returns_403():
    """Correct format, wrong HMAC value → 403"""

def test_valid_signature_returns_200():
    """Correct HMAC with test secret → 200"""

def test_tampered_body_returns_403():
    """Valid signature for original body, body modified → 403"""

def test_timing_consistency():
    """
    Correct and incorrect signatures should take approximately the same
    time to verify (within 10ms of each other across 100 iterations).
    This validates the constant-time comparison property.
    """
```

---

## Secrets Rotation Procedure

If the webhook secret is compromised:

1. Generate a new secret: `openssl rand -hex 32`
2. Update the secret in GitHub App settings (Settings → Webhooks → Edit)
3. Update `.env` on all running instances
4. Restart the API gateway service
5. **Do NOT** update secret in GitHub before updating the application — there is a brief window where GitHub sends events with the new secret that the old application rejects. Use a rolling update to minimize impact.
