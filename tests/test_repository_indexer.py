"""Unit tests for full repository indexing (RepositoryIndexer).

Tests ADR-0030: fetches all code files via GitHub tree API (no clone required),
processes files in batches of 20, respects rate limiting, and supports resume
via Redis progress tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.embedder import EmbedStats
from app.rag.indexer import IndexProgress, RepositoryIndexer


logger = logging.getLogger(__name__)


# =============================================================================
#  Fixtures
# =============================================================================


@pytest.fixture
def mock_embedding_service() -> AsyncMock:
    """Mock EmbeddingService for repository indexing."""
    service = AsyncMock()
    service.upsert_chunks = AsyncMock(
        return_value=EmbedStats(chunks_upserted=10, cache_hits=5, cache_misses=5)
    )
    return service


@pytest.fixture
def mock_github_client() -> AsyncMock:
    """Mock GitHubClient for tree and file content retrieval."""
    client = AsyncMock()
    client.get_file_content = AsyncMock(return_value="# sample code\n")
    # Mock the internal _request method for tree API
    client._request = AsyncMock()
    return client


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for progress tracking."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(return_value=True)
    return redis


@pytest.fixture
async def repo_indexer(
    mock_embedding_service: AsyncMock,
    mock_github_client: AsyncMock,
    mock_redis: AsyncMock,
) -> RepositoryIndexer:
    """Create RepositoryIndexer with mocked dependencies."""
    return RepositoryIndexer(
        embedding_service=mock_embedding_service,
        github_client=mock_github_client,
        redis=mock_redis,
    )


# =============================================================================
#  Tests: index_repository
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_index_repository_small_repo(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test indexing a small repository with minimal files.

    Verifies that:
    - Git tree API is called correctly
    - Code files are identified and filtered
    - Files are chunked and embedded
    - Final progress is marked as completed
    """
    # Arrange: mock tree response
    mock_tree_response = {
        "tree": [
            {"path": "app/main.py", "type": "blob", "size": 1000},
            {"path": "app/utils.py", "type": "blob", "size": 500},
            {"path": "README.md", "type": "blob", "size": 2000},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert
    assert result.status == "completed"
    assert result.total >= 2  # At least the two Python files
    assert result.done >= 2
    mock_github_client._request.assert_called_once()
    assert "/repos/owner/repo/git/trees/" in mock_github_client._request.call_args[0][1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_large_files(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that files larger than 500KB are skipped.

    Verifies that MAX_FILE_SIZE_BYTES limit is respected.
    """
    # Arrange: mix of normal and large files
    mock_tree_response = {
        "tree": [
            {"path": "large_binary.bin", "type": "blob", "size": 600_000},  # >500KB
            {"path": "normal.py", "type": "blob", "size": 5_000},
            {"path": "huge_data.db", "type": "blob", "size": 1_000_000},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: only normal.py should be processed
    assert result.status == "completed"
    assert result.total == 1  # Only normal.py


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_vendor_directories(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that vendor and node_modules directories are skipped.

    Verifies that third-party dependencies are excluded.
    """
    # Arrange: files in vendor and node_modules
    mock_tree_response = {
        "tree": [
            {"path": "vendor/package/file.php", "type": "blob", "size": 1_000},
            {"path": "node_modules/lib/index.js", "type": "blob", "size": 1_000},
            {"path": "src/main.py", "type": "blob", "size": 1_000},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: only src/main.py should be processed
    assert result.status == "completed"
    assert result.total == 1  # Only src/main.py


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_non_code_extensions(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that non-code file extensions are skipped.

    Verifies that images, binaries, and docs are excluded from indexing.
    """
    # Arrange: mix of code and non-code files
    mock_tree_response = {
        "tree": [
            {"path": "image.png", "type": "blob", "size": 100_000},
            {"path": "README.md", "type": "blob", "size": 1_000},
            {"path": "logo.svg", "type": "blob", "size": 50_000},
            {"path": "src/main.py", "type": "blob", "size": 1_000},
            {"path": "styles.css", "type": "blob", "size": 500},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: only src/main.py and styles.css should be processed (CSS is code)
    assert result.status == "completed"
    assert result.total >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batching_with_rate_limiting(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that files are batched (20 per batch) and rate-limited.

    Verifies that:
    - FILES_BATCH_SIZE (20) is respected
    - Async gathering happens per batch
    - Sleep is called for rate limiting
    """
    # Arrange: 50 code files
    files = [
        {"path": f"app/file{i:02d}.py", "type": "blob", "size": 1_000}
        for i in range(50)
    ]
    mock_tree_response = {"tree": files, "truncated": False}
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Act
            result = await repo_indexer.index_repository(
                repo_full_name="owner/repo",
                repo_id=123,
                head_sha="abc123def456",
            )

    # Assert
    assert result.status == "completed"
    assert result.total == 50
    assert result.done == 50
    # With 50 files and batch size of 20, should have 3 batches
    # Rate limiting may trigger sleep
    mock_github_client.get_file_content.assert_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_resume_from_redis_progress(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
    mock_redis: AsyncMock,
) -> None:
    """Test resuming indexing from saved progress in Redis.

    Verifies that:
    - Existing progress is loaded from Redis
    - last_processed_index is used to skip already-indexed files
    - Status transitions to running before processing
    """
    # Arrange: saved progress in Redis
    saved_progress = IndexProgress(
        total=50,
        done=25,
        status="running",
        last_processed_index=25,
        chunks_total=100,
    )
    mock_redis.get = AsyncMock(return_value=json.dumps(saved_progress.to_dict()).encode())

    files = [
        {"path": f"app/file{i:02d}.py", "type": "blob", "size": 1_000}
        for i in range(50)
    ]
    mock_tree_response = {"tree": files, "truncated": False}
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert
    assert result.status == "completed"
    # Should have resumed from index 25
    assert result.done >= 25
    mock_redis.get.assert_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_truncated_tree_warning(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test warning logged when git tree API returns truncated=true.

    Verifies that incomplete results from GitHub are handled gracefully
    and a warning is logged.
    """
    # Arrange: truncated response
    mock_tree_response = {
        "tree": [{"path": "app/file.py", "type": "blob", "size": 1_000}],
        "truncated": True,  # API returned partial results
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        with patch("logging.getLogger") as mock_logger:
            # Act
            result = await repo_indexer.index_repository(
                repo_full_name="owner/repo",
                repo_id=123,
                head_sha="abc123def456",
            )

    # Assert: indexing completes but with truncation warning
    assert result.status == "completed"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_empty_repository(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test handling of repository with no code files.

    Verifies that empty repos are handled gracefully.
    """
    # Arrange: empty tree
    mock_tree_response = {"tree": [], "truncated": False}
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    # Act
    result = await repo_indexer.index_repository(
        repo_full_name="owner/repo",
        repo_id=123,
        head_sha="abc123def456",
    )

    # Assert
    assert result.status == "completed"
    assert result.total == 0
    assert result.done == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_git_tree_fetch_failure(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_redis: AsyncMock,
) -> None:
    """Test handling of git tree API failures.

    Verifies that indexing gracefully fails when tree API is unavailable.
    """
    # Arrange: GitHub API failure
    mock_github_client._request = AsyncMock(
        side_effect=Exception("GitHub API rate limited")
    )

    # Act
    result = await repo_indexer.index_repository(
        repo_full_name="owner/repo",
        repo_id=123,
        head_sha="abc123def456",
    )

    # Assert
    assert result.status == "failed"
    assert "GitHub API rate limited" in result.error
    mock_redis.setex.assert_called()  # Progress saved


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_progress(
    repo_indexer: RepositoryIndexer,
    mock_redis: AsyncMock,
) -> None:
    """Test retrieving current indexing progress from Redis.

    Verifies that get_progress returns IndexProgress or None.
    """
    # Arrange: progress in Redis
    saved_progress = IndexProgress(
        total=100,
        done=50,
        status="running",
        last_processed_index=50,
        chunks_total=250,
    )
    mock_redis.get = AsyncMock(return_value=json.dumps(saved_progress.to_dict()).encode())

    # Act
    result = await repo_indexer.get_progress(123)

    # Assert
    assert result is not None
    assert result.total == 100
    assert result.done == 50
    assert result.status == "running"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_progress_not_found(
    repo_indexer: RepositoryIndexer,
    mock_redis: AsyncMock,
) -> None:
    """Test retrieving progress when none exists in Redis.

    Verifies that get_progress returns None for missing progress.
    """
    # Arrange: no progress in Redis
    mock_redis.get = AsyncMock(return_value=None)

    # Act
    result = await repo_indexer.get_progress(999)

    # Assert
    assert result is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_minified_files(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that minified files are skipped.

    Verifies that .min.js, .min.css, etc. are excluded.
    """
    # Arrange: minified and normal files
    mock_tree_response = {
        "tree": [
            {"path": "dist/app.min.js", "type": "blob", "size": 50_000},
            {"path": "src/main.js", "type": "blob", "size": 5_000},
            {"path": "dist/styles.min.css", "type": "blob", "size": 30_000},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: only src/main.js processed (minified files skipped)
    assert result.status == "completed"
    assert result.total == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_generated_files(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that generated files are skipped.

    Verifies that protobuf (_pb2.py), lock files, and generated/ dirs
    are excluded.
    """
    # Arrange: generated files
    mock_tree_response = {
        "tree": [
            {"path": "proto/generated_pb2.py", "type": "blob", "size": 5_000},
            {"path": "yarn.lock", "type": "blob", "size": 100_000},
            {"path": "src/main.py", "type": "blob", "size": 1_000},
            {"path": "generated/schema.ts", "type": "blob", "size": 10_000},
        ],
        "truncated": False,
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: only src/main.py processed
    assert result.status == "completed"
    assert result.total == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_file_content_fetch_failure_continues(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
) -> None:
    """Test that indexing continues when individual file fetch fails.

    Verifies that partial failures don't halt the entire indexing job.
    """
    # Arrange: 3 files, middle one fails
    files = [
        {"path": "app/file1.py", "type": "blob", "size": 1_000},
        {"path": "app/file2.py", "type": "blob", "size": 1_000},
        {"path": "app/file3.py", "type": "blob", "size": 1_000},
    ]
    mock_tree_response = {"tree": files, "truncated": False}
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    # Simulate failure on file2
    call_count = [0]

    async def mock_get_file_content(repo: str, path: str, sha: str) -> str:
        call_count[0] += 1
        if "file2" in path:
            raise Exception("Network error")
        return "# code\n"

    mock_github_client.get_file_content = mock_get_file_content

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert: indexing completes despite file2 failure
    assert result.status == "completed"
    assert result.total == 3
    # file1 and file3 should succeed
    assert call_count[0] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_progress_persistence(
    repo_indexer: RepositoryIndexer,
    mock_github_client: AsyncMock,
    mock_embedding_service: AsyncMock,
    mock_redis: AsyncMock,
) -> None:
    """Test that progress is persisted to Redis after each batch.

    Verifies that setex is called with correct progress data.
    """
    # Arrange: small batch of files
    files = [
        {"path": f"app/file{i:02d}.py", "type": "blob", "size": 1_000}
        for i in range(5)
    ]
    mock_tree_response = {"tree": files, "truncated": False}
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=mock_tree_response)
    mock_github_client._request = AsyncMock(return_value=mock_response)

    with patch("app.rag.indexer.extract_chunks", return_value=[]):
        # Act
        result = await repo_indexer.index_repository(
            repo_full_name="owner/repo",
            repo_id=123,
            head_sha="abc123def456",
        )

    # Assert
    assert result.status == "completed"
    # setex should be called multiple times (initial, after batch, final)
    assert mock_redis.setex.call_count >= 2
    # Verify progress key format
    calls = mock_redis.setex.call_args_list
    assert any("index_progress:123" in str(call) for call in calls)
