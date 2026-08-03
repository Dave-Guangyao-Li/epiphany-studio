"""Add local Project workspace and Run ownership.

Revision ID: 0005_project_workspace
Revises: 0004_run_lineage
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_project_workspace"
down_revision: str | None = "0004_run_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_created_at", "projects", ["created_at"], unique=False)
    op.create_table(
        "project_sources",
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "source_id"),
    )
    op.create_index(
        "ix_project_sources_source_id",
        "project_sources",
        ["source_id"],
        unique=False,
    )
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("submission_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_runs_project_id",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_runs_project_id", ["project_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_runs_project_submission_id",
            ["project_id", "submission_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("uq_runs_project_submission_id", type_="unique")
        batch_op.drop_index("ix_runs_project_id")
        batch_op.drop_constraint("fk_runs_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("submission_id")
    op.drop_index("ix_project_sources_source_id", table_name="project_sources")
    op.drop_table("project_sources")
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_table("projects")
