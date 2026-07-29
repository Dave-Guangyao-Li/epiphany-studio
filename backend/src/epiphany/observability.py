from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

LOG_FIELDS = (
    "event",
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "run_id",
    "task_id",
    "parent_task_id",
    "task_kind",
    "attempt",
    "child_count",
    "concurrency",
    "artifact_id",
    "source_id",
    "source_type",
    "char_count",
    "source_char_count",
    "segment_count",
    "source_segment_count",
    "source_count",
    "section_count",
    "question_count",
    "markdown_char_count",
    "checkpoint",
    "idempotent_replay",
    "readiness_artifact_id",
    "readiness_status",
    "target_duration_minutes",
    "available_source_char_count",
    "additional_source_chars_needed",
    "editor_queued",
    "draft_artifact_id",
    "metrics_artifact_id",
    "quality_report_id",
    "quality_decision",
    "experimental_overall_score",
    "hard_blocker_count",
    "warning_count",
    "feedback_origin",
    "feedback_decision",
    "feedback_rating",
    "human_signal_eligible",
    "model_call_id",
    "provider",
    "model",
    "status",
    "limit",
    "input_tokens",
    "output_tokens",
    "estimated_cost_micros",
    "cost_currency",
    "error_code",
    "recovered_count",
)


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    application_logger = logging.getLogger("epiphany")
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    application_logger.setLevel(resolved_level)
    application_logger.propagate = False

    existing = next(
        (
            handler
            for handler in application_logger.handlers
            if getattr(handler, "_epiphany_json_handler", False)
        ),
        None,
    )
    if existing is not None:
        existing.setLevel(resolved_level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(resolved_level)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    handler._epiphany_json_handler = True
    application_logger.addHandler(handler)
