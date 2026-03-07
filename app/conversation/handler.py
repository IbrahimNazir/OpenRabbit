"""Conversation handler — processes developer replies to AI review comments.

Implements ADR-0037: intent detection (fix / explain / ignore) and action
dispatch.  Looks up the original Finding from the DB, generates a response
via the LLM or returns a pre-existing suggestion, and posts a reply on GitHub.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.exceptions import GitHubAPIError

logger = logging.getLogger(__name__)

# Intent patterns (matched against normalised comment body)
_FIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/fix\b"),
    re.compile(r"\bfix\s+this\b"),
    re.compile(r"\bfix\s+it\b"),
]
_EXPLAIN_PATTERN: re.Pattern[str] = re.compile(r"^/explain\b")
_IGNORE_PATTERN: re.Pattern[str] = re.compile(r"^/ignore\b")


class ConversationHandler:
    """Handles developer replies to OpenRabbit review comments."""

    def __init__(self, github_client: Any) -> None:
        self.github = github_client

    # ------------------------------------------------------------------
    #  Public entry point
    # ------------------------------------------------------------------

    async def handle(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        comment_id: int,
        comment_body: str,
        in_reply_to_id: int | None,
        redis_client: Any,
    ) -> dict[str, Any]:
        """Process a PR review comment reply.

        Args:
            installation_id: GitHub App installation ID.
            repo_full_name: ``owner/repo`` format.
            pr_number: Pull request number.
            comment_id: The reply comment GitHub ID.
            comment_body: Raw body of the reply.
            in_reply_to_id: GitHub ID of the original comment being replied to.
            redis_client: Async Redis client (for future state caching).

        Returns:
            Dict describing the action taken or reason for ignoring.
        """
        normalised = comment_body.lower().strip()

        # Must be a reply to an existing comment
        if in_reply_to_id is None:
            logger.info(
                "Comment is not a reply — ignoring",
                extra={"comment_id": comment_id, "repo": repo_full_name},
            )
            return {"action": "ignored", "reason": "not_a_reply"}

        # Look up the original Finding in the DB
        finding = self._lookup_finding(in_reply_to_id)
        if finding is None:
            logger.info(
                "No Finding found for in_reply_to_id — ignoring",
                extra={"in_reply_to_id": in_reply_to_id, "repo": repo_full_name},
            )
            return {"action": "ignored", "reason": "finding_not_found"}

        # Detect intent
        intent = self._detect_intent(normalised)
        if intent == "unknown":
            logger.info(
                "Unknown intent — ignoring",
                extra={"body_preview": normalised[:80], "comment_id": comment_id},
            )
            return {"action": "ignored", "reason": "unknown_intent"}

        logger.info(
            "Intent detected",
            extra={
                "intent": intent,
                "comment_id": comment_id,
                "finding_id": str(finding.id),
            },
        )

        # Dispatch by intent
        new_comment_id: int = 0

        if intent == "fix":
            new_comment_id = await self._handle_fix(
                finding=finding,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                in_reply_to_id=in_reply_to_id,
            )

        elif intent == "explain":
            new_comment_id = await self._handle_explain(
                finding=finding,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                in_reply_to_id=in_reply_to_id,
            )

        elif intent == "ignore":
            new_comment_id = await self._handle_ignore(
                finding=finding,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                in_reply_to_id=in_reply_to_id,
            )

        # Upsert ConversationThread record
        self._upsert_conversation_thread(
            github_comment_id=comment_id,
            finding=finding,
            pr_number=pr_number,
            intent=intent,
        )

        return {"action": intent, "comment_id": new_comment_id}

    # ------------------------------------------------------------------
    #  Intent detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_intent(normalised_body: str) -> str:
        """Classify the reply intent from the normalised comment body."""
        for pattern in _FIX_PATTERNS:
            if pattern.search(normalised_body):
                return "fix"

        if _EXPLAIN_PATTERN.search(normalised_body):
            return "explain"

        if _IGNORE_PATTERN.search(normalised_body):
            return "ignore"

        return "unknown"

    # ------------------------------------------------------------------
    #  Intent handlers
    # ------------------------------------------------------------------

    async def _handle_fix(
        self,
        finding: Any,
        repo_full_name: str,
        pr_number: int,
        in_reply_to_id: int,
    ) -> int:
        """Handle 'fix' intent: post a suggestion code block or generate one."""
        if finding.suggestion_code:
            # Pre-existing suggestion — post it directly
            body = (
                "Here's the suggested fix:\n\n"
                f"```suggestion\n{finding.suggestion_code}\n```"
            )
        else:
            # Generate a fix via LLM
            body = await self._generate_fix_via_llm(finding)

        return await self._post_reply(repo_full_name, pr_number, body, in_reply_to_id)

    async def _handle_explain(
        self,
        finding: Any,
        repo_full_name: str,
        pr_number: int,
        in_reply_to_id: int,
    ) -> int:
        """Handle 'explain' intent: generate a detailed explanation via LLM."""
        body = await self._generate_explanation_via_llm(finding)
        return await self._post_reply(repo_full_name, pr_number, body, in_reply_to_id)

    async def _handle_ignore(
        self,
        finding: Any,
        repo_full_name: str,
        pr_number: int,
        in_reply_to_id: int,
    ) -> int:
        """Handle 'ignore' intent: dismiss the finding in the DB."""
        self._dismiss_finding(finding)
        body = "✅ Finding dismissed. It will not be reported again."
        return await self._post_reply(repo_full_name, pr_number, body, in_reply_to_id)

    # ------------------------------------------------------------------
    #  GitHub interaction
    # ------------------------------------------------------------------

    async def _post_reply(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        in_reply_to_id: int,
    ) -> int:
        """Post a reply comment on the PR thread. Returns the new comment ID."""
        try:
            result = await self.github.post_review_comment(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=body,
                in_reply_to=in_reply_to_id,
            )
            new_id: int = result.get("id", 0)
            logger.info(
                "Posted reply comment",
                extra={"new_comment_id": new_id, "repo": repo_full_name},
            )
            return new_id
        except GitHubAPIError:
            logger.exception(
                "Failed to post reply comment",
                extra={"repo": repo_full_name, "pr_number": pr_number},
            )
            return 0

    # ------------------------------------------------------------------
    #  LLM interactions
    # ------------------------------------------------------------------

    async def _generate_fix_via_llm(self, finding: Any) -> str:
        """Use the LLM to generate a code fix for the finding."""
        try:
            from app.llm.client import LLMClient

            llm = LLMClient()
            prompt = (
                f"A code review finding was identified:\n\n"
                f"**Title:** {finding.title or 'N/A'}\n"
                f"**Description:** {finding.body or 'N/A'}\n"
                f"**File:** {finding.file_path or 'N/A'}\n"
                f"**Lines:** {finding.line_start}–{finding.line_end}\n\n"
                f"Generate ONLY the corrected code that fixes this issue. "
                f"Return the code inside a markdown code block. "
                f"Do not include explanations outside the code block."
            )
            response_text, _cost = await llm.complete(
                prompt=prompt,
                system="You are a senior software engineer. Output only code fixes.",
            )
            await llm.close()

            # Wrap in suggestion block
            code = self._extract_code_block(response_text)
            return f"Here's a suggested fix:\n\n```suggestion\n{code}\n```"

        except Exception:
            logger.exception("LLM fix generation failed")
            return (
                "⚠️ I wasn't able to generate a fix automatically. "
                "Please review the finding and apply a manual fix."
            )

    async def _generate_explanation_via_llm(self, finding: Any) -> str:
        """Use the LLM to generate a detailed explanation of the finding."""
        try:
            from app.llm.client import LLMClient

            llm = LLMClient()
            prompt = (
                f"A code review finding was identified:\n\n"
                f"**Title:** {finding.title or 'N/A'}\n"
                f"**Description:** {finding.body or 'N/A'}\n"
                f"**File:** {finding.file_path or 'N/A'}\n"
                f"**Severity:** {finding.severity or 'N/A'}\n"
                f"**Category:** {finding.category or 'N/A'}\n\n"
                f"Provide a detailed explanation of:\n"
                f"1. Why this is an issue\n"
                f"2. What could go wrong if it's not addressed\n"
                f"3. The recommended approach to fix it\n"
                f"4. Any relevant best practices or references"
            )
            response_text, _cost = await llm.complete(
                prompt=prompt,
                system="You are a senior software engineer explaining code review findings.",
            )
            await llm.close()
            return response_text

        except Exception:
            logger.exception("LLM explanation generation failed")
            return (
                f"**{finding.title or 'Finding'}**\n\n"
                f"{finding.body or 'No additional details available.'}"
            )

    @staticmethod
    def _extract_code_block(text: str) -> str:
        """Extract the first code block from LLM response text."""
        match = re.search(r"```(?:\w*)\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    # ------------------------------------------------------------------
    #  Database operations (sync — runs inside asyncio.run in a thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_finding(github_comment_id: int) -> Any:
        """Look up a Finding record by its GitHub comment ID."""
        from sqlalchemy import select

        from app.models.database import get_sync_db
        from app.models.pr_review import Finding

        session = get_sync_db()
        try:
            stmt = select(Finding).where(Finding.github_comment_id == github_comment_id)
            result = session.execute(stmt)
            return result.scalars().first()
        finally:
            session.close()

    @staticmethod
    def _dismiss_finding(finding: Any) -> None:
        """Set was_dismissed = True on a Finding record."""
        from app.models.database import get_sync_db

        session = get_sync_db()
        try:
            finding_in_session = session.merge(finding)
            finding_in_session.was_dismissed = True
            session.commit()
            logger.info(
                "Finding dismissed",
                extra={"finding_id": str(finding.id)},
            )
        except Exception:
            session.rollback()
            logger.exception("Failed to dismiss finding")
            raise
        finally:
            session.close()

    @staticmethod
    def _upsert_conversation_thread(
        github_comment_id: int,
        finding: Any,
        pr_number: int,
        intent: str,
    ) -> None:
        """Upsert a ConversationThread record with the current thread state."""
        from sqlalchemy import select

        from app.models.database import get_sync_db
        from app.models.pr_review import ConversationThread

        session = get_sync_db()
        try:
            stmt = select(ConversationThread).where(
                ConversationThread.github_comment_id == github_comment_id
            )
            result = session.execute(stmt)
            thread = result.scalars().first()

            thread_state: dict[str, Any] = {
                "last_intent": intent,
                "finding_id": str(finding.id),
            }

            if thread:
                # Merge new state into existing
                existing_state: dict[str, Any] = thread.thread_state or {}
                existing_state.update(thread_state)
                thread.thread_state = existing_state
            else:
                thread = ConversationThread(
                    github_comment_id=github_comment_id,
                    finding_id=finding.id,
                    pr_number=pr_number,
                    thread_state=thread_state,
                )
                session.add(thread)

            session.commit()
            logger.info(
                "Upserted conversation thread",
                extra={
                    "github_comment_id": github_comment_id,
                    "intent": intent,
                },
            )
        except Exception:
            session.rollback()
            logger.exception("Failed to upsert conversation thread")
        finally:
            session.close()
