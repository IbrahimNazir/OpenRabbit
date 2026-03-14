"""Unit tests for incremental PR-time chunk indexing (PRIndexer).

Tests ADR-0031: incremental indexing that only embeds chunks overlapping with
changed hunks. Unchanged chunks at the same commit SHA are skipped to keep
per-PR embedding costs near zero after initial indexing.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.diff_parser import DiffHunk, DiffLine, FileDiff
from app.parsing.chunk_extractor import CodeChunk
from app.rag.embedder import EmbedStats
from app.rag.pr_indexer import PRIndexer


logger = logging.getLogger(__name__)


# =============================================================================
#  Fixtures
# =============================================================================


@pytest.fixture
def mock_embedding_service() -> AsyncMock:
    """Mock EmbeddingService with pre-configured returns.

    Returns a service that:
    - Upserts chunks successfully
    - Returns existing chunk IDs
    - Deletes files without error
    """
    service = AsyncMock()
    service.upsert_chunks = AsyncMock(
        return_value=EmbedStats(chunks_upserted=5, cache_hits=2, cache_misses=3)
    )
    service.delete_file_chunks = AsyncMock(return_value=None)
    service.scroll_chunk_ids_for_file = AsyncMock(return_value=set())
    return service


@pytest.fixture
def mock_github_client() -> AsyncMock:
    """Mock GitHubClient for file content retrieval."""
    client = AsyncMock()
    client.get_file_content = AsyncMock(
        return_value="def test_func():\n    return 42\n"
    )
    return client


@pytest.fixture
async def pr_indexer(
    mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> PRIndexer:
    """Create PRIndexer with mocked dependencies."""
    return PRIndexer(
        embedding_service=mock_embedding_service, github_client=mock_github_client
    )


# =============================================================================
#  Tests: index_pr_changes
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_modified_file(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test indexing a modified file with changed hunks.

    Verifies that:
    - File content is fetched from GitHub
    - Chunks are extracted
    - Only chunks overlapping with hunks are indexed
    - Statistics are returned
    """
    # Arrange: create a modified file diff with one hunk
    hunk = DiffHunk(
        old_start=10,
        old_count=5,
        new_start=10,
        new_count=6,
        header="@@ -10,5 +10,6 @@ def test_func():",
        function_context="def test_func():",
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/module.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk],
    )

    # Mock chunk extraction to return chunks overlapping the hunk
    mock_chunks = [
        CodeChunk(
            chunk_id="chunk001",
            file_path="app/module.py",
            chunk_type="function",
            name="test_func",
            content="def test_func():\n    return 42\n",
            start_line=9,
            end_line=12,
            language="python",
        ),
    ]

    with patch("app.rag.pr_indexer.extract_chunks", return_value=mock_chunks):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert
    assert result == 5  # EmbedStats.chunks_upserted
    mock_github_client.get_file_content.assert_called_once_with(
        "owner/repo", "app/module.py", "abc123def456"
    )
    mock_embedding_service.upsert_chunks.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_added_file(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test indexing a newly added file.

    Verifies that newly added files are processed correctly.
    """
    # Arrange: new file with all additions
    hunk = DiffHunk(
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=10,
        header="@@ -0,0 +1,10 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/newmodule.py",
        old_filename=None,
        language="python",
        status="added",
        hunks=[hunk],
    )

    mock_chunks = [
        CodeChunk(
            chunk_id="chunk002",
            file_path="app/newmodule.py",
            chunk_type="file_header",
            name="<file_header>",
            content="# new module\ndef new_func():\n    pass\n",
            start_line=1,
            end_line=10,
            language="python",
        ),
    ]

    with patch("app.rag.pr_indexer.extract_chunks", return_value=mock_chunks):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert
    assert result == 5
    mock_embedding_service.upsert_chunks.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_removed_file(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock
) -> None:
    """Test handling of file removal.

    Verifies that removed files have their chunks deleted from the index.
    """
    # Arrange: deleted file
    hunk = DiffHunk(
        old_start=1,
        old_count=10,
        new_start=0,
        new_count=0,
        header="@@ -1,10 +0,0 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/oldmodule.py",
        old_filename=None,
        language="python",
        status="removed",
        hunks=[hunk],
    )

    # Act
    result = await pr_indexer.index_pr_changes(
        file_diffs=[file_diff],
        repo_id=123,
        head_sha="abc123def456",
        repo_full_name="owner/repo",
    )

    # Assert
    assert result == 0  # No chunks upserted for removed file
    mock_embedding_service.delete_file_chunks.assert_called_once_with(
        123, "app/oldmodule.py"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_renamed_file(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test indexing a renamed file with modifications.

    Verifies that renamed files are treated as added for indexing purposes.
    """
    # Arrange: renamed file
    hunk = DiffHunk(
        old_start=5,
        old_count=3,
        new_start=5,
        new_count=4,
        header="@@ -5,3 +5,4 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/new_name.py",
        old_filename="app/old_name.py",
        language="python",
        status="renamed",
        hunks=[hunk],
    )

    mock_chunks = [
        CodeChunk(
            chunk_id="chunk003",
            file_path="app/new_name.py",
            chunk_type="function",
            name="renamed_func",
            content="def renamed_func():\n    return 1\n",
            start_line=1,
            end_line=8,
            language="python",
        ),
    ]

    with patch("app.rag.pr_indexer.extract_chunks", return_value=mock_chunks):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert
    assert result == 5
    mock_github_client.get_file_content.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skip_already_indexed_at_same_sha(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test that chunks at the same SHA are not re-indexed.

    Verifies the skip optimization: if all changed chunks are already indexed
    at the exact commit SHA, they are not re-embedded.
    """
    # Arrange: file with chunks already indexed
    hunk = DiffHunk(
        old_start=1,
        old_count=5,
        new_start=1,
        new_count=5,
        header="@@ -1,5 +1,5 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/module.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk],
    )

    mock_chunks = [
        CodeChunk(
            chunk_id="chunk001",
            file_path="app/module.py",
            chunk_type="function",
            name="test_func",
            content="def test_func():\n    pass\n",
            start_line=1,
            end_line=5,
            language="python",
        ),
    ]

    # Mock: chunks already exist at this SHA
    mock_embedding_service.scroll_chunk_ids_for_file = AsyncMock(
        return_value={"chunk001"}  # chunk is already indexed
    )

    with patch("app.rag.pr_indexer.extract_chunks", return_value=mock_chunks):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert: no upsert happens because chunk is already current
    assert result == 0
    mock_embedding_service.upsert_chunks.assert_not_called()
    mock_embedding_service.scroll_chunk_ids_for_file.assert_called_once_with(
        123, "app/module.py", "abc123def456"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_no_overlapping_chunks(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test file with hunks but no overlapping chunks.

    When changed hunks exist but no chunks overlap with them, indexing
    should skip the file.
    """
    # Arrange: hunks that don't overlap with chunk locations
    hunk = DiffHunk(
        old_start=50,
        old_count=5,
        new_start=50,
        new_count=5,
        header="@@ -50,5 +50,5 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/module.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk],
    )

    # Mock chunks far away from the hunk
    mock_chunks = [
        CodeChunk(
            chunk_id="chunk001",
            file_path="app/module.py",
            chunk_type="function",
            name="early_func",
            content="def early_func():\n    pass\n",
            start_line=1,
            end_line=10,
            language="python",
        ),
    ]

    with patch("app.rag.pr_indexer.extract_chunks", return_value=mock_chunks):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert: no chunks indexed because none overlap
    assert result == 0
    mock_embedding_service.upsert_chunks.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_multiple_files(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test indexing PR with changes to multiple files.

    Verifies that multiple files are processed in sequence and statistics
    are accumulated correctly.
    """
    # Arrange: two modified files
    hunk1 = DiffHunk(
        old_start=1,
        old_count=5,
        new_start=1,
        new_count=6,
        header="@@ -1,5 +1,6 @@",
        function_context=None,
        lines=[],
    )
    hunk2 = DiffHunk(
        old_start=10,
        old_count=3,
        new_start=10,
        new_count=3,
        header="@@ -10,3 +10,3 @@",
        function_context=None,
        lines=[],
    )

    file_diff1 = FileDiff(
        filename="app/file1.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk1],
    )
    file_diff2 = FileDiff(
        filename="app/file2.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk2],
    )

    chunks1 = [
        CodeChunk(
            chunk_id="chunk001",
            file_path="app/file1.py",
            chunk_type="function",
            name="func1",
            content="def func1(): pass",
            start_line=1,
            end_line=6,
            language="python",
        ),
    ]
    chunks2 = [
        CodeChunk(
            chunk_id="chunk002",
            file_path="app/file2.py",
            chunk_type="function",
            name="func2",
            content="def func2(): pass",
            start_line=10,
            end_line=12,
            language="python",
        ),
    ]

    call_count = [0]

    def mock_extract(content: str, path: str) -> list[CodeChunk]:
        call_count[0] += 1
        if "file1" in path:
            return chunks1
        return chunks2

    with patch("app.rag.pr_indexer.extract_chunks", side_effect=mock_extract):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff1, file_diff2],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert: both files processed, chunks accumulated
    assert result == 10  # 5 chunks from each file
    assert mock_embedding_service.upsert_chunks.call_count == 2
    assert call_count[0] == 2


# =============================================================================
#  Tests: _overlaps_hunks
# =============================================================================


@pytest.mark.unit
def test_overlaps_hunks_complete_overlap(pr_indexer: PRIndexer) -> None:
    """Test complete overlap between chunk and hunk.

    When a chunk's line range completely encompasses a hunk, they overlap.
    """
    # Arrange
    hunk = DiffHunk(
        old_start=5,
        old_count=3,
        new_start=5,
        new_count=3,
        header="@@ -5,3 +5,3 @@",
        function_context=None,
        lines=[],
    )
    chunk = CodeChunk(
        chunk_id="chunk001",
        file_path="app/module.py",
        chunk_type="function",
        name="func",
        content="code",
        start_line=1,
        end_line=10,
        language="python",
    )

    # Act
    overlaps = pr_indexer._overlaps_hunks(chunk, [hunk])

    # Assert
    assert overlaps is True


@pytest.mark.unit
def test_overlaps_hunks_partial_overlap(pr_indexer: PRIndexer) -> None:
    """Test partial overlap between chunk and hunk.

    When chunk and hunk ranges partially overlap, they should be considered
    overlapping.
    """
    # Arrange
    hunk = DiffHunk(
        old_start=8,
        old_count=5,
        new_start=8,
        new_count=5,
        header="@@ -8,5 +8,5 @@",
        function_context=None,
        lines=[],
    )
    chunk = CodeChunk(
        chunk_id="chunk001",
        file_path="app/module.py",
        chunk_type="function",
        name="func",
        content="code",
        start_line=5,
        end_line=12,
        language="python",
    )

    # Act
    overlaps = pr_indexer._overlaps_hunks(chunk, [hunk])

    # Assert
    assert overlaps is True


@pytest.mark.unit
def test_overlaps_hunks_no_overlap(pr_indexer: PRIndexer) -> None:
    """Test when chunk and hunk ranges do not overlap.

    When chunk end is before hunk start, they do not overlap.
    """
    # Arrange
    hunk = DiffHunk(
        old_start=50,
        old_count=5,
        new_start=50,
        new_count=5,
        header="@@ -50,5 +50,5 @@",
        function_context=None,
        lines=[],
    )
    chunk = CodeChunk(
        chunk_id="chunk001",
        file_path="app/module.py",
        chunk_type="function",
        name="func",
        content="code",
        start_line=1,
        end_line=10,
        language="python",
    )

    # Act
    overlaps = pr_indexer._overlaps_hunks(chunk, [hunk])

    # Assert
    assert overlaps is False


@pytest.mark.unit
def test_overlaps_hunks_multiple_hunks_second_matches(pr_indexer: PRIndexer) -> None:
    """Test overlap detection with multiple hunks (second matches).

    When a chunk overlaps with one of multiple hunks, it should be detected.
    """
    # Arrange
    hunk1 = DiffHunk(
        old_start=1,
        old_count=5,
        new_start=1,
        new_count=5,
        header="@@ -1,5 +1,5 @@",
        function_context=None,
        lines=[],
    )
    hunk2 = DiffHunk(
        old_start=20,
        old_count=5,
        new_start=20,
        new_count=5,
        header="@@ -20,5 +20,5 @@",
        function_context=None,
        lines=[],
    )
    chunk = CodeChunk(
        chunk_id="chunk001",
        file_path="app/module.py",
        chunk_type="function",
        name="func",
        content="code",
        start_line=18,
        end_line=25,
        language="python",
    )

    # Act
    overlaps = pr_indexer._overlaps_hunks(chunk, [hunk1, hunk2])

    # Assert: overlaps with hunk2
    assert overlaps is True


@pytest.mark.unit
def test_overlaps_hunks_single_line_hunk(pr_indexer: PRIndexer) -> None:
    """Test overlap with a single-line hunk.

    Hunks can cover just one line (old_count=1 or new_count=1).
    """
    # Arrange
    hunk = DiffHunk(
        old_start=10,
        old_count=1,
        new_start=10,
        new_count=1,
        header="@@ -10,1 +10,1 @@",
        function_context=None,
        lines=[],
    )
    chunk = CodeChunk(
        chunk_id="chunk001",
        file_path="app/module.py",
        chunk_type="function",
        name="func",
        content="code",
        start_line=8,
        end_line=12,
        language="python",
    )

    # Act
    overlaps = pr_indexer._overlaps_hunks(chunk, [hunk])

    # Assert
    assert overlaps is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_github_fetch_failure(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test graceful handling of GitHub API failures.

    When file content fetch fails, the file should be skipped and indexing
    should continue with other files.
    """
    # Arrange: GitHub fetch fails
    hunk = DiffHunk(
        old_start=1,
        old_count=5,
        new_start=1,
        new_count=5,
        header="@@ -1,5 +1,5 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/module.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk],
    )

    mock_github_client.get_file_content = AsyncMock(
        side_effect=Exception("GitHub API error")
    )

    # Act
    result = await pr_indexer.index_pr_changes(
        file_diffs=[file_diff],
        repo_id=123,
        head_sha="abc123def456",
        repo_full_name="owner/repo",
    )

    # Assert: file skipped, returns 0
    assert result == 0
    mock_embedding_service.upsert_chunks.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_pr_chunk_extraction_failure(
    pr_indexer: PRIndexer, mock_embedding_service: AsyncMock, mock_github_client: AsyncMock
) -> None:
    """Test graceful handling of chunk extraction failures.

    When chunk extraction fails, the file should be skipped.
    """
    # Arrange
    hunk = DiffHunk(
        old_start=1,
        old_count=5,
        new_start=1,
        new_count=5,
        header="@@ -1,5 +1,5 @@",
        function_context=None,
        lines=[],
    )
    file_diff = FileDiff(
        filename="app/module.py",
        old_filename=None,
        language="python",
        status="modified",
        hunks=[hunk],
    )

    with patch("app.rag.pr_indexer.extract_chunks", side_effect=Exception("Parse error")):
        # Act
        result = await pr_indexer.index_pr_changes(
            file_diffs=[file_diff],
            repo_id=123,
            head_sha="abc123def456",
            repo_full_name="owner/repo",
        )

    # Assert
    assert result == 0
    mock_embedding_service.upsert_chunks.assert_not_called()
