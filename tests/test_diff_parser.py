"""Comprehensive tests for the unified diff parser.

Tests cover all edge cases from ADR-0010:
- Simple modification (1 file, 1 hunk)
- New file (all additions)
- Deleted file (all removals)
- Renamed file
- Multi-hunk file (3 hunks)
- Multi-file diff (4 files)
- Binary file detection
- No newline at end of file
- Function context extraction
- Line-to-position mapping accuracy
- Empty diff
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.diff_parser import (
    FileDiff,
    build_line_to_position_map,
    parse_diff,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "diffs"


def _load_fixture(name: str) -> str:
    """Load a diff fixture file."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# =============================================================================
#  Basic parsing tests
# =============================================================================


class TestSimpleModification:
    """Test parsing a simple single-hunk modification."""

    def test_parses_one_file(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert len(files) == 1

    def test_filename(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert files[0].filename == "app/utils.py"

    def test_status_modified(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert files[0].status == "modified"

    def test_language_python(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert files[0].language == "python"

    def test_has_one_hunk(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert len(files[0].hunks) == 1

    def test_additions_and_deletions(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        assert files[0].additions >= 1
        assert files[0].deletions >= 1

    def test_diff_positions_are_positive(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert line.diff_position > 0

    def test_function_context_extracted(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        hunk = files[0].hunks[0]
        assert hunk.function_context == "def process_data(items):"


class TestNewFile:
    """Test parsing a new file diff (all additions)."""

    def test_status_added(self) -> None:
        files = parse_diff(_load_fixture("new_file.diff"))
        assert len(files) == 1
        assert files[0].status == "added"

    def test_all_lines_are_additions(self) -> None:
        files = parse_diff(_load_fixture("new_file.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert line.line_type == "added"

    def test_no_old_linenos(self) -> None:
        files = parse_diff(_load_fixture("new_file.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert line.old_lineno is None

    def test_new_linenos_sequential(self) -> None:
        files = parse_diff(_load_fixture("new_file.diff"))
        linenos = [
            line.new_lineno
            for hunk in files[0].hunks
            for line in hunk.lines
            if line.new_lineno is not None
        ]
        assert linenos == list(range(1, len(linenos) + 1))

    def test_deletions_zero(self) -> None:
        files = parse_diff(_load_fixture("new_file.diff"))
        assert files[0].deletions == 0


class TestDeletedFile:
    """Test parsing a deleted file diff (all removals)."""

    def test_status_removed(self) -> None:
        files = parse_diff(_load_fixture("deleted_file.diff"))
        assert len(files) == 1
        assert files[0].status == "removed"

    def test_all_lines_are_removals(self) -> None:
        files = parse_diff(_load_fixture("deleted_file.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert line.line_type == "removed"

    def test_no_new_linenos(self) -> None:
        files = parse_diff(_load_fixture("deleted_file.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert line.new_lineno is None

    def test_additions_zero(self) -> None:
        files = parse_diff(_load_fixture("deleted_file.diff"))
        assert files[0].additions == 0


class TestRenamedFile:
    """Test parsing a renamed file with modifications."""

    def test_status_renamed(self) -> None:
        files = parse_diff(_load_fixture("renamed_file.diff"))
        assert len(files) == 1
        assert files[0].status == "renamed"

    def test_old_filename_present(self) -> None:
        files = parse_diff(_load_fixture("renamed_file.diff"))
        assert files[0].old_filename is not None
        assert "old_name" in files[0].old_filename

    def test_new_filename(self) -> None:
        files = parse_diff(_load_fixture("renamed_file.diff"))
        assert "new_name" in files[0].filename


class TestMultiHunk:
    """Test parsing a file with multiple hunks."""

    def test_has_three_hunks(self) -> None:
        files = parse_diff(_load_fixture("multi_hunk.diff"))
        assert len(files) == 1
        assert len(files[0].hunks) == 3

    def test_positions_are_cumulative(self) -> None:
        """diff_position must cumulate across hunks (not reset per hunk)."""
        files = parse_diff(_load_fixture("multi_hunk.diff"))
        all_positions = [
            line.diff_position
            for hunk in files[0].hunks
            for line in hunk.lines
        ]
        # Positions should be strictly increasing.
        for i in range(1, len(all_positions)):
            assert all_positions[i] > all_positions[i - 1], (
                f"Position {all_positions[i]} is not greater than {all_positions[i - 1]} "
                f"at index {i}"
            )

    def test_hunk_starts_differ(self) -> None:
        files = parse_diff(_load_fixture("multi_hunk.diff"))
        starts = [h.new_start for h in files[0].hunks]
        assert len(set(starts)) == len(starts), "Hunk starts should be unique"


class TestMultiFile:
    """Test parsing a diff with multiple files."""

    def test_parses_four_files(self) -> None:
        files = parse_diff(_load_fixture("multi_file.diff"))
        assert len(files) == 4

    def test_has_new_file(self) -> None:
        files = parse_diff(_load_fixture("multi_file.diff"))
        added = [f for f in files if f.status == "added"]
        assert len(added) >= 1

    def test_filenames_are_distinct(self) -> None:
        files = parse_diff(_load_fixture("multi_file.diff"))
        names = [f.filename for f in files]
        assert len(set(names)) == len(names)


class TestBinaryFile:
    """Test binary file detection."""

    def test_is_binary(self) -> None:
        files = parse_diff(_load_fixture("binary_file.diff"))
        assert len(files) == 1
        assert files[0].is_binary is True

    def test_no_hunks(self) -> None:
        files = parse_diff(_load_fixture("binary_file.diff"))
        assert len(files[0].hunks) == 0


class TestNoNewline:
    """Test handling of 'No newline at end of file' markers."""

    def test_parses_without_error(self) -> None:
        files = parse_diff(_load_fixture("no_newline.diff"))
        assert len(files) == 1

    def test_marker_not_in_lines(self) -> None:
        """The '\\' marker should not appear as a diff line."""
        files = parse_diff(_load_fixture("no_newline.diff"))
        for hunk in files[0].hunks:
            for line in hunk.lines:
                assert "No newline at end of file" not in line.content


class TestFunctionContext:
    """Test function context extraction from @@ headers."""

    def test_function_name_extracted(self) -> None:
        files = parse_diff(_load_fixture("function_context.diff"))
        assert len(files) == 1
        hunk = files[0].hunks[0]
        assert hunk.function_context is not None
        assert "verify_token" in hunk.function_context


class TestEmptyDiff:
    """Test edge case: empty diff."""

    def test_empty_string(self) -> None:
        assert parse_diff("") == []

    def test_whitespace_only(self) -> None:
        assert parse_diff("   \n\n  ") == []


# =============================================================================
#  Line-to-position mapping
# =============================================================================


class TestLineToPositionMap:
    """Test build_line_to_position_map() accuracy."""

    def test_simple_modification_has_entries(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        position_map = build_line_to_position_map(files[0])
        assert len(position_map) > 0

    def test_all_values_positive(self) -> None:
        files = parse_diff(_load_fixture("simple_modification.diff"))
        position_map = build_line_to_position_map(files[0])
        for line_no, pos in position_map.items():
            assert line_no > 0, f"Line number must be positive, got {line_no}"
            assert pos > 0, f"Position must be positive, got {pos}"

    def test_new_file_all_lines_mapped(self) -> None:
        """For a new file, every line should be commentable."""
        files = parse_diff(_load_fixture("new_file.diff"))
        position_map = build_line_to_position_map(files[0])
        assert len(position_map) == files[0].additions

    def test_deleted_file_empty_map(self) -> None:
        """For a deleted file, no lines are commentable (all are removed)."""
        files = parse_diff(_load_fixture("deleted_file.diff"))
        position_map = build_line_to_position_map(files[0])
        assert len(position_map) == 0

    def test_multi_hunk_positions_increase(self) -> None:
        """Positions in the map should reflect cumulative counting."""
        files = parse_diff(_load_fixture("multi_hunk.diff"))
        position_map = build_line_to_position_map(files[0])
        sorted_items = sorted(position_map.items(), key=lambda x: x[1])
        for i in range(1, len(sorted_items)):
            assert sorted_items[i][1] > sorted_items[i - 1][1]
