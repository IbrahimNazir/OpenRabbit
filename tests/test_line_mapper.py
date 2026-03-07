"""Tests for build_line_to_position_map — 10 real-world diff scenarios.

Implements the 20-day plan Task 10.2.

GitHub's PR Review API requires a `position` parameter that is the
1-indexed count of lines within the file diff (counting the @@ header
as position 1, then every subsequent line regardless of type).

Only added (+) and context ( ) lines are commentable — removed lines
have no new_lineno and thus no position map entry.
"""

from __future__ import annotations

import pytest

from app.core.diff_parser import build_line_to_position_map, parse_diff


# =============================================================================
#  Helper
# =============================================================================


def _get_map(diff_text: str, filename: str = "file.py") -> dict[int, int]:
    """Parse a diff and return the position map for the first matching file."""
    files = parse_diff(diff_text)
    for f in files:
        if filename in f.filename:
            return build_line_to_position_map(f)
    # Return map for first file if filename not found
    if files:
        return build_line_to_position_map(files[0])
    return {}


# =============================================================================
#  Scenario 1: New file — all additions
# =============================================================================


def test_new_file_all_additions() -> None:
    """A new file: every line is an addition, positions start at 2 (after @@ header)."""
    diff = """\
diff --git a/new_file.py b/new_file.py
new file mode 100644
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+line one
+line two
+line three
"""
    pos_map = _get_map(diff, "new_file.py")
    # @@ header = position 1; line 1 ("+line one") = position 2
    assert pos_map[1] == 2
    assert pos_map[2] == 3
    assert pos_map[3] == 4
    # No more lines
    assert 4 not in pos_map


# =============================================================================
#  Scenario 2: Deletions only — no commentable new lines
# =============================================================================


def test_deletions_only() -> None:
    """A file where all changes are deletions — position map should be empty."""
    diff = """\
diff --git a/del_file.py b/del_file.py
--- a/del_file.py
+++ b/del_file.py
@@ -1,3 +1,0 @@
-line one
-line two
-line three
"""
    pos_map = _get_map(diff, "del_file.py")
    # Removed lines have no new_lineno — position map is empty
    assert pos_map == {}


# =============================================================================
#  Scenario 3: Multiple hunks far apart
# =============================================================================


def test_multiple_hunks_far_apart() -> None:
    """Two hunks with large line gap — positions are cumulative across the full diff."""
    diff = """\
diff --git a/multi.py b/multi.py
--- a/multi.py
+++ b/multi.py
@@ -1,2 +1,3 @@
 context line 1
+new line A
 context line 2
@@ -50,2 +51,3 @@
 context line 50
+new line B
 context line 51
"""
    pos_map = _get_map(diff, "multi.py")

    # First hunk: @@ = pos 1, "context line 1" = pos 2, "+new line A" = pos 3,
    # "context line 2" = pos 4
    # Second hunk @@ = pos 5, "context line 50" = pos 6, "+new line B" = pos 7,
    # "context line 51" = pos 8

    # Context line 1 is at new_lineno=1 → position 2
    assert pos_map.get(1) == 2
    # New line A is at new_lineno=2 → position 3
    assert pos_map.get(2) == 3
    # Context line 2 is at new_lineno=3 → position 4
    assert pos_map.get(3) == 4

    # Context line at new_lineno=51 → position 6
    assert pos_map.get(51) == 6
    # New line B at new_lineno=52 → position 7
    assert pos_map.get(52) == 7
    # Context line 51 at new_lineno=53 → position 8
    assert pos_map.get(53) == 8


# =============================================================================
#  Scenario 4: Adjacent hunks (positions continue)
# =============================================================================


def test_adjacent_hunks() -> None:
    """Hunks that are right next to each other — positions must be contiguous."""
    diff = """\
diff --git a/adj.py b/adj.py
--- a/adj.py
+++ b/adj.py
@@ -1,1 +1,2 @@
+added line 1
 original line 1
@@ -2,1 +3,2 @@
 original line 2
+added line 2
"""
    pos_map = _get_map(diff, "adj.py")

    # First hunk: @@ = pos 1, "+added line 1" = pos 2, " original line 1" = pos 3
    # Second hunk: @@ = pos 4, " original line 2" = pos 5, "+added line 2" = pos 6

    assert pos_map.get(1) == 2   # added line 1 → new_lineno=1
    assert pos_map.get(2) == 3   # original line 1 → new_lineno=2
    assert pos_map.get(3) == 5   # original line 2 → new_lineno=3
    assert pos_map.get(4) == 6   # added line 2 → new_lineno=4


# =============================================================================
#  Scenario 5: Renamed file
# =============================================================================


def test_renamed_file() -> None:
    """A renamed file (similarity index) — diff positions work the same as modified."""
    diff = """\
diff --git a/old_name.py b/new_name.py
similarity index 80%
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1,2 +1,3 @@
 unchanged line
+new line
 another unchanged
"""
    pos_map = _get_map(diff, "new_name.py")
    # @@ = pos 1; "unchanged line" = pos 2; "+new line" = pos 3; "another unchanged" = pos 4
    assert pos_map.get(1) == 2
    assert pos_map.get(2) == 3
    assert pos_map.get(3) == 4


# =============================================================================
#  Scenario 6: Context lines only (no additions or removals)
# =============================================================================


def test_context_lines_only() -> None:
    """A hunk with only context lines — all are commentable."""
    diff = """\
diff --git a/ctx.py b/ctx.py
--- a/ctx.py
+++ b/ctx.py
@@ -10,3 +10,3 @@
 line 10
 line 11
 line 12
"""
    pos_map = _get_map(diff, "ctx.py")
    # @@ = pos 1; "line 10" = pos 2; "line 11" = pos 3; "line 12" = pos 4
    assert pos_map.get(10) == 2
    assert pos_map.get(11) == 3
    assert pos_map.get(12) == 4


# =============================================================================
#  Scenario 7: No newline at end of file marker
# =============================================================================


def test_no_newline_at_end_marker() -> None:
    r"""The '\ No newline at end of file' line must NOT increment diff position."""
    diff = """\
diff --git a/nonewline.py b/nonewline.py
--- a/nonewline.py
+++ b/nonewline.py
@@ -1,2 +1,3 @@
 line 1
+new line
 line 2
\\ No newline at end of file
"""
    pos_map = _get_map(diff, "nonewline.py")
    # The "\\ No newline" marker is skipped — positions are:
    # @@ = 1, "line 1" = 2, "+new line" = 3, "line 2" = 4
    assert pos_map.get(1) == 2
    assert pos_map.get(2) == 3
    assert pos_map.get(3) == 4
    # No spurious extra entries
    assert 4 not in pos_map


# =============================================================================
#  Scenario 8: Single hunk file
# =============================================================================


def test_single_hunk_small_file() -> None:
    """Simple single-hunk file — verify basic position assignment."""
    diff = """\
diff --git a/simple.py b/simple.py
--- a/simple.py
+++ b/simple.py
@@ -5,3 +5,4 @@
 def foo():
+    x = 1
     return x
 # end
"""
    pos_map = _get_map(diff, "simple.py")
    # @@ = 1, "def foo():" = 2, "    x = 1" = 3, "    return x" = 4, "# end" = 5
    assert pos_map.get(5) == 2    # "def foo():" at new_lineno=5
    assert pos_map.get(6) == 3    # "+    x = 1"
    assert pos_map.get(7) == 4    # "    return x"
    assert pos_map.get(8) == 5    # "# end"


# =============================================================================
#  Scenario 9: Large hunk (many lines)
# =============================================================================


def test_large_hunk() -> None:
    """A hunk with 50 added lines — verify positions are continuous and correct."""
    added_lines = "\n".join(f"+added_{i}" for i in range(1, 51))
    diff = f"""\
diff --git a/large.py b/large.py
--- a/large.py
+++ b/large.py
@@ -0,0 +1,50 @@
{added_lines}
"""
    pos_map = _get_map(diff, "large.py")
    # @@ = pos 1, added_1 = pos 2, ..., added_50 = pos 51
    assert pos_map.get(1) == 2
    assert pos_map.get(25) == 26
    assert pos_map.get(50) == 51
    assert len(pos_map) == 50


# =============================================================================
#  Scenario 10: Mixed adds/removes/context
# =============================================================================


def test_mixed_adds_removes_context() -> None:
    """Mixed hunk: some adds, some removes, some context — only adds+context commentable."""
    diff = """\
diff --git a/mixed.py b/mixed.py
--- a/mixed.py
+++ b/mixed.py
@@ -10,5 +10,5 @@
 context at 10
-removed at 11
+added at 11
 context at 12
-removed at 13
+added at 13
 context at 14
"""
    pos_map = _get_map(diff, "mixed.py")
    # Positions:
    # @@ = 1
    # " context at 10" = 2 → new_lineno=10
    # "-removed at 11" = 3 → no new_lineno
    # "+added at 11" = 4 → new_lineno=11
    # " context at 12" = 5 → new_lineno=12
    # "-removed at 13" = 6 → no new_lineno
    # "+added at 13" = 7 → new_lineno=13
    # " context at 14" = 8 → new_lineno=14

    assert pos_map.get(10) == 2   # context
    assert pos_map.get(11) == 4   # added (skipped removed at position 3)
    assert pos_map.get(12) == 5   # context
    assert pos_map.get(13) == 7   # added
    assert pos_map.get(14) == 8   # context

    # Removed lines have no new_lineno — they must not appear in the map
    # (old_lineno 11 and 13 should not be keys since those map to new positions)
    assert len(pos_map) == 5  # only 5 commentable lines


# =============================================================================
#  Edge case: removed-line positions are not in the map
# =============================================================================


def test_removed_lines_not_commentable() -> None:
    """Removed lines must not appear in the position map (GitHub API constraint)."""
    diff = """\
diff --git a/edge.py b/edge.py
--- a/edge.py
+++ b/edge.py
@@ -1,3 +1,2 @@
 keep line 1
-remove this
 keep line 2
"""
    pos_map = _get_map(diff, "edge.py")
    # "keep line 1" → new_lineno=1, pos=2
    # "-remove this" → no new_lineno (removed)
    # "keep line 2" → new_lineno=2, pos=4
    assert pos_map.get(1) == 2
    assert pos_map.get(2) == 4
    assert len(pos_map) == 2


# =============================================================================
#  Test build_line_to_position_map returns None-safe results
# =============================================================================


def test_empty_diff_returns_empty_map() -> None:
    """Parsing an empty diff produces no files and thus no position map."""
    files = parse_diff("")
    assert files == []


def test_binary_file_not_in_map() -> None:
    """Binary files should be parsed but have no hunks and thus empty position maps."""
    diff = """\
diff --git a/image.png b/image.png
Binary files a/image.png and b/image.png differ
"""
    files = parse_diff(diff)
    assert len(files) == 1
    assert files[0].is_binary
    pos_map = build_line_to_position_map(files[0])
    assert pos_map == {}
