"""defesa do orçamento: perguntas sobre o orçamento (questionar o gestor da área)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("cost_center_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("asked_by", sa.Integer(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["budget_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cost_center_id"], ["cost_centers.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["asked_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["answered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budget_questions_cost_center_id", "budget_questions", ["cost_center_id"])
    op.create_index("ix_budget_questions_version_status", "budget_questions", ["version_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_budget_questions_version_status", table_name="budget_questions")
    op.drop_index("ix_budget_questions_cost_center_id", table_name="budget_questions")
    op.drop_table("budget_questions")
