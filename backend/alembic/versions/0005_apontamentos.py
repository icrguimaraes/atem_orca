"""apontamentos: registro das correções e dos avisos mantidos

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("finding_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["budget_submissions.id"],
            name=op.f("fk_finding_reviews_submission_id_budget_submissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_finding_reviews_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_reviews")),
    )
    op.create_index(op.f("ix_finding_reviews_submission_id"), "finding_reviews", ["submission_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_finding_reviews_submission_id"), table_name="finding_reviews")
    op.drop_table("finding_reviews")
