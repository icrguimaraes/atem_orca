"""defesa do orçamento: perguntas sobre o lançamento (linha OPEX, movimentação de pessoal, solicitação de CAPEX)

As perguntas saem do pacote/recorte e vão para o lançamento em si. FKs com ON DELETE SET NULL (o lançamento pode ser
excluído numa reimportação) e `item_snapshot` com o lançamento como estava na hora da pergunta. Perguntas antigas
(sem `item_type`) continuam valendo.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09 15:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# coluna → tabela referenciada
ITEM_FKS = {
    "budget_line_id": "budget_lines",
    "personnel_movement_id": "personnel_movements",
    "capex_project_id": "capex_projects",
}


def upgrade() -> None:
    op.add_column("budget_questions", sa.Column("item_type", sa.String(length=30), nullable=True))
    for column, table in ITEM_FKS.items():
        op.add_column("budget_questions", sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_budget_questions_{column}_{table}", "budget_questions", table, [column], ["id"], ondelete="SET NULL"
        )
        op.create_index(f"ix_budget_questions_{column}", "budget_questions", [column])
    op.add_column(
        "budget_questions", sa.Column("item_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("budget_questions", "item_snapshot")
    for column in reversed(list(ITEM_FKS)):
        op.drop_index(f"ix_budget_questions_{column}", table_name="budget_questions")
        op.drop_constraint(f"fk_budget_questions_{column}_{ITEM_FKS[column]}", "budget_questions", type_="foreignkey")
        op.drop_column("budget_questions", column)
    op.drop_column("budget_questions", "item_type")
