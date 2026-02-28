"""Unified diff parser — converts raw Git diffs into structured objects.

Implements ADR-0010: custom parser for exact GitHub diff-position accuracy.

Key concepts:
- ``diff_position`` is GitHub's 1-indexed counter within a file's diff.
  It counts the @@ header as position 1, then every subsequent line
  (context, added, removed) increments by 1.  Positions are cumulative
  across hunks within a single file but reset between files.
- Only added (+) and context ( ) lines are commentable in GitHub's API.
- The @@ header may include an optional function name after the second @@.

This module is a pure function: ``parse_diff(str) -> list[FileDiff]``.
Zero external dependencies — uses only Python stdlib + ``re``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


# =============================================================================
#  Data classes
# =============================================================================


@dataclass
class DiffLine:
    """A single line within a diff hunk."""

    content: str
    line_type: Literal["added", "removed", "context"]
    old_lineno: int | None  # None for added lines
    new_lineno: int | None  # None for removed lines
    diff_position: int      # GitHub's 1-indexed position within the file diff


@dataclass
class DiffHunk:
    """A hunk (@@-block) within a file diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str                       # Raw @@ line
    function_context: str | None      # e.g., "def process_payment():" from header
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """A complete file diff with all hunks parsed."""

    filename: str                               # New filename (after rename if applicable)
    old_filename: str | None                    # Old filename before rename; None if not renamed
    status: Literal["added", "modified", "removed", "renamed"]
    language: str | None                        # Detected from file extension
    hunks: list[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False


# =============================================================================
#  Language detection
# =============================================================================

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".tf": "terraform",
    ".proto": "protobuf",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".r": "r",
    ".scala": "scala",
    ".dart": "dart",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".vue": "vue",
    ".svelte": "svelte",
}


def _detect_language(filename: str) -> str | None:
    """Detect programming language from file extension."""
    for ext, lang in EXTENSION_TO_LANGUAGE.items():
        if filename.endswith(ext):
            return lang
    return None


# =============================================================================
#  Hunk header regex
# =============================================================================

# Matches: @@ -10,5 +10,7 @@ optional function context
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@\s*(.*)?$"
)


# =============================================================================
#  Diff file header parsing
# =============================================================================

def _extract_filename(header_line: str) -> tuple[str, str | None]:
    """Extract the new filename (and optional old filename) from a diff header.

    Returns:
        (new_filename, old_filename_if_different)
    """
    # diff --git a/path/to/old b/path/to/new
    match = re.match(r"^diff --git a/(.*) b/(.*)$", header_line)
    if match:
        old = match.group(1)
        new = match.group(2)
        return new, (old if old != new else None)
    return header_line, None


# =============================================================================
#  Main parser
# =============================================================================

def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff text into a list of ``FileDiff`` objects.

    This is the main entry point.  Handles:
    - Standard modified files
    - New files (all additions, ``/dev/null`` as old path)
    - Deleted files (all deletions, ``/dev/null`` as new path)
    - Renamed files (with or without content changes)
    - Binary files (detected via "Binary files" marker, skipped)
    - Multi-hunk files (positions cumulate across hunks)
    - ``\\ No newline at end of file`` markers (skipped, don't affect positions)
    - Function context in @@ headers (e.g., ``def my_func():``)

    Args:
        diff_text: Raw unified diff output from ``git diff`` or GitHub's API.

    Returns:
        List of ``FileDiff`` objects, one per file in the diff.
    """
    if not diff_text or not diff_text.strip():
        return []

    # Normalize line endings — handle CRLF from Windows or file IO.
    diff_text = diff_text.replace("\r\n", "\n").replace("\r", "\n")

    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None
    diff_position: int = 0  # cumulative within each file
    old_lineno: int = 0
    new_lineno: int = 0

    # Track file-level metadata lines.
    is_new_file = False
    is_deleted_file = False
    is_rename = False

    lines = diff_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- File header ---
        if line.startswith("diff --git "):
            # Save the previous file.
            if current_file is not None:
                files.append(current_file)

            filename, old_filename = _extract_filename(line)
            current_file = FileDiff(
                filename=filename,
                old_filename=old_filename,
                status="modified",
                language=_detect_language(filename),
            )
            current_hunk = None
            diff_position = 0
            is_new_file = False
            is_deleted_file = False
            is_rename = old_filename is not None
            i += 1
            continue

        # --- File metadata lines (before first hunk) ---
        if current_file is not None and current_hunk is None:
            if line.startswith("new file mode"):
                is_new_file = True
                current_file.status = "added"
                i += 1
                continue

            if line.startswith("deleted file mode"):
                is_deleted_file = True
                current_file.status = "removed"
                i += 1
                continue

            if line.startswith("similarity index") or line.startswith("rename from"):
                is_rename = True
                current_file.status = "renamed"
                i += 1
                continue

            if line.startswith("rename to"):
                # Already handled by diff --git header parsing.
                i += 1
                continue

            if line.startswith("Binary files"):
                current_file.is_binary = True
                i += 1
                continue

            if line.startswith("--- "):
                # Old file path.  "--- /dev/null" for new files.
                if line == "--- /dev/null":
                    is_new_file = True
                    current_file.status = "added"
                i += 1
                continue

            if line.startswith("+++ "):
                # New file path.  "+++ /dev/null" for deleted files.
                if line == "+++ /dev/null":
                    is_deleted_file = True
                    current_file.status = "removed"
                elif not is_new_file:
                    # Extract actual filename from +++ b/path
                    path_match = re.match(r"^\+\+\+ b/(.*)$", line)
                    if path_match:
                        current_file.filename = path_match.group(1)
                        current_file.language = _detect_language(current_file.filename)
                i += 1
                continue

            if line.startswith("index ") or line.startswith("old mode") or line.startswith("new mode"):
                i += 1
                continue

        # --- Hunk header ---
        if current_file is not None and line.startswith("@@"):
            hunk_match = _HUNK_HEADER_RE.match(line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
                new_start = int(hunk_match.group(3))
                new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1
                func_ctx_raw = (hunk_match.group(5) or "").strip()
                func_ctx = func_ctx_raw if func_ctx_raw else None

                # The @@ header itself counts as a diff position.
                diff_position += 1

                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=line,
                    function_context=func_ctx,
                )
                current_file.hunks.append(current_hunk)
                old_lineno = old_start - 1  # will be incremented on first context/remove line
                new_lineno = new_start - 1  # will be incremented on first context/add line

            i += 1
            continue

        # --- Diff body lines ---
        if current_file is not None and current_hunk is not None:
            # Skip "\ No newline at end of file" — does not affect position.
            if line.startswith("\\ No newline at end of file"):
                i += 1
                continue

            # Skip bare empty lines — these are not part of the actual diff.
            # Valid diff lines always start with '+', '-', or ' ' (space).
            if line == "":
                i += 1
                continue

            diff_position += 1

            if line.startswith("+"):
                new_lineno += 1
                current_hunk.lines.append(
                    DiffLine(
                        content=line[1:],  # strip leading +
                        line_type="added",
                        old_lineno=None,
                        new_lineno=new_lineno,
                        diff_position=diff_position,
                    )
                )
                current_file.additions += 1

            elif line.startswith("-"):
                old_lineno += 1
                current_hunk.lines.append(
                    DiffLine(
                        content=line[1:],  # strip leading -
                        line_type="removed",
                        old_lineno=old_lineno,
                        new_lineno=None,
                        diff_position=diff_position,
                    )
                )
                current_file.deletions += 1

            else:
                # Context line (starts with a space character).
                old_lineno += 1
                new_lineno += 1
                content = line[1:] if line.startswith(" ") else line
                current_hunk.lines.append(
                    DiffLine(
                        content=content,
                        line_type="context",
                        old_lineno=old_lineno,
                        new_lineno=new_lineno,
                        diff_position=diff_position,
                    )
                )

        i += 1

    # Don't forget the last file.
    if current_file is not None:
        files.append(current_file)

    # Apply rename status where detected.
    for f in files:
        if is_rename and f.old_filename is not None and f.status == "modified":
            f.status = "renamed"

    logger.debug(
        "Parsed diff: %d files (%d added, %d modified, %d removed)",
        len(files),
        sum(1 for f in files if f.status == "added"),
        sum(1 for f in files if f.status == "modified"),
        sum(1 for f in files if f.status == "removed"),
    )

    return files


# =============================================================================
#  Position map builder
# =============================================================================

def build_line_to_position_map(file_diff: FileDiff) -> dict[int, int]:
    """Build a mapping from new-file line numbers to GitHub diff positions.

    Only added (+) and context ( ) lines are commentable — removed lines
    have no new_lineno and cannot receive PR comments.

    Args:
        file_diff: A parsed ``FileDiff`` object.

    Returns:
        ``{new_file_line_number: diff_position}`` for all commentable lines.
    """
    return {
        line.new_lineno: line.diff_position
        for hunk in file_diff.hunks
        for line in hunk.lines
        if line.line_type in ("added", "context") and line.new_lineno is not None
    }
