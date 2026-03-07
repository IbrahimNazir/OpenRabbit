"""MVP review pipeline orchestrator.

Implements ADR-0015: single-pass pipeline for Day 3 MVP.
Flow: Fetch diff → Parse → Filter → LLM per hunk → Map positions → Return results.

This will evolve into a multi-stage pipeline in Days 7+.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.comment_formatter import Finding, ReviewResult
from app.core.diff_parser import FileDiff, build_line_to_position_map, parse_diff
from app.core.filter_engine import FilterEngine
from app.core.github_client import GitHubClient
from app.llm.client import LLMClient
from app.llm.prompts import PROMPT_BUG_DETECTION, PROMPT_SUMMARIZE, SYSTEM_REVIEWER

logger = logging.getLogger(__name__)

# MVP limits per ADR-0015
MAX_FILES_PER_REVIEW = 10
MAX_HUNKS_PER_FILE = 5
LLM_CONCURRENCY = 1  # Sequential on free-tier Gemini (15 RPM limit)


async def run_pipeline(
    github_client: GitHubClient,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str = "",
    pr_description: str = "",
) -> ReviewResult:
    """Execute the MVP review pipeline.

    Args:
        github_client: Authenticated GitHub API client.
        repo_full_name: ``owner/repo`` format.
        pr_number: Pull request number.
        head_sha: HEAD commit SHA of the PR.
        base_sha: BASE commit SHA of the PR.
        pr_title: Title of the PR (for summarization).
        pr_description: Body/description of the PR.

    Returns:
        A ReviewResult with all findings and cost tracking.
    """
    total_cost = 0.0
    stages: list[str] = []

    llm = LLMClient()
    filter_engine = FilterEngine()
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    # ------------------------------------------------------------------
    # Step 1: Fetch the raw diff
    # ------------------------------------------------------------------
    logger.info(
        "Pipeline: fetching diff",
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
    # Step 2: Parse the diff
    # ------------------------------------------------------------------
    file_diffs = parse_diff(raw_diff)
    if not file_diffs:
        logger.info("No file diffs found — nothing to review")
        return ReviewResult(
            pr_summary="No code changes found in this PR.",
            stages_completed=["parse"],
        )

    # ------------------------------------------------------------------
    # Step 3: Filter reviewable files
    # ------------------------------------------------------------------
    all_filenames = [fd.filename for fd in file_diffs]
    reviewable_names = set(filter_engine.get_reviewable_files(all_filenames))
    reviewable_diffs = [fd for fd in file_diffs if fd.filename in reviewable_names]

    # Apply MVP file limit
    if len(reviewable_diffs) > MAX_FILES_PER_REVIEW:
        logger.info(
            "Truncating to %d files (from %d)",
            MAX_FILES_PER_REVIEW,
            len(reviewable_diffs),
        )
        reviewable_diffs = reviewable_diffs[:MAX_FILES_PER_REVIEW]

    if not reviewable_diffs:
        return ReviewResult(
            pr_summary="All changed files are non-code files — skipping review.",
            stages_completed=["parse", "filter"],
        )

    stages.append("filter")

    # ------------------------------------------------------------------
    # Step 4: Summarize the PR (Stage 1)
    # ------------------------------------------------------------------
    diff_summary = raw_diff[:2000]
    try:
        summary_prompt = PROMPT_SUMMARIZE.format(
            pr_title=pr_title or "(no title)",
            pr_description=pr_description or "(no description)",
            diff_summary=diff_summary,
        )
        summary_data, summary_cost = await llm.complete_with_json(
            summary_prompt,
            system=SYSTEM_REVIEWER,
        )
        total_cost += summary_cost

        pr_summary = summary_data.get("summary", "PR reviewed.") if isinstance(summary_data, dict) else "PR reviewed."
        stages.append("summarize")
    except Exception:
        logger.exception("Summarization failed — continuing without summary")
        pr_summary = "PR reviewed."

    # ------------------------------------------------------------------
    # Step 5: Bug detection per hunk (Stage 2)
    # ------------------------------------------------------------------
    all_findings: list[Finding] = []
    hunks_reviewed = 0

    async def _analyze_hunk(
        file_diff: FileDiff,
        hunk_index: int,
        hunk_content: str,
        position_map: dict[int, int],
    ) -> list[Finding]:
        """Analyze a single hunk with the LLM."""
        nonlocal total_cost, hunks_reviewed

        async with semaphore:
            try:
                prompt = PROMPT_BUG_DETECTION.format(
                    file_path=file_diff.filename,
                    language=file_diff.language or "text",
                    hunk_content=hunk_content,
                    full_file_context="",  # MVP: no full file context yet
                )
                result, cost = await llm.complete_with_json(
                    prompt,
                    system=SYSTEM_REVIEWER,
                )
                total_cost += cost
                hunks_reviewed += 1

                findings: list[Finding] = []
                items = result if isinstance(result, list) else []

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    line_start = item.get("line_start", 0)
                    line_end = item.get("line_end", line_start)

                    # Map to diff_position
                    diff_pos = position_map.get(line_start)
                    if diff_pos is None:
                        # Try nearby lines
                        for offset in range(1, 4):
                            diff_pos = position_map.get(line_start + offset)
                            if diff_pos is not None:
                                break
                            diff_pos = position_map.get(line_start - offset)
                            if diff_pos is not None:
                                break

                    if diff_pos is None:
                        logger.debug(
                            "Skipping finding — no valid diff_position for line %d",
                            line_start,
                        )
                        continue

                    findings.append(
                        Finding(
                            file_path=file_diff.filename,
                            line_start=line_start,
                            line_end=line_end,
                            diff_position=diff_pos,
                            severity=item.get("severity", "low"),
                            category=item.get("category", "bug"),
                            title=item.get("title", "Issue found"),
                            body=item.get("body", ""),
                            suggestion_code=item.get("suggestion_code"),
                            confidence=0.8,
                        )
                    )

                return findings

            except Exception:
                logger.exception(
                    "Hunk analysis failed for %s hunk %d",
                    file_diff.filename,
                    hunk_index,
                )
                return []

    # Launch tasks for each hunk across all files
    tasks: list[asyncio.Task[list[Finding]]] = []

    for file_diff in reviewable_diffs:
        position_map = build_line_to_position_map(file_diff)
        hunks = file_diff.hunks[:MAX_HUNKS_PER_FILE]

        for i, hunk in enumerate(hunks):
            # Build hunk content string with line numbers
            lines: list[str] = []
            for dl in hunk.lines:
                prefix = " "
                if dl.line_type == "added":
                    prefix = "+"
                elif dl.line_type == "removed":
                    prefix = "-"

                lineno = dl.new_lineno or dl.old_lineno or 0
                lines.append(f"{lineno:4d} {prefix} {dl.content}")

            hunk_content = "\n".join(lines)

            task = asyncio.create_task(
                _analyze_hunk(file_diff, i, hunk_content, position_map)
            )
            tasks.append(task)

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_findings.extend(r)
            elif isinstance(r, Exception):
                logger.warning("Task failed: %s", r)

    stages.append("bug_detection")

    # ------------------------------------------------------------------
    # Step 6: Build and return the ReviewResult
    # ------------------------------------------------------------------
    # Sort findings: critical first, then by file, then by line
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    all_findings.sort(
        key=lambda f: (severity_order.get(f.severity, 5), f.file_path, f.line_start)
    )

    # Cap at 25 findings
    if len(all_findings) > 25:
        all_findings = all_findings[:25]

    result = ReviewResult(
        pr_summary=pr_summary,
        findings=all_findings,
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
            "findings": len(all_findings),
            "cost_usd": f"{total_cost:.4f}",
            "files": len(reviewable_diffs),
            "hunks": hunks_reviewed,
        },
    )

    return result
