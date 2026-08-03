"""Add internal course collection authority tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "j6a7b8c9d0e1"
down_revision: str | None = "i5f6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "course_collection",
        sa.Column("collection_id", sa.String(length=100), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("raw_title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("declared_lessons", sa.Integer(), nullable=True),
        sa.Column("total_minutes", sa.Integer(), nullable=True),
        sa.Column("entry_course_id", sa.String(length=100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="approved"),
        sa.Column("source_file", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_course_collection_name", "course_collection", ["name"])
    op.create_index("ix_course_collection_enabled", "course_collection", ["enabled"])
    op.create_index("ix_course_collection_review_status", "course_collection", ["review_status"])
    op.create_table(
        "course_collection_member",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "collection_id",
            sa.String(length=100),
            sa.ForeignKey("course_collection.collection_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.String(length=100),
            sa.ForeignKey("course.course_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_rank", sa.Integer(), nullable=False),
        sa.Column("is_entry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("collection_id", "course_id", name="uq_course_collection_course"),
        sa.UniqueConstraint("collection_id", "order_rank", name="uq_course_collection_order"),
    )
    op.create_index("ix_course_collection_member_collection_id", "course_collection_member", ["collection_id"])
    op.create_index("ix_course_collection_member_course", "course_collection_member", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_course_collection_member_course", table_name="course_collection_member")
    op.drop_index("ix_course_collection_member_collection_id", table_name="course_collection_member")
    op.drop_table("course_collection_member")
    op.drop_index("ix_course_collection_review_status", table_name="course_collection")
    op.drop_index("ix_course_collection_enabled", table_name="course_collection")
    op.drop_index("ix_course_collection_name", table_name="course_collection")
    op.drop_table("course_collection")
