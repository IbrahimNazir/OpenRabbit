"""initial_schema

Revision ID: a18cdd16f712
Revises: f64701a88bd6
Create Date: 2026-03-08 01:38:19.857922
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a18cdd16f712'
down_revision: Union[str, None] = 'f64701a88bd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_repositories_installation_id",
        "repositories",
        "installations",
        ["installation_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_repositories_installation_id", "repositories", type_="foreignkey")
