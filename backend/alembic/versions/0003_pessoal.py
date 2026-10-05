"""pessoal: atributos, CC de destino e autoria das movimentações

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05 22:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

FKS = (
    ("target_cost_center_id", "cost_centers"),
    ("created_by", "users"),
    ("updated_by", "users"),
)


def upgrade() -> None:
    op.add_column("personnel_movements", sa.Column("attributes", postgresql.JSONB(), nullable=True))
    for column, table in FKS:
        op.add_column("personnel_movements", sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_personnel_movements_{column}_{table}"), "personnel_movements", table, [column], ["id"]
        )


def downgrade() -> None:
    for column, table in reversed(FKS):
        op.drop_constraint(op.f(f"fk_personnel_movements_{column}_{table}"), "personnel_movements", type_="foreignkey")
        op.drop_column("personnel_movements", column)
    op.drop_column("personnel_movements", "attributes")
