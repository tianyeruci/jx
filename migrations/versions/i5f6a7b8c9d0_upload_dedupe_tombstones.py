"""Add normalized upload dedupe keys and remote deletion tombstones."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "i5f6a7b8c9d0"
down_revision: str | None = "h4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cleaning_file", sa.Column("source_name_key", sa.String(length=500), nullable=True))
    op.add_column("cleaning_file", sa.Column("dedupe_released_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_cleaning_file_source_name_key", "cleaning_file", ["source_name_key"])
    op.add_column("cleaning_document", sa.Column("remote_deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dify_publication_shard", sa.Column("remote_deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dify_publication_shard", "remote_deleted_at")
    op.drop_column("cleaning_document", "remote_deleted_at")
    op.drop_index("ix_cleaning_file_source_name_key", table_name="cleaning_file")
    op.drop_column("cleaning_file", "dedupe_released_at")
    op.drop_column("cleaning_file", "source_name_key")
