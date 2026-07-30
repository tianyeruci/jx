"""Store the source user id for administrator audit views.

Revision ID: f2a1d6c4b8e9
Revises: e11c7f4a2d90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f2a1d6c4b8e9"
down_revision: str | None = "e11c7f4a2d90"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("business_profile") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint("uq_business_profile_user_id", ["user_id"])
        batch_op.create_index("ix_business_profile_user_id", ["user_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("business_profile") as batch_op:
        batch_op.drop_index("ix_business_profile_user_id")
        batch_op.drop_constraint("uq_business_profile_user_id", type_="unique")
        batch_op.drop_column("user_id")
