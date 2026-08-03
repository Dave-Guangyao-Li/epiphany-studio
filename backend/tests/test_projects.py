from __future__ import annotations

from pathlib import Path

import httpx

from epiphany.config import Settings
from epiphany.main import create_app


def _source_body(*, title: str, text: str, source_type: str = "journal") -> dict[str, object]:
    return {
        "title": title,
        "source_type": source_type,
        "text": text,
        "metadata": {"synthetic": True},
    }


async def _new_project(client: httpx.AsyncClient, title: str) -> dict[str, object]:
    response = await client.post(
        "/projects",
        json={"title": title, "description": "本地测试工作区"},
    )
    assert response.status_code == 201
    return response.json()


async def test_project_source_crud_and_duplicate_link_are_durable(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'projects.db'}"
    app = create_app(
        settings=Settings(
            database_url=database_url,
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = await _new_project(client, "  Epiphany   第一季  ")
        project_id = project["id"]
        assert project["title"] == "Epiphany 第一季"
        assert project["source_count"] == 0
        assert project["run_count"] == 0

        first = await client.post(
            f"/projects/{project_id}/sources",
            json=_source_body(title="旧录音日记", text="雨落在空调外机上。我按下录音键。"),
        )
        assert first.status_code == 201
        assert first.json()["created"] is True
        assert first.json()["linked"] is True
        source_id = first.json()["source"]["id"]

        replay = await client.post(
            f"/projects/{project_id}/sources",
            json=_source_body(title="标题不会覆盖", text="雨落在空调外机上。我按下录音键。"),
        )
        assert replay.status_code == 200
        assert replay.json()["created"] is False
        assert replay.json()["linked"] is False
        assert replay.json()["source"]["id"] == source_id

        listed = await client.get("/projects")
        assert listed.status_code == 200
        assert listed.json()[0]["source_count"] == 1

        detail = await client.get(f"/projects/{project_id}")
        assert detail.status_code == 200
        assert [source["id"] for source in detail.json()["sources"]] == [source_id]
        assert detail.json()["runs"] == []

    await app.state.database.close()

    # A fresh application instance can discover the same workspace after refresh/restart.
    restarted = create_app(
        settings=Settings(
            database_url=database_url,
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    restarted_transport = httpx.ASGITransport(app=restarted)
    async with httpx.AsyncClient(
        transport=restarted_transport,
        base_url="http://test",
    ) as client:
        detail = await client.get(f"/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["source_count"] == 1
    await restarted.state.database.close()


async def test_project_run_requires_linked_sources_and_is_idempotent(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'project-runs.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_project = await _new_project(client, "第一季")
        second_project = await _new_project(client, "第二季")
        first_project_id = first_project["id"]
        second_project_id = second_project["id"]

        factual = (
            await client.post(
                f"/projects/{first_project_id}/sources",
                json=_source_body(title="事实素材", text="五年前我第一次录下自己的声音。"),
            )
        ).json()["source"]
        style = (
            await client.post(
                f"/projects/{first_project_id}/sources",
                json=_source_body(
                    title="写作样本",
                    text="我不太想急着总结。先把窗外的雨声留下来，再慢慢说。",
                    source_type="writing_sample",
                ),
            )
        ).json()["source"]
        foreign_source = (
            await client.post(
                f"/projects/{second_project_id}/sources",
                json=_source_body(title="另一个项目的素材", text="这段素材不属于第一季。"),
            )
        ).json()["source"]

        base_payload = {
            "topic": "五年后重新打开播客",
            "source_ids": [factual["id"]],
            "creative_brief": {},
            "writing_style_reference": {
                "samples": [
                    {"source_id": style["id"], "sample_kind": "written_prose"},
                ],
                "ownership_attested": True,
                "model_processing_consent": True,
                "usage": "style_only",
            },
        }
        request_body = {
            "submission_id": "ui-create-run-1",
            "workflow_type": "episode-research",
            "payload": base_payload,
        }
        created = await client.post(
            f"/projects/{first_project_id}/runs",
            json=request_body,
        )
        assert created.status_code == 201
        created_run = created.json()
        assert created_run["project_id"] == first_project_id

        replay = await client.post(
            f"/projects/{first_project_id}/runs",
            json=request_body,
        )
        assert replay.status_code == 200
        assert replay.headers["x-idempotent-replay"] == "true"
        assert replay.json()["id"] == created_run["id"]

        conflict_body = {
            **request_body,
            "payload": {**base_payload, "topic": "同一个 key 的另一期节目"},
        }
        conflict = await client.post(
            f"/projects/{first_project_id}/runs",
            json=conflict_body,
        )
        assert conflict.status_code == 409

        foreign_factual = await client.post(
            f"/projects/{first_project_id}/runs",
            json={
                "submission_id": "foreign-factual",
                "workflow_type": "episode-research",
                "payload": {
                    "topic": "不允许跨项目",
                    "source_ids": [foreign_source["id"]],
                },
            },
        )
        assert foreign_factual.status_code == 409
        assert foreign_source["id"] in foreign_factual.json()["detail"]

        foreign_style = await client.post(
            f"/projects/{first_project_id}/runs",
            json={
                "submission_id": "foreign-style",
                "workflow_type": "episode-research",
                "payload": {
                    **base_payload,
                    "writing_style_reference": {
                        **base_payload["writing_style_reference"],
                        "samples": [
                            {
                                "source_id": foreign_source["id"],
                                "sample_kind": "written_prose",
                            }
                        ],
                    },
                },
            },
        )
        assert foreign_style.status_code == 409

        runs = await client.get("/runs", params={"project_id": first_project_id})
        assert runs.status_code == 200
        assert [run["id"] for run in runs.json()] == [created_run["id"]]
        assert runs.json()[0]["project_id"] == first_project_id

        detail = await client.get(f"/projects/{first_project_id}")
        assert detail.status_code == 200
        assert detail.json()["run_count"] == 1
        assert detail.json()["runs"][0]["id"] == created_run["id"]

    await app.state.database.close()


async def test_ai_assisted_source_cannot_be_used_as_writing_style_identity(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'ai-style-boundary.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = await _new_project(client, "写作身份边界")
        project_id = project["id"]
        factual = (
            await client.post(
                f"/projects/{project_id}/sources",
                json=_source_body(title="事实素材", text="我在下雨的下午第一次录音。"),
            )
        ).json()["source"]
        ai_style = (
            await client.post(
                f"/projects/{project_id}/sources",
                json={
                    "title": "模型生成但伪装成写作样本",
                    "source_type": "writing_sample",
                    "text": "这段内容由模型生成，不能反过来定义用户本人的声音。",
                    "metadata": {"origin": "ai_assisted", "user_confirmed": True},
                },
            )
        ).json()["source"]

        rejected = await client.post(
            f"/projects/{project_id}/runs",
            json={
                "submission_id": "reject-ai-assisted-style",
                "workflow_type": "episode-research",
                "payload": {
                    "topic": "为什么要保护写作身份",
                    "source_ids": [factual["id"]],
                    "creative_brief": {},
                    "writing_style_reference": {
                        "samples": [
                            {
                                "source_id": ai_style["id"],
                                "sample_kind": "written_prose",
                            }
                        ],
                        "ownership_attested": True,
                        "model_processing_consent": True,
                        "usage": "style_only",
                    },
                },
            },
        )
        assert rejected.status_code == 422
        assert rejected.json() == {
            "detail": "AI-assisted Sources cannot be used as writing-style samples"
        }
        assert (await client.get(f"/projects/{project_id}")).json()["run_count"] == 0

    await app.state.database.close()
