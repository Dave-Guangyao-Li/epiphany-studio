from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from epiphany.ids import new_id
from epiphany.state_machine import RunStatus, TaskStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Run(TimestampMixin, Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    workflow_type: Mapped[str] = mapped_column(String(80), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.QUEUED)
    current_step: Mapped[str | None] = mapped_column(String(80))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", use_alter=True, name="fk_runs_output_artifact_id")
    )
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tasks: Mapped[list[Task]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="Task.run_id",
    )
    events: Mapped[list[Event]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="Event.run_id",
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="Artifact.run_id",
    )


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        Index("ix_tasks_claim", "status", "created_at"),
        Index("ix_tasks_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("task"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(80), nullable=False, default="fake_agent")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.QUEUED)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="tasks", foreign_keys=[run_id])
    parent_task: Mapped[Task | None] = relationship(remote_side=[id])


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_events_run_sequence"),
        Index("ix_events_run_sequence", "run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evt"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="events", foreign_keys=[run_id])


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_artifacts_idempotency_key"),
        Index("ix_artifacts_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="artifacts", foreign_keys=[run_id])
