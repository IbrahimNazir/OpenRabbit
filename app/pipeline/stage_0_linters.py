"""Stage 0: Static analysis via subprocess-sandboxed linters.

Implements ADR-0023 Stage 0 and the 20-day plan Task 7.1.

Tools:
- Python: ruff (fast, comprehensive)
- JavaScript/TypeScript: eslint (via npx, optional)
- Go: gofmt
- All: gitleaks (secret detection, optional)

All file I/O is isolated to ephemeral temp directories that are deleted in
the ``finally`` block.  Linter failures NEVER propagate — they are logged
and swallowed so the review continues without linter results.

Only findings that fall within the *changed* line ranges are returned, to
avoid surfacing pre-existing issues.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass

from app.core.diff_parser import DiffHunk

logger = logging.getLogger(__name__)

LINTER_TIMEOUT_SECONDS = 10


@dataclass
class LinterFinding:
    """A single finding from a static analysis tool."""

    tool: str       # "ruff" | "eslint" | "gofmt" | "gitleaks"
    rule: str       # e.g. "E501", "no-unused-vars"
    line: int       # 1-indexed line number in the file
    message: str
    severity: str   # "error" | "warning" | "info"


def run_linters(
    file_path: str,
    file_content: str,
    language: str,
    changed_hunks: list[DiffHunk],
) -> list[LinterFinding]:
    """Run appropriate linters for *language* on *file_content*.

    Args:
        file_path: Repository-relative path (used to name the temp file).
        file_content: Full file content as a string.
        language: Detected language name (e.g. ``"python"``).
        changed_hunks: The diff hunks for this file — used to filter findings
                       to lines within the changed ranges only.

    Returns:
        List of ``LinterFinding`` objects for changed lines only.
    """
    if not file_content:
        return []

    # Build set of changed line numbers for fast lookup
    changed_lines = _build_changed_line_set(changed_hunks)
    if not changed_lines:
        return []

    tmp_dir = tempfile.mkdtemp(prefix="openrabbit_lint_")
    try:
        # Write file to temp dir preserving its extension
        ext = os.path.splitext(file_path)[1] or ".txt"
        tmp_file = os.path.join(tmp_dir, f"file{ext}")
        with open(tmp_file, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(file_content)

        findings: list[LinterFinding] = []

        # Language-specific linters
        if language == "python":
            findings.extend(_run_ruff(tmp_file, changed_lines))
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            findings.extend(_run_eslint(tmp_file, language, changed_lines))
        elif language == "go":
            findings.extend(_run_gofmt(tmp_file, changed_lines))

        # Secrets detection — all languages
        findings.extend(_run_gitleaks(tmp_dir, tmp_file, changed_lines))

        return findings

    except Exception:
        logger.exception("Unexpected error in run_linters for %s", file_path)
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
#  Changed line set helper
# ---------------------------------------------------------------------------


def _build_changed_line_set(hunks: list[DiffHunk]) -> set[int]:
    """Return the set of new-file line numbers that were added or changed."""
    changed: set[int] = set()
    for hunk in hunks:
        for dl in hunk.lines:
            if dl.line_type in ("added", "context") and dl.new_lineno is not None:
                changed.add(dl.new_lineno)
    return changed


# ---------------------------------------------------------------------------
#  ruff (Python)
# ---------------------------------------------------------------------------


def _run_ruff(tmp_file: str, changed_lines: set[int]) -> list[LinterFinding]:
    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format=json", tmp_file],
            capture_output=True,
            text=True,
            timeout=LINTER_TIMEOUT_SECONDS,
        )
        if not result.stdout.strip():
            return []

        raw = json.loads(result.stdout)
        findings: list[LinterFinding] = []

        for item in raw:
            line = item.get("location", {}).get("row", 0)
            if line not in changed_lines:
                continue
            code = item.get("code") or "ruff"
            message = item.get("message", "")
            findings.append(
                LinterFinding(
                    tool="ruff",
                    rule=code,
                    line=line,
                    message=message,
                    severity="error" if code.startswith("E") else "warning",
                )
            )

        return findings

    except FileNotFoundError:
        logger.debug("ruff not found — skipping Python linting")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("ruff timed out on %s", tmp_file)
        return []
    except Exception:
        logger.exception("ruff failed on %s", tmp_file)
        return []


# ---------------------------------------------------------------------------
#  eslint (JavaScript / TypeScript)
# ---------------------------------------------------------------------------


def _run_eslint(
    tmp_file: str,
    language: str,
    changed_lines: set[int],
) -> list[LinterFinding]:
    try:
        result = subprocess.run(
            [
                "npx",
                "--yes",
                "eslint",
                "--no-eslintrc",
                "--rule",
                "{}",
                "--format",
                "json",
                tmp_file,
            ],
            capture_output=True,
            text=True,
            timeout=LINTER_TIMEOUT_SECONDS * 3,  # npx needs download time
        )
        output = result.stdout.strip()
        if not output:
            return []

        raw = json.loads(output)
        findings: list[LinterFinding] = []

        for file_result in raw:
            for msg in file_result.get("messages", []):
                line = msg.get("line", 0)
                if line not in changed_lines:
                    continue
                rule_id = msg.get("ruleId") or "eslint"
                message = msg.get("message", "")
                severity_code = msg.get("severity", 1)
                severity = "error" if severity_code == 2 else "warning"
                findings.append(
                    LinterFinding(
                        tool="eslint",
                        rule=rule_id,
                        line=line,
                        message=message,
                        severity=severity,
                    )
                )

        return findings

    except FileNotFoundError:
        logger.debug("npx not found — skipping ESLint")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("eslint timed out on %s", tmp_file)
        return []
    except Exception:
        logger.exception("eslint failed on %s", tmp_file)
        return []


# ---------------------------------------------------------------------------
#  gofmt (Go)
# ---------------------------------------------------------------------------


def _run_gofmt(tmp_file: str, changed_lines: set[int]) -> list[LinterFinding]:
    try:
        result = subprocess.run(
            ["gofmt", "-l", tmp_file],
            capture_output=True,
            text=True,
            timeout=LINTER_TIMEOUT_SECONDS,
        )
        findings: list[LinterFinding] = []

        if result.stdout.strip():
            # gofmt -l prints the filename if formatting differs — report at line 1
            findings.append(
                LinterFinding(
                    tool="gofmt",
                    rule="gofmt",
                    line=1,
                    message="File is not gofmt-formatted",
                    severity="warning",
                )
            )

        return findings

    except FileNotFoundError:
        logger.debug("gofmt not found — skipping Go formatting check")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("gofmt timed out on %s", tmp_file)
        return []
    except Exception:
        logger.exception("gofmt failed on %s", tmp_file)
        return []


# ---------------------------------------------------------------------------
#  gitleaks (secrets — all languages)
# ---------------------------------------------------------------------------


def _run_gitleaks(
    tmp_dir: str,
    tmp_file: str,
    changed_lines: set[int],
) -> list[LinterFinding]:
    try:
        report_file = os.path.join(tmp_dir, "gitleaks_report.json")
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--source",
                tmp_dir,
                "--no-git",
                "--report-format",
                "json",
                "--report-path",
                report_file,
                "--exit-code",
                "0",  # don't fail the process on findings
            ],
            capture_output=True,
            text=True,
            timeout=LINTER_TIMEOUT_SECONDS,
        )

        if not os.path.exists(report_file):
            return []

        with open(report_file, encoding="utf-8") as fh:
            raw = json.load(fh)

        if not raw:
            return []

        findings: list[LinterFinding] = []
        for leak in raw:
            line = leak.get("StartLine", 0)
            # Gitleaks reports are per-file — we accept all lines since secrets
            # are always critical regardless of whether they're in changed lines.
            findings.append(
                LinterFinding(
                    tool="gitleaks",
                    rule=leak.get("RuleID", "secret"),
                    line=line,
                    message=f"Potential secret detected: {leak.get('Description', '')}",
                    severity="error",
                )
            )

        return findings

    except FileNotFoundError:
        logger.debug("gitleaks not found — skipping secret detection")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("gitleaks timed out on %s", tmp_dir)
        return []
    except Exception:
        logger.exception("gitleaks failed on %s", tmp_dir)
        return []
