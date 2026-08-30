"""Add cross-batch methodology promotion and backfill state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "l8c9d0e1f2g3"
down_revision: str | None = "k7b8c9d0e1f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "consultation_method",
        sa.Column("source_document_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("consultation_method", sa.Column("promoted_at", sa.DateTime(timezone=True)))
    op.add_column(
        "consultation_method",
        sa.Column("promotion_status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.add_column(
        "consultation_method",
        sa.Column("promotion_error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_consultation_method_promotion_status",
        "consultation_method",
        ["promotion_status"],
    )

    op.create_table(
        "consultation_method_search_token",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("method_id", sa.String(36), nullable=False),
        sa.Column("token", sa.String(120), nullable=False),
        sa.Column("token_type", sa.String(32), nullable=False, server_default="lexical"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("method_id", "token", name="uq_consultation_method_search_token"),
    )
    op.create_index(
        "ix_consultation_method_search_token_method_id",
        "consultation_method_search_token",
        ["method_id"],
    )
    op.create_index(
        "ix_consultation_method_search_token_token",
        "consultation_method_search_token",
        ["token"],
    )

    op.create_table(
        "consultation_method_publication",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("method_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("dify_document_id", sa.String(64), nullable=True),
        sa.Column("dify_batch_id", sa.String(100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("method_id", name="uq_consultation_method_publication"),
    )
    op.create_index(
        "ix_consultation_method_publication_method_id",
        "consultation_method_publication",
        ["method_id"],
    )
    op.create_index(
        "ix_consultation_method_publication_status",
        "consultation_method_publication",
        ["status"],
    )
    op.create_index(
        "ix_consultation_method_publication_dify_document_id",
        "consultation_method_publication",
        ["dify_document_id"],
    )

    op.create_table(
        "consultation_backfill_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_missing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("file_id", name="uq_consultation_backfill_file"),
    )
    op.create_index(
        "ix_consultation_backfill_item_file_id", "consultation_backfill_item", ["file_id"]
    )
    op.create_index(
        "ix_consultation_backfill_item_status", "consultation_backfill_item", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_consultation_backfill_item_status", table_name="consultation_backfill_item")
    op.drop_index("ix_consultation_backfill_item_file_id", table_name="consultation_backfill_item")
    op.drop_table("consultation_backfill_item")
    op.drop_index(
        "ix_consultation_method_publication_dify_document_id",
        table_name="consultation_method_publication",
    )
    op.drop_index(
        "ix_consultation_method_publication_status",
        table_name="consultation_method_publication",
    )
    op.drop_index(
        "ix_consultation_method_publication_method_id",
        table_name="consultation_method_publication",
    )
    op.drop_table("consultation_method_publication")
    op.drop_index(
        "ix_consultation_method_search_token_token",
        table_name="consultation_method_search_token",
    )
    op.drop_index(
        "ix_consultation_method_search_token_method_id",
        table_name="consultation_method_search_token",
    )
    op.drop_table("consultation_method_search_token")
    op.drop_index("ix_consultation_method_promotion_status", table_name="consultation_method")
    op.drop_column("consultation_method", "promotion_error")
    op.drop_column("consultation_method", "promotion_status")
    op.drop_column("consultation_method", "promoted_at")
    op.drop_column("consultation_method", "source_document_count")
