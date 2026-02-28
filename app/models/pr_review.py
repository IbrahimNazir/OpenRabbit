"""PR review, finding, and conversation thread models.

Uses UUID primary keys (gen_random_uuid at DB level).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class PRReview(Base):
    """One record per PR review job."""

    __tablename__ = "pr_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="queued", nullable=False
    )  # queued|processing|completed|failed
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0, nullable=False)  # type: ignore[assignment]
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_pr_reviews_repo_pr", "repo_id", "pr_number"),
        Index("ix_pr_reviews_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<PRReview id={self.id!s:.8} repo_id={self.repo_id} "
            f"pr=#{self.pr_number} status={self.status!r}>"
        )


class Finding(Base):
    """Individual finding posted as a PR comment."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diff_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # critical|high|medium|low|info
    category: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # bug|security|style|performance|docs
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    was_applied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    was_dismissed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationship
    review: Mapped["PRReview"] = relationship(back_populates="findings")

    __table_args__ = (
        Index("ix_findings_review_id", "review_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Finding id={self.id!s:.8} file={self.file_path!r} "
            f"severity={self.severity!r} category={self.category!r}>"
        )


class ConversationThread(Base):
    """PR comment thread state for conversation (\"Fix this\" flow)."""

    __tablename__ = "conversation_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    github_comment_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    repo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_state: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)  # type: ignore[assignment]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_conversation_threads_github_comment_id", "github_comment_id", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationThread github_comment_id={self.github_comment_id} "
            f"pr=#{self.pr_number}>"
        )
