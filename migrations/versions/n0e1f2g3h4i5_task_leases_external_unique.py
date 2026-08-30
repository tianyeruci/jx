"""Add task leases and external deletion uniqueness."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n0e1f2g3h4i5"
down_revision = "m9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT kind, external_id FROM external_deletion_task "
            "GROUP BY kind, external_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("external_deletion_task 存在重复 kind/external_id，拒绝迁移")
    for table in ("model_cleaning_task", "cleaning_file", "external_deletion_task"):
        op.add_column(table, sa.Column("lease_token", sa.String(64), nullable=True))
        op.add_column(
            table, sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.create_index(f"ix_{table}_lease_token", table, ["lease_token"])
        op.create_index(f"ix_{table}_lease_expires_at", table, ["lease_expires_at"])
    if bind.dialect.name == "sqlite":
        op.create_index(
            "uq_external_deletion_kind_id",
            "external_deletion_task",
            ["kind", "external_id"],
            unique=True,
        )
    else:
        op.create_unique_constraint(
            "uq_external_deletion_kind_id", "external_deletion_task", ["kind", "external_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.drop_index("uq_external_deletion_kind_id", table_name="external_deletion_task")
    else:
        op.drop_constraint("uq_external_deletion_kind_id", "external_deletion_task", type_="unique")
    for table in ("external_deletion_task", "cleaning_file", "model_cleaning_task"):
        op.drop_index(f"ix_{table}_lease_expires_at", table_name=table)
        op.drop_index(f"ix_{table}_lease_token", table_name=table)
        op.drop_column(table, "lease_expires_at")
        op.drop_column(table, "lease_token")
