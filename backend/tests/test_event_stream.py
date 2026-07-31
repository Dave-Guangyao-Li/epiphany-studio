from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from epiphany.config import Settings
from epiphany.event_stream import encode_sse_event, stream_run_events
from epiphany.main import create_app
from epiphany.schemas import EventView


def _trace_events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def test_sse_replays_then_follows_live_events_until_terminal(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'sse.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
            worker_poll_interval_seconds=0.01,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = (await client.post("/runs", json={"payload": {"topic": "SSE live trace"}})).json()
        run_id = created["id"]

        response_task = asyncio.create_task(client.get(f"/runs/{run_id}/events/stream"))
        await asyncio.sleep(0.02)
        assert await app.state.worker.run_until_idle() == 3
        response = await asyncio.wait_for(response_task, timeout=2)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _trace_events(response.text)
        sequences = [event["sequence"] for event in events]
        assert sequences == list(range(1, len(sequences) + 1))
        assert events[0]["type"] == "run.created"
        assert events[-1]["type"] == "run.succeeded"

        replay = await client.get(
            f"/runs/{run_id}/events/stream?after=1",
            headers={"Last-Event-ID": "3"},
        )
        replay_events = _trace_events(replay.text)
        assert replay_events
        assert all(int(event["sequence"]) > 3 for event in replay_events)
        assert replay_events[-1]["type"] == "run.succeeded"

        missing = await client.get("/runs/run_missing/events/stream")
        assert missing.status_code == 404
        invalid = await client.get(
            f"/runs/{run_id}/events/stream",
            headers={"Last-Event-ID": "not-a-sequence"},
        )
        assert invalid.status_code == 422
    await app.state.database.close()


def test_sse_encoding_uses_sequence_as_reconnect_cursor() -> None:
    event = EventView.model_validate(
        {
            "id": "evt_test",
            "run_id": "run_test",
            "task_id": None,
            "sequence": 7,
            "type": "workflow.user_input.requested",
            "payload": {"checkpoint": "interview_scaffold", "label": "中文"},
            "created_at": "2026-07-31T00:00:00Z",
        }
    )

    encoded = encode_sse_event(event)

    assert encoded.startswith("id: 7\nevent: trace\ndata: ")
    assert '"type":"workflow.user_input.requested"' in encoded
    assert "中文" in encoded
    assert encoded.endswith("\n\n")


async def test_sse_stops_before_reading_state_when_client_disconnects() -> None:
    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    class UnexpectedService:
        async def get_run(self, _: str) -> SimpleNamespace:
            raise AssertionError("a disconnected stream must not read Run state")

        async def list_events(self, _: str, *, after: int) -> list[EventView]:
            raise AssertionError(f"a disconnected stream must not replay after {after}")

    stream = stream_run_events(
        request=cast(Any, DisconnectedRequest()),
        service=cast(Any, UnexpectedService()),
        run_id="run_disconnected",
        after=9,
        poll_interval_seconds=0,
        heartbeat_seconds=0,
    )

    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_sse_emits_transport_heartbeat_without_persisting_fake_event() -> None:
    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    class WaitingService:
        async def get_run(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(status="waiting_for_user")

        async def list_events(self, _: str, *, after: int) -> list[EventView]:
            assert after == 4
            return []

    stream = stream_run_events(
        request=cast(Any, ConnectedRequest()),
        service=cast(Any, WaitingService()),
        run_id="run_waiting",
        after=4,
        poll_interval_seconds=0,
        heartbeat_seconds=0,
    )
    assert await anext(stream) == ": heartbeat 4\n\n"
    await stream.aclose()
