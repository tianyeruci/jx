"""Add authoritative course-catalogue metadata.

Revision ID: e11c7f4a2d90
Revises: 6a5f9e2b1c44
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e11c7f4a2d90"
down_revision: str | None = "6a5f9e2b1c44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("course") as batch:
        batch.add_column(
            sa.Column("stage", sa.String(length=100), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column(
                "source_platform",
                sa.String(length=100),
                nullable=False,
                server_default="小鹅通",
            )
        )
        batch.add_column(sa.Column("series_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("published_at", sa.Date(), nullable=True))
        batch.add_column(sa.Column("first_imported_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "prerequisite_course_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    op.create_index("ix_course_stage", "course", ["stage"])
    op.create_index("ix_course_source_platform", "course", ["source_platform"])
    op.create_index("ix_course_series_name", "course", ["series_name"])
    op.create_index("ix_course_published_at", "course", ["published_at"])
    op.create_index("ix_course_first_imported_at", "course", ["first_imported_at"])

    # Preserve the original import/creation chronology for legacy records;
    # null global stages intentionally remain ineligible for stage-dependent
    # standalone recommendations rather than being guessed.
    op.execute("UPDATE course SET first_imported_at = created_at WHERE first_imported_at IS NULL")


def downgrade() -> None:
    op.drop_index("ix_course_first_imported_at", table_name="course")
    op.drop_index("ix_course_published_at", table_name="course")
    op.drop_index("ix_course_series_name", table_name="course")
    op.drop_index("ix_course_source_platform", table_name="course")
    op.drop_index("ix_course_stage", table_name="course")
    with op.batch_alter_table("course") as batch:
        batch.drop_column("first_imported_at")
        batch.drop_column("published_at")
        batch.drop_column("prerequisite_course_ids")
        batch.drop_column("series_name")
        batch.drop_column("source_platform")
        batch.drop_column("stage")
