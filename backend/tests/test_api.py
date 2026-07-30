from __future__ import annotations

from pathlib import Path

import httpx

from epiphany.config import Settings
from epiphany.main import create_app


async def test_create_and_read_run_api(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/runs",
            json={"payload": {"topic": "API smoke test"}},
        )
        assert response.status_code == 201
        assert response.headers["x-request-id"].startswith("req_")
        run_id = response.json()["id"]

        response = await client.get(
            f"/runs/{run_id}",
            headers={"x-request-id": "req_test_manual"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "req_test_manual"
        assert response.json()["status"] == "queued"
        assert response.json()["parent_run_id"] is None

        response = await client.get(f"/runs/{run_id}/events")
        assert response.status_code == 200
        assert [event["type"] for event in response.json()] == [
            "run.created",
            "task.queued",
        ]
    await app.state.database.close()
