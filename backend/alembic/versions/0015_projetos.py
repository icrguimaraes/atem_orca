"""Projetos (14/10/2026): cadastro, vínculos com o realizado/orçamento do sistema, marcos, riscos, links e diário.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _child(name: str, *columns: sa.Column, unique: tuple[str, ...] | None = None) -> None:
    args = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE", name=f"fk_{name}_project_id_projects"),
            nullable=False,
        ),
        *columns,
    ]
    if unique:
        args.append(sa.UniqueConstraint(*unique, name=f"uq_{name}_{'_'.join(unique)}"))
    op.create_table(name, *args, sa.PrimaryKeyConstraint("id", name=f"pk_{name}"))
    op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cost_center_id", sa.Integer(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("owner_name", sa.String(200), nullable=True),
        sa.Column("sponsor", sa.String(200), nullable=True),
        sa.Column("start_planned", sa.Date(), nullable=True),
        sa.Column("end_planned", sa.Date(), nullable=True),
        sa.Column("start_actual", sa.Date(), nullable=True),
        sa.Column("end_actual", sa.Date(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["cost_center_id"], ["cost_centers.id"], name="fk_projects_cost_center_id_cost_centers"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_projects_owner_user_id_users"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_projects_created_by_users"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_projects_updated_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("code", name="uq_projects_code"),
    )
    _child(
        "project_fact_keys",
        sa.Column(
            "cost_center_id",
            sa.Integer(),
            sa.ForeignKey("cost_centers.id", name="fk_project_fact_keys_cost_center_id_cost_centers"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_project_fact_keys_account_id_accounts"),
            nullable=False,
        ),
        unique=("project_id", "cost_center_id", "account_id"),
    )
    _child(
        "project_capex_links",
        sa.Column(
            "cost_center_id",
            sa.Integer(),
            sa.ForeignKey("cost_centers.id", name="fk_project_capex_links_cost_center_id_cost_centers"),
            nullable=False,
        ),
        sa.Column("capex_code", sa.String(20), nullable=False),
        unique=("project_id", "cost_center_id", "capex_code"),
    )
    _child(
        "project_milestones",
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("done_date", sa.Date(), nullable=True),
        sa.Column("progress_pct", sa.SmallInteger(), nullable=False),
        sa.Column("owner_name", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    _child(
        "project_risks",
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(10), nullable=False),
        sa.Column("probability", sa.String(10), nullable=False),
        sa.Column("mitigation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
    )
    _child(
        "project_links",
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
    )
    _child(
        "project_updates",
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_project_updates_user_id_users"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "project_updates",
        "project_links",
        "project_risks",
        "project_milestones",
        "project_capex_links",
        "project_fact_keys",
    ):
        op.drop_table(name)
    op.drop_table("projects")
