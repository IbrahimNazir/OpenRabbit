"""AST validation for LLM-generated code suggestions.

Implements ADR-0025: validate every code suggestion with Tree-sitter before
posting.  Invalid suggestions are silently dropped and the finding body is
annotated with a note.

Usage:
    result = validate_suggestion(code, "python", line_start=10, line_end=20)
    if result.is_valid:
        finding.suggestion_code = result.fixed_code or code
    else:
        finding.suggestion_code = None
        finding.body += "\\n\\n" + INVALID_SUGGESTION_NOTE
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass

from app.parsing.tree_sitter_parser import TreeSitterParser

logger = logging.getLogger(__name__)

INVALID_SUGGESTION_NOTE = (
    "(Note: A code fix was suggested but could not be validated — "
    "please review manually.)"
)

_parser = TreeSitterParser()


@dataclass
class ValidationResult:
    """Result of AST-based suggestion validation."""

    is_valid: bool
    error: str | None = None
    fixed_code: str | None = None   # Auto-fixed code if repair succeeded


def validate_suggestion(
    code: str,
    language: str,
    line_start: int = 0,
    line_end: int = 0,
) -> ValidationResult:
    """Validate a code suggestion using Tree-sitter.

    Steps:
    1. Parse with Tree-sitter.
    2. Check for ERROR / MISSING nodes.
    3. On error: attempt auto-fix (dedent + strip) and re-parse.
    4. Validate line-count: suggestion must be ≥ (line_end - line_start - 2).

    Args:
        code: The suggested code snippet.
        language: Language name (e.g. ``"python"``).
        line_start: First line of the finding (1-indexed). Used for line-count
                    validation when both are provided.
        line_end: Last line of the finding (1-indexed).

    Returns:
        ``ValidationResult``.
    """
    if not code or not code.strip():
        return ValidationResult(is_valid=False, error="Empty suggestion")

    # ------------------------------------------------------------------
    # Line-count validation (before AST — cheap check)
    # ------------------------------------------------------------------
    if line_start > 0 and line_end > line_start:
        expected_min_lines = max(0, (line_end - line_start) - 2)
        actual_lines = len([l for l in code.splitlines() if l.strip()])
        if expected_min_lines > 3 and actual_lines < expected_min_lines:
            return ValidationResult(
                is_valid=False,
                error=(
                    f"Suggestion has {actual_lines} non-empty lines but the finding "
                    f"spans {line_end - line_start + 1} lines — likely incomplete fix"
                ),
            )

    # ------------------------------------------------------------------
    # Tree-sitter check
    # ------------------------------------------------------------------
    tree = _parser.parse_file(code, language)
    if tree is None:
        # Unsupported language — skip validation, accept as-is
        logger.debug("AST validation skipped for language: %s", language)
        return ValidationResult(is_valid=True)

    if not _parser.has_syntax_errors(tree):
        return ValidationResult(is_valid=True)

    # ------------------------------------------------------------------
    # Auto-fix attempt 1: textwrap.dedent + strip
    # ------------------------------------------------------------------
    fixed = textwrap.dedent(code).strip()
    fixed_tree = _parser.parse_file(fixed, language)
    if fixed_tree is not None and not _parser.has_syntax_errors(fixed_tree):
        logger.debug("AST suggestion auto-fixed via dedent for language: %s", language)
        return ValidationResult(is_valid=True, fixed_code=fixed)

    # ------------------------------------------------------------------
    # Auto-fix attempt 2: try wrapping in a function body for snippets
    # that are valid statements but not stand-alone files
    # ------------------------------------------------------------------
    if language == "python":
        wrapped = f"def _validate_wrapper():\n"
        for line in code.splitlines():
            wrapped += f"    {line}\n"
        wrapped_tree = _parser.parse_file(wrapped, language)
        if wrapped_tree is not None and not _parser.has_syntax_errors(wrapped_tree):
            # The original code is valid as a function body — accept original
            return ValidationResult(is_valid=True)

    return ValidationResult(
        is_valid=False,
        error=f"Syntax errors detected in {language} suggestion",
    )
