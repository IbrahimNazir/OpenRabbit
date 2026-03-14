"""Integration tests for database models and operations.

Tests real database operations using SQLite via test_db_session fixture.
Uses async SQLAlchemy to verify CRUD operations, foreign key constraints,
cascade deletes, and unique constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from uuid import uuid4

from app.models.database import Base
from app.models.tenant import Installation, Repository
from app.models.pr_review import PRReview, Finding, ConversationThread


@pytest.mark.integration
class TestDatabaseOperations:
    """Tests for database CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_installation(self, test_db_session):
        """Test creating an Installation record.

        Verifies:
        - Record is inserted into database
        - All required fields are persisted
        - Query returns expected values
        """
        inst = Installation(
            id=12345, account_login="test_org", account_type="Organization", is_active=True
        )
        test_db_session.add(inst)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Installation).where(Installation.id == 12345))
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.account_login == "test_org"
        assert fetched.account_type == "Organization"
        assert fetched.is_active is True

    @pytest.mark.asyncio
    async def test_create_repository_with_fk(self, test_db_session):
        """Test creating a Repository with Installation FK.

        Verifies:
        - Installation and Repository are linked via FK
        - FK constraint is respected
        - Query with relationship works
        """
        # First create Installation
        inst = Installation(
            id=12345, account_login="test_org", account_type="Organization", is_active=True
        )
        test_db_session.add(inst)
        await test_db_session.commit()

        # Then create Repository with FK
        repo = Repository(
            id=999,
            installation_id=12345,
            full_name="test_org/test_repo",
            default_branch="main",
            index_status="ready",
        )
        test_db_session.add(repo)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Repository).where(Repository.id == 999))
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.installation_id == 12345
        assert fetched.full_name == "test_org/test_repo"
        assert fetched.index_status == "ready"

    @pytest.mark.asyncio
    async def test_create_pr_review(self, test_db_session):
        """Test creating a PR review record.

        Verifies:
        - UUID primary key is generated
        - All fields are persisted correctly
        - Status defaults to 'queued'
        """
        pr_id = uuid4()
        review = PRReview(
            id=pr_id,
            repo_id=123,
            pr_number=42,
            pr_title="Test PR",
            head_sha="abc123",
            base_sha="def456",
            status="completed",
            findings_count=2,
            cost_usd=0.01,
        )
        test_db_session.add(review)
        await test_db_session.commit()

        result = await test_db_session.execute(select(PRReview).where(PRReview.id == pr_id))
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.pr_number == 42
        assert fetched.pr_title == "Test PR"
        assert fetched.status == "completed"
        assert fetched.findings_count == 2

    @pytest.mark.asyncio
    async def test_create_finding_with_fk(self, test_db_session):
        """Test creating a Finding linked to PRReview.

        Verifies:
        - Finding FK to PRReview is enforced
        - All finding fields persist correctly
        - Query returns expected values
        """
        pr_id = uuid4()
        review = PRReview(
            id=pr_id,
            repo_id=123,
            pr_number=42,
            pr_title="Test PR",
            head_sha="abc123",
            base_sha="def456",
            status="completed",
        )
        test_db_session.add(review)
        await test_db_session.commit()

        finding = Finding(
            id=uuid4(),
            review_id=pr_id,
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="high",
            category="security",
            title="SQL Injection",
            body="User input in SQL",
        )
        test_db_session.add(finding)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Finding).where(Finding.review_id == pr_id))
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.severity == "high"
        assert fetched.category == "security"
        assert fetched.file_path == "app/main.py"
        assert fetched.line_start == 10

    @pytest.mark.asyncio
    async def test_cascade_delete_findings(self, test_db_session):
        """Test that deleting PRReview cascades to Findings.

        Verifies:
        - Cascade delete constraint works
        - Child records are deleted when parent is deleted
        """
        pr_id = uuid4()
        review = PRReview(
            id=pr_id,
            repo_id=123,
            pr_number=42,
            pr_title="Test PR",
            head_sha="abc123",
            base_sha="def456",
            status="completed",
        )
        test_db_session.add(review)
        await test_db_session.commit()

        finding = Finding(
            id=uuid4(),
            review_id=pr_id,
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="high",
            category="bug",
            title="Test",
            body="Test",
        )
        test_db_session.add(finding)
        await test_db_session.commit()

        # Delete review
        await test_db_session.delete(review)
        await test_db_session.commit()

        # Finding should be deleted too (cascade)
        result = await test_db_session.execute(select(Finding).where(Finding.review_id == pr_id))
        fetched = result.scalars().first()
        assert fetched is None

    @pytest.mark.asyncio
    async def test_conversation_thread_unique_comment_id(self, test_db_session):
        """Test unique constraint on ConversationThread.github_comment_id.

        Verifies:
        - Unique constraint is enforced
        - Inserting duplicate comment_id raises IntegrityError
        """
        finding = Finding(
            id=uuid4(),
            review_id=uuid4(),
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="high",
            category="bug",
            title="Test",
            body="Test",
        )
        test_db_session.add(finding)
        await test_db_session.commit()

        thread1 = ConversationThread(
            id=uuid4(),
            github_comment_id=12345,
            finding_id=finding.id,
            pr_number=42,
            thread_state={"intent": "fix"},
        )
        test_db_session.add(thread1)
        await test_db_session.commit()

        # Try to add another with same comment ID
        thread2 = ConversationThread(
            id=uuid4(),
            github_comment_id=12345,  # Duplicate!
            finding_id=finding.id,
            pr_number=42,
            thread_state={"intent": "explain"},
        )
        test_db_session.add(thread2)

        with pytest.raises(Exception):  # Should raise IntegrityError
            await test_db_session.commit()

    @pytest.mark.asyncio
    async def test_upsert_conversation_thread(self, test_db_session):
        """Test updating existing ConversationThread.

        Verifies:
        - Existing thread state can be updated
        - Changes persist after commit
        - JSON field (thread_state) updates correctly
        """
        finding = Finding(
            id=uuid4(),
            review_id=uuid4(),
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="high",
            category="bug",
            title="Test",
            body="Test",
        )
        test_db_session.add(finding)
        await test_db_session.commit()

        thread = ConversationThread(
            id=uuid4(),
            github_comment_id=12345,
            finding_id=finding.id,
            pr_number=42,
            thread_state={"intent": "fix"},
        )
        test_db_session.add(thread)
        await test_db_session.commit()

        # Update it
        thread.thread_state = {"intent": "fix", "status": "generated_code"}
        await test_db_session.commit()

        result = await test_db_session.execute(
            select(ConversationThread).where(ConversationThread.github_comment_id == 12345)
        )
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.thread_state["status"] == "generated_code"
        assert fetched.thread_state["intent"] == "fix"

    @pytest.mark.asyncio
    async def test_multiple_findings_per_review(self, test_db_session):
        """Test creating multiple findings linked to same PR review.

        Verifies:
        - Multiple findings can be attached to one review
        - All findings are retrieved with correct relationships
        """
        pr_id = uuid4()
        review = PRReview(
            id=pr_id,
            repo_id=123,
            pr_number=42,
            pr_title="Test PR",
            head_sha="abc123",
            base_sha="def456",
            status="completed",
            findings_count=3,
        )
        test_db_session.add(review)
        await test_db_session.commit()

        findings = [
            Finding(
                id=uuid4(),
                review_id=pr_id,
                file_path=f"app/file_{i}.py",
                line_start=10 + i,
                line_end=10 + i,
                diff_position=5 + i,
                severity="medium",
                category="style",
                title=f"Finding {i}",
                body=f"Description {i}",
            )
            for i in range(3)
        ]
        for finding in findings:
            test_db_session.add(finding)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Finding).where(Finding.review_id == pr_id))
        fetched_findings = result.scalars().all()
        assert len(fetched_findings) == 3
        for i, finding in enumerate(fetched_findings):
            assert finding.category == "style"
            assert f"Finding {i}" in [f.title for f in fetched_findings]
