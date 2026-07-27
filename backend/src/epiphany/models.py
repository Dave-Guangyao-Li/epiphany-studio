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
    model_calls: Mapped[list[ModelCall]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="ModelCall.run_id",
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
    output_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "artifacts.id",
            use_alter=True,
            name="fk_tasks_output_artifact_id",
        )
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="tasks", foreign_keys=[run_id])
    parent_task: Mapped[Task | None] = relationship(remote_side=[id])
    model_calls: Mapped[list[ModelCall]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        foreign_keys="ModelCall.task_id",
    )


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt", name="uq_model_calls_task_attempt"),
        Index("ix_model_calls_run_id", "run_id"),
        Index("ix_model_calls_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mcall"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="model_calls", foreign_keys=[run_id])
    task: Mapped[Task] = relationship(back_populates="model_calls", foreign_keys=[task_id])


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


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_sources_content_sha256"),
        Index("ix_sources_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    segments: Mapped[list[SourceSegment]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="SourceSegment.position",
    )


class SourceSegment(Base):
    __tablename__ = "source_segments"
    __table_args__ = (
        UniqueConstraint("source_id", "position", name="uq_source_segments_position"),
        Index("ix_source_segments_source_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    source: Mapped[Source] = relationship(back_populates="segments")
