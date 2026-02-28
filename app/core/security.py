"""HMAC-SHA256 webhook signature verification.

Implements ADR-0004: every incoming webhook is cryptographically verified
BEFORE any other processing.  Uses hmac.compare_digest() for constant-time
comparison to prevent timing attacks.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def verify_github_signature(
    body: bytes,
    secret: str,
    signature_header: str | None,
) -> None:
    """Verify HMAC-SHA256 signature from a GitHub webhook.

    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
    We compute:   sha256=HMAC-SHA256(secret, body)

    Args:
        body: Raw request body bytes.
        secret: The shared webhook secret.
        signature_header: Value of the ``X-Hub-Signature-256`` header.

    Raises:
        HTTPException(403): If the signature is missing, malformed, or invalid.
    """
    if not signature_header:
        logger.warning("Webhook rejected: missing X-Hub-Signature-256 header")
        raise HTTPException(
            status_code=403,
            detail="Missing X-Hub-Signature-256 header",
        )

    if not signature_header.startswith("sha256="):
        logger.warning("Webhook rejected: malformed signature (no sha256= prefix)")
        raise HTTPException(
            status_code=403,
            detail="Invalid signature format — expected sha256= prefix",
        )

    received_signature = signature_header[7:]  # strip "sha256=" prefix

    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # CRITICAL: constant-time comparison prevents timing side-channel attacks.
    if not hmac.compare_digest(expected_signature, received_signature):
        logger.warning("Webhook rejected: invalid HMAC signature")
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature",
        )
