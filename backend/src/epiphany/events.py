from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from epiphany.models import Event, Run


async def append_event(
    session: AsyncSession,
    *,
    run_id: str,
    event_type: str,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    statement = (
        update(Run)
        .where(Run.id == run_id)
        .values(next_event_sequence=Run.next_event_sequence + 1)
        .returning(Run.next_event_sequence)
    )
    sequence = (await session.execute(statement)).scalar_one()
    event = Event(
        run_id=run_id,
        task_id=task_id,
        sequence=sequence,
        type=event_type,
        payload=payload or {},
    )
    session.add(event)
    return event
