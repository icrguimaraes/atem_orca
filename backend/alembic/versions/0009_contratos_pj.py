"""contratos PJ: cadastro confidencial, fotos e acesso "Vê contratos PJ" (Não / Da área / Todos)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 23:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_view_pj", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("can_view_all_pj", sa.Boolean(), server_default="false", nullable=False))
    op.create_table(
        "pj_contracts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("role", sa.String(length=150), nullable=True),
        sa.Column("cost_center_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("monthly_value", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("annual_bonus", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photo_blurred", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["cost_center_id"], ["cost_centers.id"], name=op.f("fk_pj_contracts_cost_center_id_cost_centers")
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_pj_contracts_created_by_users")),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_pj_contracts_updated_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pj_contracts")),
    )
    op.create_index(op.f("ix_pj_contracts_archived_at"), "pj_contracts", ["archived_at"], unique=False)
    op.create_index(op.f("ix_pj_contracts_cnpj"), "pj_contracts", ["cnpj"], unique=False)
    op.create_index(op.f("ix_pj_contracts_cost_center_id"), "pj_contracts", ["cost_center_id"], unique=False)
    op.create_table(
        "pj_photos",
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(length=30), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["pj_contracts.id"],
            name=op.f("fk_pj_photos_contract_id_pj_contracts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("contract_id", name=op.f("pk_pj_photos")),
    )


def downgrade() -> None:
    op.drop_table("pj_photos")
    op.drop_index(op.f("ix_pj_contracts_cost_center_id"), table_name="pj_contracts")
    op.drop_index(op.f("ix_pj_contracts_cnpj"), table_name="pj_contracts")
    op.drop_index(op.f("ix_pj_contracts_archived_at"), table_name="pj_contracts")
    op.drop_table("pj_contracts")
    op.drop_column("users", "can_view_all_pj")
    op.drop_column("users", "can_view_pj")
