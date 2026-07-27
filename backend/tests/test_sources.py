from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.main import create_app
from epiphany.schemas import SourceReference
from epiphany.source_service import SourceService


async def test_source_import_is_idempotent_and_logged(
    runtime: tuple[Database, object, object],
    caplog: object,
) -> None:
    database, _, _ = runtime
    service = SourceService(database)
    private_text = "大学的时候，我一直在准备出国。\n\n后来我去了美国。"

    with caplog.at_level(logging.INFO, logger="epiphany.source_service"):
        first = await service.import_text(
            title="成年十年素材",
            source_type="journal",
            text=private_text,
            metadata={"year": 2026},
        )
        duplicate = await service.import_text(
            title="重复上传不应创建新记录",
            source_type="journal",
            text=private_text,
            metadata={},
        )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.source.id == first.source.id
    assert duplicate.source.title == "成年十年素材"
    assert [segment.position for segment in first.source.segments] == [0, 1]
    assert len(await service.list_sources()) == 1
    assert private_text not in caplog.text
    assert [record.event for record in caplog.records[-2:]] == [
        "source.imported",
        "source.import.deduplicated",
    ]


async def test_concurrent_source_import_creates_one_source(
    runtime: tuple[Database, object, object],
) -> None:
    database, _, _ = runtime
    service = SourceService(database)

    results = await asyncio.gather(
        service.import_text(
            title="Concurrent A",
            source_type="journal",
            text="相同的并发导入内容。",
            metadata={},
        ),
        service.import_text(
            title="Concurrent B",
            source_type="journal",
            text="相同的并发导入内容。",
            metadata={},
        ),
    )

    assert sorted(result.created for result in results) == [False, True]
    assert results[0].source.id == results[1].source.id
    assert len(await service.list_sources()) == 1


async def test_source_survives_database_restart(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}"
    database = Database(database_url)
    await database.create_schema()
    service = SourceService(database)
    imported = await service.import_text(
        title="Restart source",
        source_type="podcast_draft",
        text="第一段。\n\n第二段。",
        metadata={},
    )
    await database.close()

    restarted_database = Database(database_url)
    restarted_service = SourceService(restarted_database)
    restored = await restarted_service.get_source(imported.source.id)

    assert restored.id == imported.source.id
    assert [segment.text for segment in restored.segments] == ["第一段。", "第二段。"]
    await restarted_database.close()


async def test_source_api_import_list_get_and_validation(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'source-api.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "title": "Episode 0",
            "source_type": "podcast_draft",
            "text": "五年后，我重新打开了这个播客。\n\n声音像一封跨越时间的信。",
            "metadata": {"episode": 0},
        }
        response = await client.post("/sources", json=payload)
        assert response.status_code == 201
        imported = response.json()
        source_id = imported["source"]["id"]
        assert imported["created"] is True
        assert imported["source"]["segment_count"] == 2
        assert "content_text" not in imported["source"]

        response = await client.post("/sources", json=payload)
        assert response.status_code == 200
        assert response.json()["created"] is False

        response = await client.get("/sources")
        assert response.status_code == 200
        assert response.json()[0]["id"] == source_id
        assert "segments" not in response.json()[0]

        response = await client.get(f"/sources/{source_id}")
        assert response.status_code == 200
        assert [segment["position"] for segment in response.json()["segments"]] == [0, 1]

        response = await client.get("/sources/src_missing")
        assert response.status_code == 404

        response = await client.post(
            "/sources",
            json={"title": "blank", "text": "  \n  "},
        )
        assert response.status_code == 422
    await app.state.database.close()


def test_source_reference_forbids_unexpected_fields() -> None:
    reference = SourceReference(
        source_id="src_test",
        source_segment_id="seg_test",
    )
    assert reference.source_segment_id == "seg_test"

    with pytest.raises(ValidationError):
        SourceReference(
            source_id="src_test",
            source_segment_id="seg_test",
            quote="must not be smuggled into a reference",
        )


def test_application_does_not_auto_create_schema_by_default() -> None:
    assert Settings(_env_file=None).create_schema_on_start is False
