"""Create the durable M1 runtime tables.

Revision ID: 0001_initial_runtime
Revises:
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_runtime"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workflow_type", sa.String(length=80), nullable=False),
        sa.Column("workflow_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=80), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("parent_task_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("agent_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )
    op.create_index("ix_tasks_claim", "tasks", ["status", "created_at"], unique=False)
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"], unique=False)
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_events_run_sequence"),
    )
    op.create_index(
        "ix_events_run_sequence",
        "events",
        ["run_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_artifacts_idempotency_key"),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"], unique=False)
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.create_foreign_key(
            "fk_tasks_output_artifact_id",
            "artifacts",
            ["output_artifact_id"],
            ["id"],
        )
    with op.batch_alter_table("runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_runs_output_artifact_id",
            "artifacts",
            ["output_artifact_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("fk_runs_output_artifact_id", type_="foreignkey")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_output_artifact_id", type_="foreignkey")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_events_run_sequence", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_tasks_run_id", table_name="tasks")
    op.drop_index("ix_tasks_claim", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("runs")
