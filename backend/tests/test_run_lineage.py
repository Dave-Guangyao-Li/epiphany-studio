from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from epiphany.db import Database
from epiphany.models import Run
from epiphany.services import RunService
from epiphany.state_machine import RunStatus


async def test_run_lineage_is_nullable_persisted_and_exposed(
    runtime: tuple[Database, RunService, object],
) -> None:
    database, service, _ = runtime

    async with database.sessions() as session, session.begin():
        parent = Run(
            workflow_type="episode-research",
            workflow_version="v7",
            status=RunStatus.SUCCEEDED,
            current_step="complete",
            input_json={"topic": "original draft"},
        )
        session.add(parent)
        await session.flush()

        child = Run(
            parent_run_id=parent.id,
            workflow_type="episode-research",
            workflow_version="v7",
            status=RunStatus.QUEUED,
            current_step="plan_revision",
            input_json={"topic": "guided revision"},
        )
        session.add(child)
        await session.flush()
        parent_run_id = parent.id
        child_run_id = child.id

    parent_view = await service.get_run(parent_run_id)
    child_view = await service.get_run(child_run_id)

    assert parent_view.parent_run_id is None
    assert child_view.parent_run_id == parent_run_id


async def test_run_lineage_rejects_unknown_parent(
    runtime: tuple[Database, RunService, object],
) -> None:
    database, _, _ = runtime

    async with database.sessions() as session:
        session.add(
            Run(
                parent_run_id="run_missing",
                workflow_type="episode-research",
                workflow_version="v7",
                status=RunStatus.QUEUED,
                current_step="plan_revision",
                input_json={"topic": "invalid lineage"},
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_run_lineage_schema_has_self_foreign_key_and_index(
    runtime: tuple[Database, RunService, object],
) -> None:
    database, _, _ = runtime

    async with database.engine.connect() as connection:
        foreign_keys = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_foreign_keys("runs")
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("runs")
        )

    assert any(
        foreign_key["name"] == "fk_runs_parent_run_id"
        and foreign_key["constrained_columns"] == ["parent_run_id"]
        and foreign_key["referred_table"] == "runs"
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in foreign_keys
    )
    assert any(
        index["name"] == "ix_runs_parent_run_id"
        and index["column_names"] == ["parent_run_id"]
        and index["unique"] == 0
        for index in indexes
    )
