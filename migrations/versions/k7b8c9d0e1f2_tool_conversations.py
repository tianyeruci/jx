"""Add temporary tool conversation storage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m9d0e1f2g3h4"
down_revision = "l8c9d0e1f2g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM admin_setting WHERE key IN ('tool_examples:moments', 'tool_examples:writing')"
        )
    )
    op.create_table(
        "tool_conversation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("tool_type", sa.String(32), nullable=False),
        sa.Column("output_format", sa.String(64), nullable=False, server_default="article"),
        sa.Column("dify_conversation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, column in (
        ("ix_tool_conversation_user_hash", "user_hash"),
        ("ix_tool_conversation_kind", "kind"),
        ("ix_tool_conversation_status", "status"),
        ("ix_tool_conversation_last_active_at", "last_active_at"),
    ):
        op.create_index(name, "tool_conversation", [column])
    if op.get_bind().dialect.name == "sqlite":
        op.create_index(
            "uq_tool_conversation_user_kind",
            "tool_conversation",
            ["user_hash", "kind"],
            unique=True,
        )
    else:
        op.create_unique_constraint(
            "uq_tool_conversation_user_kind", "tool_conversation", ["user_hash", "kind"]
        )
    op.create_table(
        "tool_message",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("tool_conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        sa.Column("dify_task_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tool_message_conversation_id", "tool_message", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_message_conversation_id", table_name="tool_message")
    op.drop_table("tool_message")
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("uq_tool_conversation_user_kind", table_name="tool_conversation")
    else:
        op.drop_constraint("uq_tool_conversation_user_kind", "tool_conversation", type_="unique")
    for name in (
        "ix_tool_conversation_last_active_at",
        "ix_tool_conversation_status",
        "ix_tool_conversation_kind",
        "ix_tool_conversation_user_hash",
    ):
        op.drop_index(name, table_name="tool_conversation")
    op.drop_table("tool_conversation")
