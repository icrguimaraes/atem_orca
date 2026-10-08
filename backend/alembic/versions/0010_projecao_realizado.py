"""realizado: linhas vindas da projeção do gestor (meses sem KSB1)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08 23:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "actual_entries",
        sa.Column("projected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("actual_entries", "projected")
