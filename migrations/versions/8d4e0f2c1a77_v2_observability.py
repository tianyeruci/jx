"""V2 observability and context indexes.

Revision ID: 8d4e0f2c1a77
Revises: 0b3d1aab3feb
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d4e0f2c1a77"
down_revision: str | None = "0b3d1aab3feb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_business_message_context_cursor",
        "business_message",
        ["conversation_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "system_event_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("event_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("actor_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("build_version", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "occurred_at",
        "level",
        "category",
        "component",
        "event_code",
        "status",
        "request_id",
        "actor_hash",
        "resource_type",
        "resource_id",
    ):
        op.create_index(f"ix_system_event_log_{column}", "system_event_log", [column])


def downgrade() -> None:
    op.drop_table("system_event_log")
    op.drop_index("ix_business_message_context_cursor", table_name="business_message")
