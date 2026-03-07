"""Multi-stage review pipeline orchestrator.

Implements ADR-0022 (ReviewContext), ADR-0023 (five-stage pipeline),
and the 20-day plan Task 7.3 + Day 6 Task 6.3.

Pipeline flow:
  Stage 0  Static analysis (linters, parallel across files)
  Stage 1  PR Summarization  (sequential — seeds all later prompts)
  Stage 2  Bug & Security Detection (parallel across files/hunks)
  Stage 3  Cross-File Impact Analysis (conditional — only if risk=high
           or function signature changed)
  Stage 4  Style & Best Practices (parallel across hunks, cheap model)
  Stage 5  Synthesis & Deduplication (rule-based + optional LLM)

All stages share a single ``ReviewContext`` dataclass.  The context
carries the GitHub client, parsed diffs, config, and mutable state
(summary, linter findings) that is written by early stages and read
by later ones.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.comment_formatter import Finding, ReviewResult
from app.core.config_loader import ReviewConfig, load_review_config, should_ignore_file
from app.core.diff_parser import FileDiff, build_line_to_position_map, parse_diff
from app.core.filter_engine import FilterEngine
from app.core.github_client import GitHubClient
from app.llm.client import LLMClient
from app.parsing.tree_sitter_parser import TreeSitterParser
from app.pipeline.stage_0_linters import LinterFinding, run_linters
from app.pipeline.stage_1_summary import SummaryResult, run_summarization
from app.pipeline.stage_2_bugs import run_bug_detection
from app.pipeline.stage_3_xfile import run_cross_file_analysis
from app.pipeline.stage_4_style import run_style_review
from app.pipeline.stage_5_synth import run_synthesis

logger = logging.getLogger(__name__)

# Maximum files reviewed per PR (MVP + Phase 2 cap)
MAX_FILES_PER_REVIEW = 15
# Maximum concurrent LLM calls across all stages
LLM_CONCURRENCY = 5

# Regex patterns used to detect function definition lines (mirrors stage_3_xfile)
_FUNC_DEF_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^\s*(async\s+)?def\s+\w+\s*\("),
    "javascript": re.compile(r"^\s*(async\s+)?function\s+\w+\s*\(|^\s*\w+\s*=\s*(async\s+)?\(.*\)\s*=>"),
    "typescript": re.compile(r"^\s*(async\s+)?function\s+\w+\s*\("),
    "go": re.compile(r"^\s*func\s+\w+\s*\("),
    "rust": re.compile(r"^\s*(pub\s+)?(async\s+)?fn\s+\w+\s*\("),
    "java": re.compile(r"^\s*(public|private|protected|static|\s)*\s+\w+\s+\w+\s*\("),
}

# Module-level tree-sitter parser singleton
_ts_parser = TreeSitterParser()


# ---------------------------------------------------------------------------
#  ReviewContext
# ---------------------------------------------------------------------------


@dataclass
class ReviewContext:
    """State carrier for the entire review pipeline.

    Early stages write into ``summary`` and ``linter_findings_by_file``.
    Later stages read from these fields.  Fields marked "set by pipeline"
    are populated by ``run_pipeline()`` before stage execution begins.
    """

    github_client: GitHubClient
    repo_full_name: str
    pr_number: int
    head_sha: str
    base_sha: str
    config: ReviewConfig
    file_diffs: list[FileDiff]    # set by pipeline after filtering
    raw_diff: str                 # full raw diff text
    pr_title: str = ""
    pr_description: str = ""

    # Mutable state written by stages
    summary: SummaryResult | None = None
    linter_findings: list[LinterFinding] = field(default_factory=list)
    linter_findings_by_file: dict[str, list[LinterFinding]] = field(
        default_factory=dict
    )

    # Shared LLM client + semaphore (initialized by run_pipeline)
    llm: LLMClient = field(default_factory=LLMClient)
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(LLM_CONCURRENCY)
    )

    def should_run_stage_3(self) -> bool:
        """Return True if cross-file analysis should run."""
        if self.summary and self.summary.risk_level == "high":
            return True
        # Check if any function signature line appears in the diff
        for file_diff in self.file_diffs:
            lang = file_diff.language or ""
            pattern = _FUNC_DEF_PATTERNS.get(lang)
            if pattern is None:
                continue
            for hunk in file_diff.hunks:
                for dl in hunk.lines:
                    if dl.line_type in ("added", "removed") and pattern.search(
                        dl.content
                    ):
                        return True
        return False


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    github_client: GitHubClient,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str = "",
    pr_description: str = "",
) -> ReviewResult:
    """Execute the full five-stage review pipeline.

    Args:
        github_client: Authenticated GitHub API client.
        repo_full_name: ``owner/repo`` format.
        pr_number: Pull request number.
        head_sha: HEAD commit SHA.
        base_sha: BASE commit SHA.
        pr_title: PR title for summarization prompts.
        pr_description: PR body for summarization prompts.

    Returns:
        ``ReviewResult`` with all findings and metadata.
    """
    total_cost = 0.0
    stages: list[str] = []
    llm = LLMClient()
    filter_engine = FilterEngine()

    # ------------------------------------------------------------------
    # 1. Fetch the raw diff
    # ------------------------------------------------------------------
    logger.info(
        "Pipeline starting",
        extra={"repo": repo_full_name, "pr": pr_number},
    )
    try:
        raw_diff = await github_client.get_pr_diff(repo_full_name, pr_number)
    except Exception:
        logger.exception("Failed to fetch PR diff")
        return ReviewResult(
            pr_summary="Failed to fetch PR diff.",
            stages_completed=["error"],
        )

    # ------------------------------------------------------------------
    # 2. Parse diff
    # ------------------------------------------------------------------
    file_diffs = parse_diff(raw_diff)
    if not file_diffs:
        return ReviewResult(
            pr_summary="No code changes found in this PR.",
            stages_completed=["parse"],
        )

    # ------------------------------------------------------------------
    # 3. Load repo config
    # ------------------------------------------------------------------
    try:
        config = await load_review_config(github_client, repo_full_name, base_sha)
    except Exception:
        logger.warning("Config load failed — using defaults")
        config = ReviewConfig()

    if not config.enabled:
        return ReviewResult(
            pr_summary="Review disabled via .openrabbit.yaml.",
            stages_completed=["config"],
        )

    # ------------------------------------------------------------------
    # 4. Filter reviewable files
    # ------------------------------------------------------------------
    all_filenames = [fd.filename for fd in file_diffs]
    reviewable_names = set(filter_engine.get_reviewable_files(all_filenames))

    # Also apply config ignore_patterns
    reviewable_diffs: list[FileDiff] = []
    for fd in file_diffs:
        if fd.filename not in reviewable_names:
            continue
        if should_ignore_file(fd.filename, config):
            logger.debug("Config ignore: %s", fd.filename)
            continue
        reviewable_diffs.append(fd)

    if len(reviewable_diffs) > MAX_FILES_PER_REVIEW:
        logger.info(
            "Truncating to %d files (from %d)",
            MAX_FILES_PER_REVIEW,
            len(reviewable_diffs),
        )
        reviewable_diffs = reviewable_diffs[:MAX_FILES_PER_REVIEW]

    if not reviewable_diffs:
        return ReviewResult(
            pr_summary="All changed files are non-code or ignored — skipping review.",
            stages_completed=["parse", "filter"],
        )

    stages.append("filter")

    # ------------------------------------------------------------------
    # 5. Enrich hunks with AST context (Task 6.3)
    # ------------------------------------------------------------------
    await _enrich_hunks_with_ast_context(
        github_client, repo_full_name, head_sha, reviewable_diffs
    )
    stages.append("ast_enrichment")

    # ------------------------------------------------------------------
    # 6. Build ReviewContext
    # ------------------------------------------------------------------
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    ctx = ReviewContext(
        github_client=github_client,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        config=config,
        file_diffs=reviewable_diffs,
        raw_diff=raw_diff,
        pr_title=pr_title,
        pr_description=pr_description,
        llm=llm,
        semaphore=semaphore,
    )

    # ------------------------------------------------------------------
    # Stage 0: Static analysis (parallel across files)
    # ------------------------------------------------------------------
    linter_findings_by_file = await _run_stage_0(ctx)
    ctx.linter_findings_by_file = linter_findings_by_file
    ctx.linter_findings = [lf for lfs in linter_findings_by_file.values() for lf in lfs]
    stages.append("stage_0_linters")

    # ------------------------------------------------------------------
    # Stage 1: Summarization (sequential — seeds later stages)
    # ------------------------------------------------------------------
    summary = await run_summarization(
        diff_text=raw_diff,
        pr_title=pr_title,
        pr_description=pr_description,
        llm_client=llm,
    )
    ctx.summary = summary
    total_cost += summary.cost_usd
    stages.append("stage_1_summary")

    # ------------------------------------------------------------------
    # Stage 2: Bug & Security Detection
    # ------------------------------------------------------------------
    bug_findings = await run_bug_detection(ctx)
    stages.append("stage_2_bugs")

    # ------------------------------------------------------------------
    # Stage 3: Cross-File Impact (conditional)
    # ------------------------------------------------------------------
    xfile_findings: list[Finding] = []
    if ctx.should_run_stage_3():
        logger.info("Stage 3 triggered (risk=%s)", summary.risk_level)
        xfile_findings = await run_cross_file_analysis(ctx)
        stages.append("stage_3_xfile")
    else:
        logger.debug("Stage 3 skipped")

    # ------------------------------------------------------------------
    # Stage 4: Style Review
    # ------------------------------------------------------------------
    style_findings = await run_style_review(ctx, existing_findings=bug_findings)
    stages.append("stage_4_style")

    # ------------------------------------------------------------------
    # Stage 5: Synthesis & Deduplication
    # ------------------------------------------------------------------
    # Convert linter findings to Finding objects for synthesis input
    linter_as_findings = _linter_findings_to_findings(
        linter_findings_by_file, reviewable_diffs
    )

    all_raw = linter_as_findings + bug_findings + xfile_findings + style_findings
    final_findings = await run_synthesis(all_raw, ctx)
    stages.append("stage_5_synthesis")

    # ------------------------------------------------------------------
    # Build ReviewResult
    # ------------------------------------------------------------------
    hunks_reviewed = sum(len(fd.hunks) for fd in reviewable_diffs)

    result = ReviewResult(
        pr_summary=summary.summary,
        findings=final_findings,
        total_cost_usd=total_cost,
        stages_completed=stages,
        files_reviewed=len(reviewable_diffs),
        hunks_reviewed=hunks_reviewed,
    )

    logger.info(
        "Pipeline complete",
        extra={
            "repo": repo_full_name,
            "pr": pr_number,
            "findings": len(final_findings),
            "cost_usd": f"{total_cost:.4f}",
            "files": len(reviewable_diffs),
            "stages": stages,
        },
    )

    return result


# ---------------------------------------------------------------------------
#  Stage 0 helper (run linters for all files in parallel)
# ---------------------------------------------------------------------------


async def _run_stage_0(
    ctx: ReviewContext,
) -> dict[str, list[LinterFinding]]:
    """Run linters for all files in parallel using asyncio + threads."""

    async def _lint_one(fd: FileDiff) -> tuple[str, list[LinterFinding]]:
        try:
            # Fetch file content if not already available
            file_content = fd.hunks[0].full_file_content if fd.hunks else None
            if not file_content:
                try:
                    file_content = await ctx.github_client.get_file_content(
                        ctx.repo_full_name, fd.filename, ctx.head_sha
                    )
                except Exception:
                    logger.debug("Could not fetch file for linting: %s", fd.filename)
                    return fd.filename, []

            loop = asyncio.get_event_loop()
            findings = await loop.run_in_executor(
                None,
                run_linters,
                fd.filename,
                file_content,
                fd.language or "text",
                fd.hunks,
            )
            return fd.filename, findings
        except Exception:
            logger.exception("Stage 0 linting failed for %s", fd.filename)
            return fd.filename, []

    tasks = [asyncio.create_task(_lint_one(fd)) for fd in ctx.file_diffs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    by_file: dict[str, list[LinterFinding]] = {}
    for r in results:
        if isinstance(r, tuple):
            filename, findings = r
            by_file[filename] = findings
        elif isinstance(r, Exception):
            logger.warning("Linter task failed: %s", r)

    return by_file


# ---------------------------------------------------------------------------
#  AST context enrichment (Task 6.3)
# ---------------------------------------------------------------------------


async def _enrich_hunks_with_ast_context(
    github_client: GitHubClient,
    repo_full_name: str,
    head_sha: str,
    file_diffs: list[FileDiff],
) -> None:
    """Fetch file contents and enrich each hunk with AST context.

    For each FileDiff:
      1. Fetch the full file content at head_sha.
      2. Parse with Tree-sitter.
      3. For each hunk: set ast_function_context and scope_context.
      4. Store full_file_content on the first hunk for file-level analysis.
    """

    async def _enrich_one(fd: FileDiff) -> None:
        try:
            content = await github_client.get_file_content(
                repo_full_name, fd.filename, head_sha
            )
        except Exception:
            logger.debug("Could not fetch file for AST enrichment: %s", fd.filename)
            return

        source_lines = content.splitlines()

        # Parse with tree-sitter (sync, fast)
        loop = asyncio.get_event_loop()
        tree = await loop.run_in_executor(
            None, _ts_parser.parse_file, content, fd.language or "text"
        )

        for hunk in fd.hunks:
            # Determine the end line of this hunk in the new file
            hunk_new_end = hunk.new_start + hunk.new_count - 1

            # AST-derived enclosing function
            if tree is not None and fd.language:
                try:
                    fn_name = await loop.run_in_executor(
                        None,
                        _ts_parser.get_enclosing_function,
                        tree,
                        hunk.new_start,
                        content,
                        fd.language,
                    )
                    hunk.ast_function_context = fn_name
                except Exception:
                    pass

            # Scope context (5 lines before + hunk + 5 lines after)
            hunk.scope_context = _ts_parser.build_scope_context(
                source_lines,
                hunk.new_start,
                hunk_new_end,
            )
            # Store full file content on the first hunk
            hunk.full_file_content = content

    tasks = [asyncio.create_task(_enrich_one(fd)) for fd in file_diffs]
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
#  Convert LinterFindings → Findings (for Stage 5 synthesis)
# ---------------------------------------------------------------------------


def _linter_findings_to_findings(
    linter_by_file: dict[str, list[LinterFinding]],
    file_diffs: list[FileDiff],
) -> list[Finding]:
    """Convert ``LinterFinding`` objects to ``Finding`` objects for synthesis.

    Only includes linter findings that can be mapped to a valid diff position.
    """
    findings: list[Finding] = []
    pos_maps: dict[str, dict[int, int]] = {
        fd.filename: build_line_to_position_map(fd) for fd in file_diffs
    }

    for file_path, lints in linter_by_file.items():
        pos_map = pos_maps.get(file_path, {})
        for lf in lints:
            diff_pos = pos_map.get(lf.line)
            if diff_pos is None:
                # Try ±3 tolerance
                for offset in range(1, 4):
                    diff_pos = pos_map.get(lf.line + offset) or pos_map.get(
                        lf.line - offset
                    )
                    if diff_pos:
                        break
            if diff_pos is None:
                continue

            severity = "medium" if lf.severity == "error" else "low"
            if lf.tool == "gitleaks":
                severity = "critical"

            findings.append(
                Finding(
                    file_path=file_path,
                    line_start=lf.line,
                    line_end=lf.line,
                    diff_position=diff_pos,
                    severity=severity,
                    category="security" if lf.tool == "gitleaks" else "bug",
                    title=f"[{lf.tool}] {lf.rule}",
                    body=lf.message,
                    suggestion_code=None,
                    confidence=0.85,
                )
            )

    return findings
