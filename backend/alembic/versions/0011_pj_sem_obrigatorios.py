"""contratos PJ: nada obrigatório (nome, razão social, CNPJ, valor mensal e admissão aceitam vazio)

Campos importantes vazios viram pendência ("Falta preencher") na tela, em vez de bloquear o cadastro.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# coluna → (tipo, valor que preenche os vazios ao voltar para NOT NULL no downgrade)
COLUMNS = {
    "name": (sa.String(length=200), "''"),
    "company_name": (sa.String(length=200), "''"),
    "cnpj": (sa.String(length=14), "''"),
    "monthly_value": (sa.Numeric(precision=18, scale=2), "0"),
    "start_date": (sa.Date(), "created_at::date"),
}


def upgrade() -> None:
    for column, (type_, _) in COLUMNS.items():
        op.alter_column("pj_contracts", column, existing_type=type_, nullable=True)


def downgrade() -> None:
    for column, (type_, fill) in COLUMNS.items():
        op.execute(f"UPDATE pj_contracts SET {column} = {fill} WHERE {column} IS NULL")
        op.alter_column("pj_contracts", column, existing_type=type_, nullable=False)
