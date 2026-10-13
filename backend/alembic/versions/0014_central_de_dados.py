"""Central de dados (10/10/2026): acesso próprio, separado do perfil — "uma tela que seja só minha".

Concedido na migração aos usuários com perfil Administrador; depois, só quem já tem o acesso pode concedê-lo.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("can_control_data", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )
    op.execute(
        "UPDATE users SET can_control_data = true "
        "WHERE id IN (SELECT user_id FROM user_roles WHERE role_code = 'ADMIN')"
    )


def downgrade() -> None:
    op.drop_column("users", "can_control_data")
