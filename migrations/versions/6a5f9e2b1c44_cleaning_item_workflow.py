"""Cleaning item workflow, review state, and artifact metadata.

Revision ID: 6a5f9e2b1c44
Revises: d07f4fe0c0a1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a5f9e2b1c44"
down_revision: str | None = "d07f4fe0c0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cleaning_batch") as batch:
        batch.add_column(
            sa.Column(
                "spec_revision",
                sa.String(length=64),
                nullable=False,
                server_default="cleaning-v2",
            )
        )

    with op.batch_alter_table("cleaning_file") as batch:
        batch.add_column(
            sa.Column(
                "outcome",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(sa.Column("source_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("source_deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_cleaning_file_outcome", "cleaning_file", ["outcome"])

    with op.batch_alter_table("cleaning_document") as batch:
        batch.add_column(
            sa.Column(
                "document_key",
                sa.String(length=120),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column(
                "stage",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(sa.Column("error_code", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "content_sha256",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column(
                "chunking_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "audit_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "warnings_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "cleaned_markdown",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column(
                "review_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "retryable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch.add_column(
            sa.Column(
                "remote_parent_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "remote_child_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    op.create_index("ix_cleaning_document_document_key", "cleaning_document", ["document_key"])
    op.create_index("ix_cleaning_document_status", "cleaning_document", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cleaning_document_status", table_name="cleaning_document")
    op.drop_index("ix_cleaning_document_document_key", table_name="cleaning_document")
    with op.batch_alter_table("cleaning_document") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch.drop_column("remote_child_count")
        batch.drop_column("remote_parent_count")
        batch.drop_column("retryable")
        batch.drop_column("review_revision")
        batch.drop_column("cleaned_markdown")
        batch.drop_column("warnings_json")
        batch.drop_column("audit_json")
        batch.drop_column("chunking_json")
        batch.drop_column("content_sha256")
        batch.drop_column("attempts")
        batch.drop_column("error_message")
        batch.drop_column("error_code")
        batch.drop_column("stage")
        batch.drop_column("document_key")

    op.drop_index("ix_cleaning_file_outcome", table_name="cleaning_file")
    with op.batch_alter_table("cleaning_file") as batch:
        batch.drop_column("source_deleted_at")
        batch.drop_column("source_expires_at")
        batch.drop_column("outcome")

    with op.batch_alter_table("cleaning_batch") as batch:
        batch.drop_column("spec_revision")
