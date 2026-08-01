"""Add model cleaning tasks and Dify publication shards.

Revision ID: g3b7c9d1e5f2
Revises: f2a1d6c4b8e9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "g3b7c9d1e5f2"
down_revision: str | None = "f2a1d6c4b8e9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "course_direction",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "review_status",
            sa.String(length=32),
            nullable=False,
            server_default="approved",
        ),
        sa.Column(
            "approved_by",
            sa.String(length=100),
            nullable=False,
            server_default="admin",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_course_direction_name", "course_direction", ["name"])
    op.create_index(
        "ix_course_direction_enabled", "course_direction", ["enabled"]
    )
    op.create_index(
        "ix_course_direction_review_status",
        "course_direction",
        ["review_status"],
    )

    op.create_table(
        "model_cleaning_task",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "estimated_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "estimated_output_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "estimated_cost_low", sa.String(length=40), nullable=False, server_default="0"
        ),
        sa.Column(
            "estimated_cost_high", sa.String(length=40), nullable=False, server_default="0"
        ),
        sa.Column(
            "price_version", sa.String(length=100), nullable=False, server_default=""
        ),
        sa.Column(
            "price_effective_date",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "price_valid_until",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "approval_fingerprint",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_batch_id", sa.String(length=100), nullable=True),
        sa.Column(
            "provider_batch_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "provider_input_file_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "provider_output_file_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "provider_error_file_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "result_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "actual_usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "cleanup_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["cleaning_batch.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "prompt_version",
            "candidate_fingerprint",
            name="uq_model_cleaning_task_fingerprint",
        ),
    )
    op.create_index(
        "ix_model_cleaning_task_batch_id", "model_cleaning_task", ["batch_id"]
    )
    op.create_index(
        "ix_model_cleaning_task_status", "model_cleaning_task", ["status"]
    )
    op.create_index(
        "ix_model_cleaning_task_mode", "model_cleaning_task", ["mode"]
    )
    op.create_index(
        "ix_model_cleaning_task_provider_batch_id",
        "model_cleaning_task",
        ["provider_batch_id"],
    )

    op.create_table(
        "dify_publication_shard",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("shard_index", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="shard_pending",
        ),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("dify_document_id", sa.String(length=64), nullable=True),
        sa.Column("dify_batch_id", sa.String(length=100), nullable=True),
        sa.Column(
            "remote_parent_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "remote_child_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["cleaning_file.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id", "shard_index", name="uq_dify_shard_file_index"
        ),
        sa.UniqueConstraint(
            "file_id", "content_sha256", name="uq_dify_shard_file_content"
        ),
    )
    op.create_index(
        "ix_dify_publication_shard_file_id",
        "dify_publication_shard",
        ["file_id"],
    )
    op.create_index(
        "ix_dify_publication_shard_status",
        "dify_publication_shard",
        ["status"],
    )
    op.create_index(
        "ix_dify_publication_shard_dify_document_id",
        "dify_publication_shard",
        ["dify_document_id"],
    )

    with op.batch_alter_table("cleaning_document") as batch:
        batch.add_column(sa.Column("shard_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("shard_parent_index", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_cleaning_document_shard",
            "dify_publication_shard",
            ["shard_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_cleaning_document_shard_id", "cleaning_document", ["shard_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_cleaning_document_shard_id", table_name="cleaning_document")
    with op.batch_alter_table("cleaning_document") as batch:
        batch.drop_constraint("fk_cleaning_document_shard", type_="foreignkey")
        batch.drop_column("shard_parent_index")
        batch.drop_column("shard_id")
    op.drop_index(
        "ix_dify_publication_shard_dify_document_id",
        table_name="dify_publication_shard",
    )
    op.drop_index(
        "ix_dify_publication_shard_status", table_name="dify_publication_shard"
    )
    op.drop_index(
        "ix_dify_publication_shard_file_id", table_name="dify_publication_shard"
    )
    op.drop_table("dify_publication_shard")
    op.drop_index(
        "ix_model_cleaning_task_provider_batch_id",
        table_name="model_cleaning_task",
    )
    op.drop_index("ix_model_cleaning_task_mode", table_name="model_cleaning_task")
    op.drop_index("ix_model_cleaning_task_status", table_name="model_cleaning_task")
    op.drop_index(
        "ix_model_cleaning_task_batch_id", table_name="model_cleaning_task"
    )
    op.drop_table("model_cleaning_task")
    op.drop_index(
        "ix_course_direction_review_status", table_name="course_direction"
    )
    op.drop_index("ix_course_direction_enabled", table_name="course_direction")
    op.drop_index("ix_course_direction_name", table_name="course_direction")
    op.drop_table("course_direction")
