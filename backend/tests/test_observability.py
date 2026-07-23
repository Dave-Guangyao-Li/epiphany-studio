from __future__ import annotations

import json
import logging

from epiphany.observability import (
    JsonFormatter,
    RequestContextFilter,
    bind_request_id,
    reset_request_id,
)


def test_json_log_contains_request_and_domain_context() -> None:
    token = bind_request_id("req_test_123")
    try:
        record = logging.LogRecord(
            name="epiphany.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Task completed",
            args=(),
            exc_info=None,
        )
        record.event = "worker.task.completed"
        record.run_id = "run_test"
        record.task_id = "task_test"
        RequestContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_request_id(token)

    assert payload["level"] == "INFO"
    assert payload["event"] == "worker.task.completed"
    assert payload["request_id"] == "req_test_123"
    assert payload["run_id"] == "run_test"
    assert payload["task_id"] == "task_test"
