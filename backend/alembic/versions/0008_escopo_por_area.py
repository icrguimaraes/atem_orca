"""acesso por área inteira (user_scopes.department_id)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08 21:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_scopes", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_user_scopes_department", "user_scopes", "departments", ["department_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_user_scopes_department", "user_scopes", type_="foreignkey")
    op.drop_column("user_scopes", "department_id")
