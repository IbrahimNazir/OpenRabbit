# ADR-0022: ReviewContext as Pipeline State Carrier

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

Phase 1's `run_pipeline()` function passed individual parameters (github_client, repo_full_name, pr_number, ...) through every helper function. As the pipeline grew to 5+ stages, each stage needed different subsets of this context, and passing 10+ arguments to every function becomes unmaintainable.

## Decision

Introduce a `ReviewContext` dataclass as the single state carrier passed to every pipeline stage:

```python
@dataclass
class ReviewContext:
    github_client: GitHubClient
    repo_full_name: str
    pr_number: int
    head_sha: str
    base_sha: str
    config: ReviewConfig
    file_diffs: list[FileDiff]
    raw_diff: str
    pr_title: str = ""
    pr_description: str = ""
    summary: SummaryResult | None = None      # set after Stage 1
    linter_findings: list[LinterFinding] = field(default_factory=list)  # set after Stage 0
    llm: LLMClient = field(default_factory=LLMClient)
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(5))
```

Each stage mutates the context (e.g., sets `ctx.summary`) and returns its findings list.

## Consequences

**Positive:**
- Stages have a clean, consistent signature: `async def run_stage_N(ctx: ReviewContext) -> list[Finding]`
- Adding new state (e.g., retrieved RAG chunks in Phase 3) requires one field addition
- `should_run_stage_3()` method encapsulates escalation logic cleanly
- Easy to test: mock only the fields each stage needs

**Negative:**
- Mutable shared state can be hard to reason about in parallel stages
- Stages must not mutate shared list fields while other async tasks are running

**Mitigation:** Stages that run in parallel (Stage 0, Stage 2 files, Stage 4 hunks) only read from `ctx`, never write. Only sequential stages (1, 3) write to `ctx`.
