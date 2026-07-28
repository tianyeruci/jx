"""V2 workflow state, admin settings, and knowledge upload mode.

Revision ID: d07f4fe0c0a1
Revises: 8d4e0f2c1a77
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d07f4fe0c0a1"
down_revision: str | None = "8d4e0f2c1a77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("business_profile") as batch:
        batch.add_column(
            sa.Column(
                "display_name",
                sa.String(length=120),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
    op.create_index("ix_business_profile_display_name", "business_profile", ["display_name"])

    with op.batch_alter_table("learning_route_progress") as batch:
        batch.alter_column(
            "current_stage_id",
            existing_type=sa.String(length=100),
            nullable=True,
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("cleaning_batch") as batch:
        batch.add_column(
            sa.Column(
                "upload_mode",
                sa.String(length=16),
                nullable=False,
                server_default="cleaned",
            )
        )

    with op.batch_alter_table("business_message") as batch:
        batch.add_column(
            sa.Column("feedback", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )

    with op.batch_alter_table("cleaning_file") as batch:
        batch.add_column(sa.Column("replace_document_id", sa.String(length=64), nullable=True))

    op.create_table(
        "admin_setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "ai_style_setting",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("style_prompt", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("ai_style_setting")
    op.drop_table("admin_setting")
    with op.batch_alter_table("cleaning_batch") as batch:
        batch.drop_column("upload_mode")
    with op.batch_alter_table("business_message") as batch:
        batch.drop_column("feedback")
    with op.batch_alter_table("cleaning_file") as batch:
        batch.drop_column("replace_document_id")
    with op.batch_alter_table("learning_route_progress") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch.alter_column(
            "current_stage_id",
            existing_type=sa.String(length=100),
            nullable=False,
        )
    op.drop_index("ix_business_profile_display_name", table_name="business_profile")
    with op.batch_alter_table("business_profile") as batch:
        batch.drop_column("display_name")
