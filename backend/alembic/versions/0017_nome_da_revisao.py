"""Nome curto da revisão e versão base (10/11/2026): "Revisão 1 · VP" é a base contra a qual as outras comparam;
"Revisão 2 · Planejamento" nas legendas do Painel.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_versions", sa.Column("name", sa.String(60), nullable=True))
    op.add_column("budget_versions", sa.Column("is_base", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("budget_versions", "is_base")
    op.drop_column("budget_versions", "name")
