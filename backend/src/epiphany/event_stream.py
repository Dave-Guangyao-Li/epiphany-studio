from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import Request

from epiphany.schemas import EventView
from epiphany.services import RunService
from epiphany.state_machine import RunStatus

TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


def encode_sse_event(event: EventView) -> str:
    """Encode one durable Event without exposing an in-memory transport shape."""

    data = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.sequence}\nevent: trace\ndata: {data}\n\n"


async def stream_run_events(
    *,
    request: Request,
    service: RunService,
    run_id: str,
    after: int,
    poll_interval_seconds: float = 0.4,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Replay durable events, then poll SQLite until the Run is terminal.

    SQLite remains the source of truth. The sequence cursor makes reconnects
    deterministic and the heartbeat keeps otherwise-idle human checkpoints
    visible to proxies without inventing domain Events.
    """

    cursor = after
    last_write_at = monotonic()
    while True:
        if await request.is_disconnected():
            return

        run = await service.get_run(run_id)
        events = await service.list_events(run_id, after=cursor)
        for event in events:
            cursor = event.sequence
            last_write_at = monotonic()
            yield encode_sse_event(event)

        if run.status in TERMINAL_RUN_STATUSES:
            return

        now = monotonic()
        if now - last_write_at >= heartbeat_seconds:
            last_write_at = now
            yield f": heartbeat {cursor}\n\n"

        await asyncio.sleep(poll_interval_seconds)
