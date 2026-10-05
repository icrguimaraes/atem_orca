"""capex: origem e autoria das solicitações

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 20:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capex_projects", sa.Column("attributes", postgresql.JSONB(), nullable=True))
    op.add_column("capex_projects", sa.Column("created_by", sa.Integer(), nullable=True))
    op.add_column("capex_projects", sa.Column("updated_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_capex_projects_created_by_users"), "capex_projects", "users", ["created_by"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_capex_projects_updated_by_users"), "capex_projects", "users", ["updated_by"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_capex_projects_updated_by_users"), "capex_projects", type_="foreignkey")
    op.drop_constraint(op.f("fk_capex_projects_created_by_users"), "capex_projects", type_="foreignkey")
    op.drop_column("capex_projects", "updated_by")
    op.drop_column("capex_projects", "created_by")
    op.drop_column("capex_projects", "attributes")
