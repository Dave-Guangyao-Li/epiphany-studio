"""Add parent Run lineage.

Revision ID: 0004_run_lineage
Revises: 0003_model_call_trace
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_run_lineage"
down_revision: str | None = "0003_model_call_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("parent_run_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_runs_parent_run_id",
            "runs",
            ["parent_run_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_runs_parent_run_id",
            ["parent_run_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_parent_run_id")
        batch_op.drop_constraint("fk_runs_parent_run_id", type_="foreignkey")
        batch_op.drop_column("parent_run_id")
