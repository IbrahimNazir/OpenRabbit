# ADR-0037: Conversation Handler — "Fix this" Reply Flow

## Status
Accepted

## Context
The webhook handler receives `pull_request_review_comment` events and extracts `comment_id`, `comment_body`, and `in_reply_to_id` but has a TODO placeholder and never dispatches a task (line 253 of `webhooks.py`).

The architecture document (Section 5.2) describes the "Fix this → Commit Suggestion" flow. The build plan (Day 18) schedules this feature.

## Decision

### New Celery Task: `app/tasks/conversation_task.py`
- Task name: `openrabbit.handle_pr_reply`, queue `fast_lane`, max_retries=2, default_retry_delay=15.
- Same sync→async bridge pattern as `review_task.py` (ThreadPoolExecutor + asyncio.run).
- Signature: `handle_pr_reply(self, installation_id, repo_full_name, pr_number, comment_id, comment_body, in_reply_to_id)`.

### New Handler: `app/conversation/handler.py`
- `ConversationHandler.handle(...)` processes the reply.
- Intent detection: `/fix` or "fix this" or "fix it" → "fix"; `/explain` → "explain"; `/ignore` → "ignore"; else → "unknown".
- Look up `Finding` by `github_comment_id == in_reply_to_id` from the sync DB.
- For "fix": post suggestion code or generate fix via LLM.
- For "explain": generate detailed explanation via LLM and post reply.
- For "ignore": set `finding.was_dismissed = True`, reply confirming dismissal.
- Upsert `ConversationThread` record.

### Webhook Wiring
Replace the TODO in `_handle_review_comment_event` with `handle_pr_reply.apply_async(...)`.

## Consequences
- Developers can reply "fix this", "/explain", or "/ignore" to AI review comments.
- Conversation state is persisted in the `conversation_threads` table.
- The architecture aligns with the conversation reply flow diagram in the architecture doc.
