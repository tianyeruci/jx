"""Add leases to Dify publication shards."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o1f2g3h4i5j6"
down_revision = "n0e1f2g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dify_publication_shard", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column(
        "dify_publication_shard",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_dify_publication_shard_lease_token",
        "dify_publication_shard",
        ["lease_token"],
    )
    op.create_index(
        "ix_dify_publication_shard_lease_expires_at",
        "dify_publication_shard",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dify_publication_shard_lease_expires_at",
        table_name="dify_publication_shard",
    )
    op.drop_index(
        "ix_dify_publication_shard_lease_token",
        table_name="dify_publication_shard",
    )
    op.drop_column("dify_publication_shard", "lease_expires_at")
    op.drop_column("dify_publication_shard", "lease_token")
