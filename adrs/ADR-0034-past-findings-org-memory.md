# ADR-0034: Organizational Memory via Past Findings

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

LLM reviewers benefit from few-shot examples of good findings. Without memory, every PR review starts from scratch, potentially missing recurring patterns specific to an organization's codebase (e.g., a known anti-pattern that keeps reappearing in different files).

## Decision

After each review, upsert all findings to a second Qdrant collection `past_findings` (vector_size=1536, Cosine distance).

**Storage:**
- Vector: embedding of `finding.body` text
- Point ID: `uuid.uuid4()` (non-deterministic — each finding is a unique event)
- Payload: `{repo_id, org_id, category, severity, language, title, body, was_applied, was_dismissed}`

**Retrieval at review time:**
- Build query from `f"{language} {' '.join(changed_function_names[:3])}"`
- Search `past_findings` scoped to `repo_id`
- Return top 3 by similarity score
- Inject as "Similar Past Findings" section in Stage 2 prompt

**Feedback loop:**
- `was_applied`/`was_dismissed` updated when developer reacts to comments (Phase 4)
- Phase 3 upserts with `was_applied=False` initially

**Scoping:** `repo_id` scoping ensures findings from one repository don't pollute another's few-shot examples. `org_id=0` in Phase 3; Phase 4 will add cross-repo org-level retrieval.

## Consequences

- System improves per-repository over time without retraining
- First 5–10 reviews have no few-shot examples (cold start — acceptable)
- Storage cost: ~1536 floats × 4 bytes × N findings ≈ negligible (typically <1000 findings/repo)
- Upsert failures are silently ignored — never block the review

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Store findings in PostgreSQL, embed at query time | Embedding at query time adds latency; Qdrant already deployed |
| Cross-org retrieval from day 1 | Privacy concerns; org boundaries not yet modelled in Phase 3 |
| Fine-tune the LLM on past findings | Orders of magnitude more expensive and complex |
