"""Orçado do próprio projeto por ano × mês (15/10/2026): só no projeto, fora do orçado do Painel.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_budget_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger(), nullable=False),
        sa.Column("period", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("source", sa.String(300), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_budget_entries_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_project_budget_entries_created_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_project_budget_entries"),
        sa.UniqueConstraint(
            "project_id", "fiscal_year", "period", name="uq_project_budget_entries_project_id_fiscal_year_period"
        ),
    )
    op.create_index("ix_project_budget_entries_project_id", "project_budget_entries", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_budget_entries")
