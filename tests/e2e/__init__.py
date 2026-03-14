"""End-to-end tests for OpenRabbit.

These tests simulate real-world complete workflows from webhook payload
through the full review pipeline. They use real filter and orchestrator logic
with mocked GitHub API and LLM clients.

Marked with @pytest.mark.e2e for optional exclusion from regular CI runs.
"""
