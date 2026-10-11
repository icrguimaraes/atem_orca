"""Nomes de centros de custo e setores em MAIÚSCULAS (pedido de 10/10/2026: "sempre em caps lock").

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE cost_centers SET name = upper(name) WHERE name <> upper(name)")
    # setor: (nome, área) é único — não converte quando já existe a versão em maiúsculas na mesma área
    op.execute(
        """
        UPDATE areas a SET name = upper(a.name)
        WHERE a.name <> upper(a.name)
          AND NOT EXISTS (
            SELECT 1 FROM areas b
            WHERE b.id <> a.id AND b.name = upper(a.name)
              AND b.department_id IS NOT DISTINCT FROM a.department_id
          )
        """
    )


def downgrade() -> None:
    pass  # a caixa original não é guardada; nada a desfazer
