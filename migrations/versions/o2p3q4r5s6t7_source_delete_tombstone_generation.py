"""Fence source deletion against queued and inflight publication.

Revision ID: o2p3q4r5s6t7
Revises: n0e1f2g3h4i5
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "o2p3q4r5s6t7"
down_revision = "n0e1f2g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cleaning_file",
        sa.Column("source_delete_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "cleaning_file",
        sa.Column("source_delete_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot alter a column default in place.  Force Alembic's
        # copy/rename path so the persisted schema has no server default,
        # matching the model's application-side default.
        with op.batch_alter_table("cleaning_file", recreate="always") as batch_op:
            batch_op.alter_column(
                "source_delete_generation",
                server_default=None,
            )
    else:
        op.alter_column("cleaning_file", "source_delete_generation", server_default=None)


def downgrade() -> None:
    op.drop_column("cleaning_file", "source_delete_requested_at")
    op.drop_column("cleaning_file", "source_delete_generation")
