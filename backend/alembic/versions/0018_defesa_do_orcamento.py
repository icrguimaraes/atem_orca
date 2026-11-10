"""Defesa do orçamento (10/11/2026): ajustes da Controladoria sobre os cortes entre duas versões (prioridade,
argumento e valor a pedir de volta), na chave CC × módulo × conta. Tabela nova; não mexe no orçamento.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_defense_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("budget_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "base_version_id", sa.Integer(), sa.ForeignKey("budget_versions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("cost_center_id", sa.Integer(), sa.ForeignKey("cost_centers.id"), nullable=False),
        sa.Column("module", sa.String(20), nullable=False),
        sa.Column("account_code", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("argument", sa.Text(), nullable=True),
        sa.Column("request_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version_id", "base_version_id", "cost_center_id", "module", "account_code"),
    )
    op.create_index("ix_budget_defense_items_version_id", "budget_defense_items", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_budget_defense_items_version_id", table_name="budget_defense_items")
    op.drop_table("budget_defense_items")
