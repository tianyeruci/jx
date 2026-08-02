"""Expand cleaning batch status for partial publication states.

Revision ID: h4e5f6a7b8c9
Revises: g3b7c9d1e5f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "h4e5f6a7b8c9"
down_revision: str | None = "g3b7c9d1e5f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # SQLite does not enforce VARCHAR length and cannot execute ALTER
        # COLUMN TYPE; the wider model field is already compatible.
        return
    op.alter_column(
        "cleaning_batch",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.execute(
        "UPDATE cleaning_batch SET status = 'processing' WHERE length(status) > 16"
    )
    op.alter_column(
        "cleaning_batch",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
