"""Co-gestores do centro de custo (19/11/2026): além do gestor principal (`cost_centers.manager_user_id`), outras
pessoas com o mesmo acesso de gestor ao CC. Tabela nova; o gestor principal continua onde está.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_center_managers",
        sa.Column(
            "cost_center_id", sa.Integer(), sa.ForeignKey("cost_centers.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_cost_center_managers_user_id", "cost_center_managers", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cost_center_managers_user_id", table_name="cost_center_managers")
    op.drop_table("cost_center_managers")
