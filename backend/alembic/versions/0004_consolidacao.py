"""consolidação: fotografia da versão congelada

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06 09:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_snapshot_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(length=20), nullable=False),
        sa.Column("company_code", sa.String(length=10), nullable=False),
        sa.Column("branch_code", sa.String(length=10), nullable=True),
        sa.Column("cost_center_id", sa.Integer(), nullable=True),
        sa.Column("cost_center_code", sa.String(length=20), nullable=False),
        sa.Column("cost_center_name", sa.String(length=200), nullable=True),
        sa.Column("account_code", sa.String(length=20), nullable=False),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("package", sa.String(length=120), nullable=True),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["budget_versions.id"],
            name=op.f("fk_budget_snapshot_lines_version_id_budget_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cost_center_id"], ["cost_centers.id"], name=op.f("fk_budget_snapshot_lines_cost_center_id_cost_centers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_snapshot_lines")),
    )
    op.create_index("ix_budget_snapshot_lines_version", "budget_snapshot_lines", ["version_id", "module"])


def downgrade() -> None:
    op.drop_index("ix_budget_snapshot_lines_version", table_name="budget_snapshot_lines")
    op.drop_table("budget_snapshot_lines")
