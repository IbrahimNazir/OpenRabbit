"""Tenant models: Installation and Repository.

Maps GitHub App installations (tenants) and their connected repositories.
Uses BIGINT primary keys (GitHub's own IDs) — NOT auto-incrementing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class Installation(Base):
    """GitHub App installation → maps to a 'tenant' (organization or user)."""

    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub installation_id
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)  # Organization | User
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)  # type: ignore[assignment]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationship
    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Installation id={self.id} account={self.account_login!r} "
            f"active={self.is_active}>"
        )


class Repository(Base):
    """Repository connected to a GitHub App installation."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub repo_id
    installation_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)  # owner/repo
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    index_status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )  # pending|indexing|ready|failed
    last_indexed_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationship
    installation: Mapped["Installation"] = relationship(back_populates="repositories")

    __table_args__ = (
        Index("ix_repositories_installation_id", "installation_id"),
        Index("ix_repositories_full_name", "full_name"),
    )

    def __repr__(self) -> str:
        return (
            f"<Repository id={self.id} name={self.full_name!r} "
            f"index_status={self.index_status!r}>"
        )
