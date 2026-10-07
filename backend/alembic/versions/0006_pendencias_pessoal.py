"""pessoal: pendências da importação no colaborador

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07 20:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "attributes")
