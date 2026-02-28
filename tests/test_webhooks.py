"""Tests for the GitHub webhook endpoint (HMAC validation + event routing).

Covers ADR-0004 test requirements:
- Missing signature → 403
- Malformed signature → 403
- Wrong signature → 403
- Valid signature → 200
- Tampered body → 403
- PR event routing
- Installation event routing
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app

# Test secret — used in all HMAC tests.
TEST_SECRET = "test_webhook_secret_1234567890abcdef"


@pytest.fixture(autouse=True)
def _override_settings() -> None:
    """Patch get_settings to return a mock with the test webhook secret.

    This avoids the issue where pydantic-settings reads the .env file
    and overrides monkeypatch.setenv values.
    """
    mock_settings = MagicMock()
    mock_settings.github_webhook_secret = TEST_SECRET
    mock_settings.log_level = "INFO"

    with patch("app.api.webhooks.get_settings", return_value=mock_settings):
        yield


@pytest.fixture()
def client() -> TestClient:
    """Create a FastAPI TestClient."""
    return TestClient(app, raise_server_exceptions=False)


def _sign_payload(payload: bytes, secret: str = TEST_SECRET) -> str:
    """Compute a valid HMAC-SHA256 signature for a payload."""
    signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def _pr_payload(
    action: str = "opened",
    author: str = "developer",
    draft: bool = False,
    pr_number: int = 42,
) -> dict:
    """Build a minimal PR webhook payload."""
    return {
        "action": action,
        "installation": {"id": 12345, "account": {"login": "test-org"}},
        "repository": {"id": 67890, "full_name": "test-org/test-repo"},
        "pull_request": {
            "number": pr_number,
            "title": "Test PR",
            "draft": draft,
            "user": {"login": author},
            "head": {"sha": "abc123def456"},
            "base": {"sha": "000111222333"},
            "labels": [],
        },
    }


# =============================================================================
#  HMAC Signature Tests
# =============================================================================


class TestHMACValidation:
    """HMAC-SHA256 signature verification tests per ADR-0004."""

    def test_missing_signature_returns_403(self, client: TestClient) -> None:
        """No X-Hub-Signature-256 header → 403."""
        response = client.post(
            "/api/webhooks/github",
            content=b"{}",
            headers={"X-GitHub-Event": "ping"},
        )
        assert response.status_code == 403
        assert "Missing" in response.json()["detail"]

    def test_malformed_signature_returns_403(self, client: TestClient) -> None:
        """Header exists but doesn't start with 'sha256=' → 403."""
        response = client.post(
            "/api/webhooks/github",
            content=b"{}",
            headers={
                "X-Hub-Signature-256": "md5=notavalidformat",
                "X-GitHub-Event": "ping",
            },
        )
        assert response.status_code == 403
        assert "Invalid signature format" in response.json()["detail"]

    def test_wrong_signature_returns_403(self, client: TestClient) -> None:
        """Correct format, wrong HMAC value → 403."""
        payload = json.dumps(_pr_payload()).encode()
        response = client.post(
            "/api/webhooks/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
                "X-GitHub-Event": "pull_request",
            },
        )
        assert response.status_code == 403
        assert "Invalid webhook signature" in response.json()["detail"]

    def test_valid_signature_returns_200(self, client: TestClient) -> None:
        """Correct HMAC with test secret → 200."""
        payload = json.dumps(_pr_payload()).encode()
        signature = _sign_payload(payload)
        response = client.post(
            "/api/webhooks/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    def test_tampered_body_returns_403(self, client: TestClient) -> None:
        """Valid signature for original body, body modified → 403."""
        original_payload = json.dumps(_pr_payload()).encode()
        signature = _sign_payload(original_payload)

        # Tamper with the body.
        tampered = json.dumps(_pr_payload(action="closed")).encode()

        response = client.post(
            "/api/webhooks/github",
            content=tampered,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
            },
        )
        assert response.status_code == 403


# =============================================================================
#  Event Routing Tests
# =============================================================================


class TestEventRouting:
    """Verify correct event routing for different GitHub webhook types."""

    def _post_event(
        self,
        client: TestClient,
        payload: dict,
        event: str = "pull_request",
    ) -> dict:
        """Post a signed webhook event and return the response JSON."""
        body = json.dumps(payload).encode()
        signature = _sign_payload(body)
        response = client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": event,
            },
        )
        assert response.status_code == 200
        return response.json()

    def test_pr_opened_accepted(self, client: TestClient) -> None:
        """PR opened event is accepted."""
        result = self._post_event(client, _pr_payload(action="opened"))
        assert result["status"] == "accepted"

    def test_pr_synchronize_accepted(self, client: TestClient) -> None:
        """PR synchronize (new push) event is accepted."""
        result = self._post_event(client, _pr_payload(action="synchronize"))
        assert result["status"] == "accepted"

    def test_pr_reopened_accepted(self, client: TestClient) -> None:
        """PR reopened event is accepted."""
        result = self._post_event(client, _pr_payload(action="reopened"))
        assert result["status"] == "accepted"

    def test_pr_closed_noop(self, client: TestClient) -> None:
        """PR closed event is a no-op (still returns 200)."""
        result = self._post_event(client, _pr_payload(action="closed"))
        assert result["status"] == "accepted"

    def test_installation_created(self, client: TestClient) -> None:
        """Installation created event is accepted."""
        payload = {
            "action": "created",
            "installation": {"id": 99999, "account": {"login": "new-org"}},
            "repositories": [{"id": 1, "full_name": "new-org/repo1"}],
        }
        result = self._post_event(client, payload, event="installation")
        assert result["status"] == "accepted"

    def test_installation_deleted(self, client: TestClient) -> None:
        """Installation deleted event is accepted."""
        payload = {
            "action": "deleted",
            "installation": {"id": 99999, "account": {"login": "old-org"}},
        }
        result = self._post_event(client, payload, event="installation")
        assert result["status"] == "accepted"

    def test_review_comment_created(self, client: TestClient) -> None:
        """Review comment created event is accepted."""
        payload = {
            "action": "created",
            "comment": {"id": 1234, "body": "Fix this", "in_reply_to_id": 999},
            "pull_request": {"number": 42},
            "repository": {"full_name": "test-org/test-repo"},
            "installation": {"id": 12345},
        }
        result = self._post_event(client, payload, event="pull_request_review_comment")
        assert result["status"] == "accepted"

    def test_unknown_event_noop(self, client: TestClient) -> None:
        """Unknown event types return 200 (no-op)."""
        payload = {"action": "completed", "check_run": {"id": 1}}
        result = self._post_event(client, payload, event="check_run")
        assert result["status"] == "accepted"
