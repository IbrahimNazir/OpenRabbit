"""Stage 3: Cross-File Impact Analysis.

Implements ADR-0023 Stage 3 and the 20-day plan Task 9.1.

Phase 2 heuristic approach (symbol graph will replace this in Phase 4):
1. Extract changed function names from added/removed function definition lines.
2. Search for usages in PR diff context (other changed files).
3. Run LLM: "Does this change break call sites?"

Only triggered when ReviewContext.should_run_stage_3() returns True.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.core.comment_formatter import Finding
from app.core.diff_parser import FileDiff, build_line_to_position_map
from app.llm.prompts import PROMPT_CROSS_FILE_IMPACT, SYSTEM_REVIEWER

if TYPE_CHECKING:
    from app.pipeline.orchestrator import ReviewContext

logger = logging.getLogger(__name__)

# Regex patterns to detect function definition lines per language
_FUNC_DEF_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^\s*(async\s+)?def\s+(\w+)\s*\("),
    "javascript": re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(|^\s*(\w+)\s*=\s*(async\s+)?\(.*\)\s*=>"),
    "typescript": re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(|^\s*(async\s+)?(\w+)\s*\(.*\)\s*:"),
    "go": re.compile(r"^\s*func\s+(\w+)\s*\("),
    "rust": re.compile(r"^\s*(pub\s+)?(async\s+)?fn\s+(\w+)\s*\("),
    "java": re.compile(r"^\s*(public|private|protected|static|\s)*\s+\w+\s+(\w+)\s*\("),
}

MAX_CALL_SITES = 10
CALL_SITE_CONTEXT_LINES = 10


async def run_cross_file_analysis(ctx: "ReviewContext") -> list[Finding]:
    """Analyze cross-file breaking change impact.

    Args:
        ctx: The shared ``ReviewContext``.

    Returns:
        List of ``Finding`` objects with category='breaking-change'.
    """
    changed_functions = _extract_changed_functions(ctx.file_diffs)
    if not changed_functions:
        logger.debug("Stage 3: no changed function signatures detected")
        return []

    logger.info(
        "Stage 3: analyzing %d changed functions for cross-file impact",
        len(changed_functions),
    )

    all_findings: list[Finding] = []

    for func_name, file_diff, change_desc in changed_functions:
        call_sites = _find_call_sites(func_name, file_diff.filename, ctx.file_diffs)
        if not call_sites:
            continue

        # Limit call sites sent to LLM
        call_sites = call_sites[:MAX_CALL_SITES]

        findings = await _analyze_impact(
            func_name, change_desc, call_sites, file_diff, ctx
        )
        all_findings.extend(findings)

    logger.info("Stage 3 complete: %d cross-file findings", len(all_findings))
    return all_findings


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _extract_changed_functions(
    file_diffs: list[FileDiff],
) -> list[tuple[str, FileDiff, str]]:
    """Return list of (function_name, file_diff, change_description) tuples.

    Detects functions whose signature line appears in the added OR removed
    sections of the diff (i.e., the definition itself changed).
    """
    results: list[tuple[str, FileDiff, str]] = []

    for file_diff in file_diffs:
        lang = file_diff.language or ""
        pattern = _FUNC_DEF_PATTERNS.get(lang)
        if pattern is None:
            continue

        for hunk in file_diff.hunks:
            added_funcs: set[str] = set()
            removed_funcs: set[str] = set()

            for dl in hunk.lines:
                match = pattern.search(dl.content)
                if not match:
                    continue
                # Extract function name from last non-empty capture group
                name = next(
                    (g for g in reversed(match.groups()) if g and re.match(r"^\w+$", g)),
                    None,
                )
                if not name:
                    continue

                if dl.line_type == "added":
                    added_funcs.add(name)
                elif dl.line_type == "removed":
                    removed_funcs.add(name)

            # Functions appearing in both added and removed = signature changed
            changed = added_funcs & removed_funcs
            for name in changed:
                change_desc = f"Function `{name}` signature was modified in `{file_diff.filename}`"
                results.append((name, file_diff, change_desc))

    return results


def _find_call_sites(
    func_name: str,
    changed_file: str,
    file_diffs: list[FileDiff],
) -> list[dict[str, object]]:
    """Search for usages of *func_name* in other diff files.

    Returns a list of dicts: {file, line, code}.
    """
    call_sites: list[dict[str, object]] = []
    call_pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(")

    for file_diff in file_diffs:
        if file_diff.filename == changed_file:
            continue  # skip the file where the function was changed

        for hunk in file_diff.hunks:
            for dl in hunk.lines:
                if dl.line_type == "removed":
                    continue  # only look at post-change context
                if call_pattern.search(dl.content):
                    lineno = dl.new_lineno or 0
                    # Build context: surrounding lines from the hunk
                    context = _extract_context_around_line(
                        hunk.lines, dl.diff_position, CALL_SITE_CONTEXT_LINES
                    )
                    call_sites.append(
                        {
                            "file": file_diff.filename,
                            "line": lineno,
                            "code": context,
                        }
                    )

    return call_sites


def _extract_context_around_line(
    diff_lines: list,
    target_diff_position: int,
    context: int,
) -> str:
    """Return `context` lines around the target diff position as a string."""
    for i, dl in enumerate(diff_lines):
        if dl.diff_position == target_diff_position:
            start = max(0, i - context)
            end = min(len(diff_lines), i + context + 1)
            return "\n".join(
                f"{dl2.new_lineno or dl2.old_lineno or '?':4} {dl2.content}"
                for dl2 in diff_lines[start:end]
                if dl2.line_type != "removed"
            )
    return ""


async def _analyze_impact(
    func_name: str,
    change_description: str,
    call_sites: list[dict[str, object]],
    changed_file_diff: FileDiff,
    ctx: "ReviewContext",
) -> list[Finding]:
    """Run LLM analysis on cross-file call sites."""
    call_sites_text = json.dumps(call_sites, indent=2)

    prompt = PROMPT_CROSS_FILE_IMPACT.format(
        changed_function=func_name,
        change_description=change_description,
        call_sites=call_sites_text,
    )

    try:
        data, cost = await ctx.llm.complete_with_json(
            prompt,
            system=SYSTEM_REVIEWER,
        )

        if not isinstance(data, dict):
            return []

        if not data.get("has_breaking_changes"):
            return []

        affected = data.get("affected_call_sites", [])
        if not isinstance(affected, list):
            return []

        findings: list[Finding] = []
        position_map = build_line_to_position_map(changed_file_diff)

        for site in affected:
            if not isinstance(site, dict):
                continue

            file_path = str(site.get("file", changed_file_diff.filename))
            line = int(site.get("line") or 0)
            issue = str(site.get("issue", "Potential breaking change"))
            suggestion = str(site.get("suggestion", ""))

            # Find the diff for this file to get its position map
            target_diff = next(
                (fd for fd in ctx.file_diffs if fd.filename == file_path),
                changed_file_diff,
            )
            target_pos_map = build_line_to_position_map(target_diff)
            diff_pos = _resolve_position(line, target_pos_map)
            if diff_pos is None:
                diff_pos = _resolve_position(line, position_map)
            if diff_pos is None:
                continue

            body = issue
            if suggestion:
                body += f"\n\n**Suggested fix:** {suggestion}"

            findings.append(
                Finding(
                    file_path=file_path,
                    line_start=line,
                    line_end=line,
                    diff_position=diff_pos,
                    severity="high",
                    category="breaking-change",
                    title=f"Breaking change: `{func_name}` call site may be affected",
                    body=body,
                    suggestion_code=None,
                    confidence=0.75,
                )
            )

        return findings

    except Exception:
        logger.exception("Cross-file impact analysis failed for %s", func_name)
        return []


def _resolve_position(line: int, position_map: dict[int, int]) -> int | None:
    if line in position_map:
        return position_map[line]
    for offset in range(1, 4):
        if (line + offset) in position_map:
            return position_map[line + offset]
        if (line - offset) in position_map:
            return position_map[line - offset]
    return None
