"""Add consultation methodology registry tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "k7b8c9d0e1f2"
down_revision: str | None = "j6a7b8c9d0e1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "consultation_method",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("method_fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dify_document_id", sa.String(64), nullable=True),
        sa.Column("maturity", sa.String(40), nullable=False, server_default="single_case_pattern"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("method_fingerprint", name="uq_consultation_method_fingerprint"),
    )
    op.create_index("ix_consultation_method_status", "consultation_method", ["status"])
    op.create_index(
        "ix_consultation_method_dify_document_id", "consultation_method", ["dify_document_id"]
    )
    op.create_table(
        "consultation_method_relation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_document_id", sa.String(36), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(64), nullable=False),
        sa.Column("canonical_method_id", sa.String(36), nullable=True),
        sa.Column("related_method_id", sa.String(36), nullable=True),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("candidate_body_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("relation_score", sa.String(32), nullable=False, server_default=""),
        sa.Column("relation_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "candidate_source_line_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "evidence_case_local_indices", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("source_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("maturity", sa.String(40), nullable=False, server_default="single_case_pattern"),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "source_document_id", "candidate_fingerprint", name="uq_consultation_relation_candidate"
        ),
    )
    op.create_index(
        "ix_consultation_method_relation_source_document_id",
        "consultation_method_relation",
        ["source_document_id"],
    )
    op.create_index(
        "ix_consultation_method_relation_canonical_method_id",
        "consultation_method_relation",
        ["canonical_method_id"],
    )
    op.create_index(
        "ix_consultation_method_relation_related_method_id",
        "consultation_method_relation",
        ["related_method_id"],
    )
    op.create_table(
        "consultation_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_document_id", sa.String(36), nullable=False),
        sa.Column("canonical_method_id", sa.String(36), nullable=True),
        sa.Column("case_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("source_block_id", sa.String(100), nullable=False),
        sa.Column("source_line_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "source_document_id", "evidence_fingerprint", name="uq_consultation_evidence_identity"
        ),
    )
    op.create_index(
        "ix_consultation_evidence_source_document_id",
        "consultation_evidence",
        ["source_document_id"],
    )
    op.create_index(
        "ix_consultation_evidence_canonical_method_id",
        "consultation_evidence",
        ["canonical_method_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consultation_evidence_canonical_method_id", table_name="consultation_evidence"
    )
    op.drop_index("ix_consultation_evidence_source_document_id", table_name="consultation_evidence")
    op.drop_table("consultation_evidence")
    op.drop_index(
        "ix_consultation_method_relation_canonical_method_id",
        table_name="consultation_method_relation",
    )
    op.drop_index(
        "ix_consultation_method_relation_related_method_id",
        table_name="consultation_method_relation",
    )
    op.drop_index(
        "ix_consultation_method_relation_source_document_id",
        table_name="consultation_method_relation",
    )
    op.drop_table("consultation_method_relation")
    op.drop_index("ix_consultation_method_status", table_name="consultation_method")
    op.drop_index("ix_consultation_method_dify_document_id", table_name="consultation_method")
    op.drop_table("consultation_method")
