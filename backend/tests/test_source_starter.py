from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import epiphany.project_service as project_service_module
from epiphany.config import Settings
from epiphany.main import create_app
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderNetworkError,
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.runtime.source_starter_prompts import build_source_starter_prompt
from epiphany.source_starter_schemas import (
    BUILD_SOURCE_STARTER,
    SOURCE_STARTER_TEXT_MAX_LENGTH,
    SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH,
    build_safe_source_starter_candidate,
    ground_source_starter_candidate,
    neutralize_source_starter_direct_quote_candidate,
    neutralize_source_starter_first_person_candidate,
    validate_source_starter_output,
)


async def _create_project(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/projects",
        json={"title": "潜水学习", "description": "想探索一个完全陌生的新领域"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _starter_body(**overrides: object) -> dict[str, object]:
    return {
        "submission_id": "starter-1",
        "source_title": "我为什么想了解潜水",
        "source_type": "journal",
        "mode": "starter_draft",
        "intent": "先找到自己真正好奇和担心的地方",
        **overrides,
    }


async def test_source_starter_is_durable_idempotent_and_never_auto_imports(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(),
        )
        assert created.status_code == 201
        queued = created.json()
        run_id = queued["id"]
        assert queued["workflow_type"] == "source-starter"
        assert queued["workflow_version"] == "v1"
        assert queued["current_step"] == "build_source_starter"
        assert queued["input_json"]["project"] == {
            "project_id": project_id,
            "title": "潜水学习",
            "description": "想探索一个完全陌生的新领域",
        }

        replay = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(),
        )
        assert replay.status_code == 200
        assert replay.headers["x-idempotent-replay"] == "true"
        assert replay.json()["id"] == run_id
        conflict = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(intent="另一个意图"),
        )
        assert conflict.status_code == 409

        before = await client.get(f"/projects/{project_id}")
        assert before.json()["source_count"] == 0

        assert await app.state.worker.run_until_idle() == 1
        waiting = (await client.get(f"/runs/{run_id}")).json()
        assert waiting["status"] == "waiting_for_user"
        assert waiting["current_step"] == "awaiting_source_confirmation"
        assert waiting["model_call_count"] == 1
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        content = candidate["content_json"]
        assert content["schema_version"] == "source-starter-candidate.v1"
        assert content["source_type"] == "journal"
        assert "[待补充：" in content["starter_text"]
        assert "[待核实：" in content["starter_text"]
        assert len(content["questions"]) >= 2
        assert content["safety"] == {
            "requires_user_confirmation": True,
            "factual_claims_require_verification": True,
        }

        after_generation = await client.get(f"/projects/{project_id}")
        assert after_generation.json()["source_count"] == 0

        confirmed_text = content["starter_text"].replace(
            "[待补充：第一次产生这个念头的具体时刻]",
            "去年在海边看见潜水课程招牌的时候",
        )
        confirm_body = {
            "submission_id": "confirm-1",
            "title": "我为什么想了解潜水",
            "source_type": "journal",
            "text": confirmed_text,
        }
        confirmed = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json=confirm_body,
        )
        assert confirmed.status_code == 201
        result = confirmed.json()
        assert result["idempotent_replay"] is False
        assert result["created"] is True
        assert result["linked"] is True
        assert result["candidate_artifact_id"] == candidate["id"]
        assert result["source"]["metadata"]["origin"] == "ai_assisted"
        assert result["source"]["metadata"]["user_confirmed"] is True
        assert result["source"]["metadata"]["source_starter_run_id"] == run_id

        confirm_replay = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json={**confirm_body, "submission_id": "confirm-after-response-loss"},
        )
        assert confirm_replay.status_code == 200
        assert confirm_replay.headers["x-idempotent-replay"] == "true"
        assert confirm_replay.json()["source"]["id"] == result["source"]["id"]

        changed_confirmation = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json={**confirm_body, "text": f"{confirmed_text}\n又改了一次。"},
        )
        assert changed_confirmation.status_code == 409

        project = (await client.get(f"/projects/{project_id}")).json()
        assert project["source_count"] == 1
        final_run = (await client.get(f"/runs/{run_id}")).json()
        assert final_run["status"] == "succeeded"
        assert final_run["current_step"] == "complete"
        assert any(
            artifact["kind"] == "source_starter_confirmation" for artifact in final_run["artifacts"]
        )
        confirmation = next(
            artifact
            for artifact in final_run["artifacts"]
            if artifact["kind"] == "source_starter_confirmation"
        )
        assert confirmation["content_json"]["submission_ids"] == [
            "confirm-1",
            "confirm-after-response-loss",
        ]
        events = (await client.get(f"/runs/{run_id}/events")).json()
        event_types = [event["type"] for event in events]
        assert "workflow.source_starter.completed" in event_types
        assert "run.waiting_for_user" in event_types
        assert event_types[-2:] == ["workflow.source_starter.confirmed", "run.succeeded"]

    await app.state.database.close()


class FailSourceStarterOnceProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == BUILD_SOURCE_STARTER and invocation.attempt == 1:
            raise RetryableProviderError("temporary source starter failure")
        return await super().generate(invocation)


class GenericInvalidExplorationSourceStarterProvider(FakeProvider):
    leaked_value = "MODEL_PRIVATE_VALUE_MUST_NOT_BE_PERSISTED"

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind != BUILD_SOURCE_STARTER:
            return result
        return ProviderResult(
            content={**result.content, "unexpected_model_field": self.leaked_value},
            provider=result.provider,
            model=result.model,
        )


class UnsupportedFirstPersonSourceStarterProvider(FakeProvider):
    leaked_assertion = "我担心耳压会让第一次潜水失败。"

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind != BUILD_SOURCE_STARTER:
            return result
        return ProviderResult(
            content={**result.content, "starter_text": self.leaked_assertion},
            provider=result.provider,
            model=result.model,
        )


class UnsupportedDirectQuoteSourceStarterProvider(FakeProvider):
    concept = "“失控感”"
    dialogue = "店员问：“还是这个？”"

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind != BUILD_SOURCE_STARTER:
            return result
        return ProviderResult(
            content={
                **result.content,
                "starter_text": (
                    "【AI 候选，不是事实记录】\n"
                    f"可以先比较{self.concept}与水下画面的吸引力。\n"
                    f"{self.dialogue}"
                ),
            },
            provider=result.provider,
            model=result.model,
        )


class UnavailableSourceStarterProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == BUILD_SOURCE_STARTER:
            raise ProviderNetworkError("source starter provider is unreachable")
        return await super().generate(invocation)


async def test_source_starter_retry_wait_cancel_and_validation_redaction(
    tmp_path: Path,
) -> None:
    retry_app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-retry.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        ),
        provider=FailSourceStarterOnceProvider(),
    )
    await retry_app.state.database.create_schema()
    retry_transport = httpx.ASGITransport(app=retry_app)
    async with httpx.AsyncClient(transport=retry_transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="retry-starter"),
        )
        run_id = created.json()["id"]
        assert await retry_app.state.worker.run_until_idle() == 2
        waiting = (await client.get(f"/runs/{run_id}")).json()
        assert waiting["status"] == "waiting_for_user"
        assert waiting["tasks"][0]["attempt"] == 2
        assert [call["status"] for call in waiting["model_calls"]] == [
            "failed",
            "succeeded",
        ]
        assert [
            event["type"] for event in (await client.get(f"/runs/{run_id}/events")).json()
        ].count("task.retry_scheduled") == 1

        cancelled = await client.post(f"/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert any(
            artifact["kind"] == "source_starter_candidate"
            for artifact in cancelled.json()["artifacts"]
        )
        rejected = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json={
                "submission_id": "confirm-after-cancel",
                "title": "不会导入",
                "source_type": "journal",
                "text": "取消以后不能确认。",
            },
        )
        assert rejected.status_code == 409
        assert (await client.get(f"/projects/{project_id}")).json()["source_count"] == 0
    await retry_app.state.database.close()

    invalid_app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-invalid.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        ),
        provider=GenericInvalidExplorationSourceStarterProvider(),
    )
    await invalid_app.state.database.create_schema()
    invalid_transport = httpx.ASGITransport(app=invalid_app)
    async with httpx.AsyncClient(transport=invalid_transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(
                submission_id="invalid-exploration-starter",
                mode="exploration_outline",
            ),
        )
        run_id = created.json()["id"]
        assert await invalid_app.state.worker.run_until_idle() == 2
        waiting = (await client.get(f"/runs/{run_id}")).json()

        assert waiting["status"] == "waiting_for_user"
        assert waiting["current_step"] == "awaiting_source_confirmation"
        task = waiting["tasks"][0]
        assert task["status"] == "succeeded"
        assert task["attempt"] == 2
        assert task["error_code"] is None
        assert GenericInvalidExplorationSourceStarterProvider.leaked_value not in str(waiting)
        assert [call["status"] for call in waiting["model_calls"]] == [
            "succeeded",
            "succeeded",
        ]
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        assert candidate["content_json"]["mode"] == "exploration_outline"
        assert "【探索提纲｜AI 候选问题地图" in candidate["content_json"]["starter_text"]
        assert candidate["content_json"]["_execution"] == {
            "provider": "fake",
            "model": "fake-v1",
            "attempt": 2,
            "fallback": "server_safe_template",
            "model_output_validation_error": "task_output_invalid",
        }
        events = (await client.get(f"/runs/{run_id}/events")).json()
        retry = [event for event in events if event["type"] == "task.retry_scheduled"]
        assert len(retry) == 1
        assert retry[0]["payload"]["error_code"] == "task_output_invalid"
    await invalid_app.state.database.close()


async def test_source_starter_neutralizes_first_person_after_bounded_live_validation_repair(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-first-person.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        ),
        provider=UnsupportedFirstPersonSourceStarterProvider(),
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="unsupported-first-person"),
        )
        run_id = created.json()["id"]

        assert await app.state.worker.run_until_idle() == 2
        waiting = (await client.get(f"/runs/{run_id}")).json()

        assert waiting["status"] == "waiting_for_user"
        assert waiting["current_step"] == "awaiting_source_confirmation"
        assert waiting["tasks"][0]["status"] == "succeeded"
        assert UnsupportedFirstPersonSourceStarterProvider.leaked_assertion not in str(waiting)
        assert "耳压" in str(waiting)
        assert [call["status"] for call in waiting["model_calls"]] == [
            "succeeded",
            "succeeded",
        ]
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        assert (
            "以下是 AI 提供的主题相关候选，不是用户事实"
            in candidate["content_json"]["starter_text"]
        )
        assert "担心耳压会让第一次潜水失败" in candidate["content_json"]["starter_text"]
        assert candidate["content_json"]["_execution"] == {
            "provider": "fake",
            "model": "fake-v1",
            "attempt": 2,
            "fallback": "server_line_grounding",
            "model_output_validation_error": "source_starter_unsupported_first_person",
        }
        assert [
            event["type"] for event in (await client.get(f"/runs/{run_id}/events")).json()
        ].count("task.retry_scheduled") == 1

    await app.state.database.close()


async def test_source_starter_neutralizes_concept_quotes_and_invented_dialogue(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-direct-quote.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        ),
        provider=UnsupportedDirectQuoteSourceStarterProvider(),
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="unsupported-direct-quote"),
        )
        run_id = created.json()["id"]

        assert await app.state.worker.run_until_idle() == 2
        waiting = (await client.get(f"/runs/{run_id}")).json()

        assert waiting["status"] == "waiting_for_user"
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        text = candidate["content_json"]["starter_text"]
        assert UnsupportedDirectQuoteSourceStarterProvider.concept not in text
        assert UnsupportedDirectQuoteSourceStarterProvider.dialogue not in text
        assert "以下是 AI 提供的主题相关候选，不是用户事实" in text
        assert "‹失控感›" in text
        assert "‹还是这个？›" in text
        assert "还是这个？" in text
        assert candidate["content_json"]["_execution"] == {
            "provider": "fake",
            "model": "fake-v1",
            "attempt": 2,
            "fallback": "server_line_grounding",
            "model_output_validation_error": "source_starter_unsupported_direct_quote",
        }

    await app.state.database.close()


async def test_source_starter_does_not_replace_provider_failure_with_safe_candidate(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-network-error.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        ),
        provider=UnavailableSourceStarterProvider(),
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="network-error-starter"),
        )
        run_id = created.json()["id"]

        assert await app.state.worker.run_until_idle() == 2
        failed = (await client.get(f"/runs/{run_id}")).json()

        assert failed["status"] == "failed"
        assert failed["tasks"][0]["error_code"] == "provider_network_error"
        assert failed["tasks"][0]["attempt"] == 2
        assert not any(
            artifact["kind"] == "source_starter_candidate" for artifact in failed["artifacts"]
        )
        assert [call["status"] for call in failed["model_calls"]] == [
            "failed",
            "failed",
        ]

    await app.state.database.close()


async def test_source_starter_confirmation_rolls_back_and_recovers_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-confirm-recovery.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="starter-confirm-recovery"),
        )
        run_id = created.json()["id"]
        assert await app.state.worker.run_until_idle() == 1
        waiting = (await client.get(f"/runs/{run_id}")).json()
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        body = {
            "submission_id": "confirm-before-response-loss",
            "title": "我为什么想了解潜水",
            "source_type": "journal",
            "text": candidate["content_json"]["starter_text"],
        }

        original_append_event = project_service_module.append_event

        async def crash_before_commit(*args: object, **kwargs: object) -> object:
            raise RuntimeError("injected confirmation crash")

        monkeypatch.setattr(project_service_module, "append_event", crash_before_commit)
        with pytest.raises(RuntimeError, match="injected confirmation crash"):
            await client.post(
                f"/projects/{project_id}/source-starters/{run_id}/confirm",
                json=body,
            )
        monkeypatch.setattr(project_service_module, "append_event", original_append_event)

        after_crash = (await client.get(f"/runs/{run_id}")).json()
        assert after_crash["status"] == "waiting_for_user"
        assert after_crash["current_step"] == "awaiting_source_confirmation"
        assert not any(
            artifact["kind"] == "source_starter_confirmation"
            for artifact in after_crash["artifacts"]
        )
        assert (await client.get(f"/projects/{project_id}")).json()["source_count"] == 0

        recovered = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json={**body, "submission_id": "confirm-after-process-restart"},
        )
        assert recovered.status_code == 201
        assert recovered.json()["idempotent_replay"] is False
        completed = (await client.get(f"/runs/{run_id}")).json()
        assert completed["status"] == "succeeded"
        assert (await client.get(f"/projects/{project_id}")).json()["source_count"] == 1

    await app.state.database.close()


@pytest.mark.parametrize("existing_source_type", ["writing_sample", "journal"])
async def test_source_starter_confirmation_rejects_existing_content_without_losing_provenance(
    tmp_path: Path,
    existing_source_type: str,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=(
                f"sqlite+aiosqlite:///{tmp_path / f'starter-collision-{existing_source_type}.db'}"
            ),
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id=f"collision-{existing_source_type}"),
        )
        run_id = created.json()["id"]
        assert await app.state.worker.run_until_idle() == 1
        waiting = (await client.get(f"/runs/{run_id}")).json()
        candidate = next(
            artifact
            for artifact in waiting["artifacts"]
            if artifact["kind"] == "source_starter_candidate"
        )
        collision_text = (
            f"{candidate['content_json']['starter_text']}\n碰撞测试：{existing_source_type}。"
        )

        existing = await client.post(
            f"/projects/{project_id}/sources",
            json={
                "title": "已经存在的人工素材",
                "source_type": existing_source_type,
                "text": collision_text,
                "metadata": {"origin": "user_imported", "synthetic": True},
            },
        )
        assert existing.status_code == 201
        existing_source = existing.json()["source"]

        rejected = await client.post(
            f"/projects/{project_id}/source-starters/{run_id}/confirm",
            json={
                "submission_id": "confirm-collision",
                "title": "AI 候选素材",
                "source_type": "journal",
                "text": collision_text,
            },
        )

        assert rejected.status_code == 409
        assert rejected.json() == {
            "detail": "confirmed content already exists as an incompatible Source"
        }
        unchanged = (await client.get(f"/sources/{existing_source['id']}")).json()
        assert unchanged["source_type"] == existing_source_type
        assert unchanged["metadata"] == {"origin": "user_imported", "synthetic": True}
        still_waiting = (await client.get(f"/runs/{run_id}")).json()
        assert still_waiting["status"] == "waiting_for_user"
        assert not any(
            artifact["kind"] == "source_starter_confirmation"
            for artifact in still_waiting["artifacts"]
        )

    await app.state.database.close()


async def test_source_starter_rejects_identity_and_capture_source_types(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'starter-types.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        for source_type in ("writing_sample", "voice_note_transcript"):
            response = await client.post(
                f"/projects/{project_id}/source-starters",
                json=_starter_body(
                    submission_id=f"starter-{source_type}",
                    source_type=source_type,
                ),
            )
            assert response.status_code == 422

    await app.state.database.close()


def test_source_starter_prompt_preserves_unknowns_and_forbids_fabrication() -> None:
    prompt = build_source_starter_prompt(
        task_input={
            "task_kind": "build_source_starter",
            "project": {
                "project_id": "proj_test",
                "title": "潜水学习",
                "description": "想探索一个陌生领域",
            },
            "source_title": None,
            "source_type": "other",
            "mode": "exploration_outline",
            "intent": None,
        }
    )
    system = prompt.messages[0]["content"]
    user = prompt.messages[1]["content"]
    assert "不得编造用户的第一人称经历" in system
    assert "每一个“我”" in system
    assert "只把省略/第三人称主语换成“我”" in system
    assert "exploration_outline 是问题地图" in system
    assert "[句式示例：……]" in system
    assert f"不超过 {SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH} 个汉字" in system
    assert "第一行必须明确写“AI 候选”" in system
    assert "你的价值不只是重复输入" in system
    assert "AI 提供的可选角度（不是用户事实）" in system
    assert "区分三类来源" in system
    assert "[待补充：……]" in system
    assert "[待核实：……]" in system
    assert "潜水学习" in user
    assert "想探索一个陌生领域" in user
    assert "个人入口、核心问题、可尝试/可观察的方向" in user


def test_source_starter_mode_prompts_are_materially_distinct() -> None:
    outline = build_source_starter_prompt(
        task_input=_validation_task_input(mode="exploration_outline")
    )
    draft = build_source_starter_prompt(task_input=_validation_task_input())

    outline_user = outline.messages[1]["content"]
    draft_user = draft.messages[1]["content"]
    assert "问题地图" not in outline_user  # the system owns the shared label
    assert "个人入口、核心问题、可尝试/可观察的方向" in outline_user
    assert "现场—发生变化的动作链" in draft_user
    assert "不要退化成研究提纲" in draft_user
    assert outline_user != draft_user


@pytest.mark.parametrize("mode", ["exploration_outline", "starter_draft"])
def test_safe_source_starter_candidate_is_useful_distinct_and_strictly_valid(
    mode: str,
) -> None:
    task_input = _validation_task_input(mode=mode)

    content = build_safe_source_starter_candidate(task_input=task_input)
    validated = validate_source_starter_output(task_input=task_input, content=content)
    text = str(validated["starter_text"])

    assert "AI 候选" in text
    assert "不是事实" in text
    assert "[待补充：" in text
    assert "[待核实：" in text
    if mode == "exploration_outline":
        assert "一、先找个人入口" in text
        assert "二、把主题拆成可以回答的问题" in text
        assert "[句式示例：" not in text
    else:
        assert "事情开始以前" in text
        assert "真正发生变化的是" in text
        assert "[句式示例：" in text
        assert "一、先找个人入口" not in text


@pytest.mark.parametrize("mode", ["exploration_outline", "starter_draft"])
def test_safe_source_starter_candidate_bounds_and_neutralizes_untrusted_context(
    mode: str,
) -> None:
    task_input = _validation_task_input(
        mode=mode,
        project={
            "project_id": "proj_guard",
            "title": "边界测试]",
            "description": "我确认的背景]不要提前结束占位符[" + ("甲" * 5_000),
        },
        source_title="素材]标题[",
        intent="我确认的方向]不要提前结束占位符[" + ("乙" * 2_000),
    )

    content = build_safe_source_starter_candidate(task_input=task_input)
    validated = validate_source_starter_output(task_input=task_input, content=content)
    text = str(validated["starter_text"])

    assert len(text) <= SOURCE_STARTER_TEXT_MAX_LENGTH
    assert "素材］标题［" in text
    assert "背景］不要提前结束占位符［" in text
    assert "方向］不要提前结束占位符［" in text
    assert "甲" * 2_500 not in text
    assert "乙" * 1_000 not in text


def test_source_starter_repair_prompt_prioritizes_grounding_over_prose() -> None:
    prompt = build_source_starter_prompt(
        task_input=_validation_task_input(),
        repair_attempt=True,
    )

    user = prompt.messages[1]["content"]
    assert "自动安全修复重试" in user
    assert "仍应提供有用、与主题直接相关的探索候选" in user
    assert "事实短句必须从输入逐字复制" in user
    assert "不能改成‘那一刻，我第一次有了归属感’" in user
    assert "不要新增任何第一人称陈述" in user
    assert "不要因为安全修复退化成与主题无关的万能模板" in user
    assert "[待补充：……]" in user


def _validation_task_input(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "task_kind": "build_source_starter",
        "project": {
            "project_id": "proj_guard",
            "title": "潜水学习",
            "description": "我完全不熟悉潜水，想先弄清安全和学习方向",
        },
        "source_title": "我为什么想了解潜水",
        "source_type": "journal",
        "mode": "starter_draft",
        "intent": "我想先记下真正吸引我的画面",
    }
    value.update(overrides)
    return value


def _validation_candidate(starter_text: str) -> dict[str, object]:
    return {
        "schema_version": "source-starter-candidate.v1",
        "mode": "starter_draft",
        "source_title": "我为什么想了解潜水",
        "source_type": "journal",
        "starter_text": starter_text,
        "questions": ["你最早在什么场景里想到它？", "你希望先弄清什么？"],
        "uncertainties": ["用户尚未提供该感受"],
        "safety": {
            "requires_user_confirmation": True,
            "factual_claims_require_verification": True,
        },
    }


def test_source_starter_guard_rejects_inferred_first_person_fact_without_leaking_text() -> None:
    unsupported = "我担心耳压会带来不适。"

    with pytest.raises(ValueError) as error:
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=_validation_candidate(unsupported),
        )

    message = str(error.value)
    assert "unsupported first-person assertion" in message
    assert "fragment 1" in message
    assert unsupported not in message
    assert "耳压" not in message


@pytest.mark.parametrize(
    "starter_text",
    [
        "我完全不熟悉潜水。",
        "我想先记下真正吸引我的画面。",
        "我为什么想了解潜水。",
        "我是否真的适合学习潜水。",
        "[待补充：我第一次想到潜水的场景]",
        "[待核实：我所在地区的入门要求]",
        "对于耳压或安全的担心是什么？",
    ],
)
def test_source_starter_guard_allows_only_grounded_questions_and_placeholders(
    starter_text: str,
) -> None:
    result = validate_source_starter_output(
        task_input=_validation_task_input(),
        content=_validation_candidate(starter_text),
    )

    assert result["starter_text"] == starter_text


@pytest.mark.parametrize(
    ("description", "intent", "starter_text"),
    [
        (
            "2025年从成都调到上海工作。",
            "2025年9月从成都搬到上海。",
            "2025年9月，我从成都搬到上海。",
        ),
        (
            "她最初住在临时公寓。",
            "临时公寓只有一张折叠桌。",
            "我最初住在临时公寓。",
        ),
        (
            "她下班后常绕远路走回家。",
            "下班后故意多走二十分钟。",
            "下班后我故意多走二十分钟。",
        ),
    ],
)
def test_source_starter_guard_allows_grounded_subject_projection(
    description: str,
    intent: str,
    starter_text: str,
) -> None:
    task_input = _validation_task_input()
    task_input["project"] = {
        "project_id": "proj_guard",
        "title": "搬到上海的前三个月",
        "description": description,
    }
    task_input["intent"] = intent
    candidate = _validation_candidate(starter_text)
    candidate["source_title"] = "我为什么总绕远路回家"
    task_input["source_title"] = candidate["source_title"]

    result = validate_source_starter_output(
        task_input=task_input,
        content=candidate,
    )

    assert result["starter_text"] == starter_text


def test_source_starter_guard_rejects_new_detail_during_subject_projection() -> None:
    task_input = _validation_task_input(intent="2025年9月从成都搬到上海。")
    unsupported = "我从成都搬到上海后每天都很孤独。"

    with pytest.raises(ValueError, match="unsupported first-person assertion"):
        validate_source_starter_output(
            task_input=task_input,
            content=_validation_candidate(unsupported),
        )


def test_source_starter_guard_allows_bounded_explicit_writing_example_only() -> None:
    example = (
        "[句式示例：锅里传来糊味时，我先关了火，又犹豫要不要点外卖；"
        "请替换成真实经历，这句话本身不是事实。]"
    )
    result = validate_source_starter_output(
        task_input=_validation_task_input(),
        content=_validation_candidate(example),
    )
    assert result["starter_text"] == example

    without_disclosure = "锅里传来糊味时，我先关了火，又犹豫要不要点外卖。"
    with pytest.raises(ValueError, match="unsupported first-person assertion"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=_validation_candidate(without_disclosure),
        )


def test_source_starter_writing_example_limit_matches_prompt_contract() -> None:
    disclosure = "请替换成真实经历，这不是事实。"
    body_at_limit = disclosure + (
        "甲" * (SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH - len(disclosure))
    )
    at_limit = f"[句式示例：{body_at_limit}]"
    result = validate_source_starter_output(
        task_input=_validation_task_input(),
        content=_validation_candidate(at_limit),
    )
    assert result["starter_text"] == at_limit

    over_limit = f"[句式示例：{body_at_limit}乙]"
    with pytest.raises(ValueError, match="writing example"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=_validation_candidate(over_limit),
        )


@pytest.mark.parametrize(
    "example",
    [
        "[句式示例：锅里传来糊味时，我先关了火。]",
        "[句式示例：" + "我会替换成真实经历" * 30 + "]",
        "\n".join(
            [
                "[句式示例：我会替换成真实经历，这不是事实。]",
                "[句式示例：我会替换成真实经历，这不是事实。]",
                "[句式示例：我会替换成真实经历，这不是事实。]",
            ]
        ),
    ],
)
def test_source_starter_guard_rejects_unbounded_or_undisclosed_writing_examples(
    example: str,
) -> None:
    with pytest.raises(ValueError, match="writing example"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=_validation_candidate(example),
        )


def test_source_starter_placeholder_remains_non_factual_with_internal_punctuation() -> None:
    placeholder = (
        "[待补充：请核对这段用户输入：我为什么想去18米？"
        "有人曾说开放水域证书允许下潜18米。确认后改写。]"
    )
    candidate = _validation_candidate(placeholder)

    result = validate_source_starter_output(
        task_input=_validation_task_input(),
        content=candidate,
    )

    assert result["starter_text"] == placeholder


def test_source_starter_guard_does_not_project_a_title_question_into_fact() -> None:
    task_input = _validation_task_input(
        project={
            "project_id": "proj_guard",
            "title": "我为什么会被潜水吸引？",
            "description": "想探索一个陌生领域。",
        },
        source_title="我为什么会被潜水吸引？",
        intent=None,
    )
    candidate = _validation_candidate("我会被潜水吸引。")
    candidate["source_title"] = task_input["source_title"]

    with pytest.raises(ValueError, match="unsupported first-person assertion"):
        validate_source_starter_output(task_input=task_input, content=candidate)


def test_source_starter_guard_rejects_invented_direct_quotation() -> None:
    task_input = _validation_task_input(intent="便利店店员记住常买无糖乌龙茶。")
    candidate = _validation_candidate("店员问：“还是这个？”")

    with pytest.raises(ValueError, match="unsupported direct quotation"):
        validate_source_starter_output(task_input=task_input, content=candidate)


def test_source_starter_guard_allows_neutral_exploration_outline() -> None:
    task_input = _validation_task_input(mode="exploration_outline", intent=None)
    candidate = _validation_candidate(
        "探索角度：最初的兴趣从哪里来？\n"
        "[待补充：我第一次注意到潜水的具体场景]\n"
        "如果涉及安全要求，保留[待核实：需要查证的专业信息]。"
    )
    candidate["mode"] = "exploration_outline"

    result = validate_source_starter_output(task_input=task_input, content=candidate)

    assert result["mode"] == "exploration_outline"


def test_source_starter_guard_allows_explicit_ai_brainstorming_and_self_inquiry() -> None:
    task_input = _validation_task_input(
        mode="exploration_outline",
        intent="我真正好奇的是什么、我对耳压和失控的担忧从哪里来",
    )
    candidate = _validation_candidate(
        "【探索提纲｜AI 候选，不是事实记录】\n"
        "## AI 提供的可选角度（不是用户事实）\n"
        "- 可能值得观察：水下画面的吸引力、陌生环境中的控制感、第一次尝试前的决策。\n"
        "- 我真正好奇的是什么\n"
        "- 我对耳压和失控的担忧从哪里来\n"
        "- 哪些专业术语或安全问题需要向可信来源查证？\n"
        "[待补充：从这些候选角度中选出最接近真实想法的一项，并写下原因]"
    )
    candidate["mode"] = "exploration_outline"

    result = validate_source_starter_output(task_input=task_input, content=candidate)

    assert "AI 提供的可选角度（不是用户事实）" in result["starter_text"]
    assert "水下画面的吸引力" in result["starter_text"]


@pytest.mark.parametrize(
    "unsupported",
    [
        "我最担心的是耳压。",
        "我对耳压和失控感到害怕。",
        "我真正喜欢的是水下的安静。",
    ],
)
def test_source_starter_guard_still_rejects_ai_suggestions_as_user_facts(
    unsupported: str,
) -> None:
    candidate = _validation_candidate(
        f"【AI 候选，不是事实记录】\n## AI 提供的可选角度（不是用户事实）\n{unsupported}"
    )

    with pytest.raises(ValueError, match="unsupported first-person assertion"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=candidate,
        )


def test_source_starter_local_neutralization_preserves_topics_not_user_facts() -> None:
    task_input = _validation_task_input()
    candidate = _validation_candidate(
        "【AI 候选，不是事实记录】\n"
        "- 可以先比较画面吸引力与现实顾虑。\n"
        "- 我担心耳压会让第一次潜水失败。"
    )
    candidate["questions"] = [
        "我已经在海南体验过潜水。",
        "哪些内容需要先查证？",
    ]
    candidate["uncertainties"] = ["我去年在海南完成了体验"]

    repaired = neutralize_source_starter_first_person_candidate(
        task_input=task_input,
        content=candidate,
    )
    validated = validate_source_starter_output(
        task_input=task_input,
        content=repaired,
    )

    rendered = str(validated)
    assert "我担心耳压会让第一次潜水失败" not in rendered
    assert "我已经在海南体验过潜水" not in rendered
    assert "我去年在海南完成了体验" not in rendered
    assert "担心耳压会让第一次潜水失败" in rendered
    assert "已经在海南体验过潜水" in rendered
    assert "去年在海南完成了体验" in rendered
    assert "以下内容由 AI 提出，不是用户事实" in rendered
    assert validated["questions"][0].endswith("？")
    assert "尚未由用户确认，需要补充或删除" in validated["uncertainties"][0]


def test_source_starter_quote_neutralization_distinguishes_label_from_dialogue() -> None:
    task_input = _validation_task_input(intent="标题里已经提供“潜水学习”这个词。")
    candidate = _validation_candidate(
        "【AI 候选，不是事实记录】\n"
        "输入原词“潜水学习”保持不变。\n"
        "AI 提供的可选角度是“对失控的担忧”。\n"
        "店员问：“还是这个？”"
    )

    repaired = neutralize_source_starter_direct_quote_candidate(
        task_input=task_input,
        content=candidate,
    )
    validated = validate_source_starter_output(
        task_input=task_input,
        content=repaired,
    )
    text = validated["starter_text"]

    assert "输入原词“潜水学习”保持不变" in text
    assert "“对失控的担忧”" not in text
    assert "〔AI 候选表述，并非用户原话：对失控的担忧〕" in text
    assert "店员问：“还是这个？”" not in text
    assert "未经确认的对话或引语，不是用户事实" in text
    assert "还是这个？" in text


def test_source_starter_line_grounding_handles_multiple_latent_safety_failures() -> None:
    task_input = _validation_task_input(mode="exploration_outline", intent=None)
    candidate = _validation_candidate(
        "【AI 候选，不是事实记录】\n"
        "AI 提供的可选角度：可以比较画面吸引力、控制感和待查证问题。\n"
        "我担心耳压会让第一次潜水失败。\n"
        "教练问：“准备好了吗？”\n"
        "研究数据显示成功率为95%。\n"
        "后来去了海边，开始认真考虑潜水。\n"
        "[句式示例：我已经在海边完成第一次体验。]"
    )
    candidate["mode"] = "exploration_outline"
    candidate["questions"] = ["最想先确认的知识", "哪些内容需要查证？"]
    candidate["uncertainties"] = ["潜水经历和安全顾虑"]

    repaired = ground_source_starter_candidate(
        task_input=task_input,
        content=candidate,
    )
    validated = validate_source_starter_output(
        task_input=task_input,
        content=repaired,
    )
    rendered = str(validated)

    assert "可以比较画面吸引力、控制感和待查证问题" in rendered
    assert "以下是 AI 提供的主题相关候选，不是用户事实" in rendered
    assert "担心耳压会让第一次潜水失败" in rendered
    assert "准备好了吗？" in rendered
    assert "成功率为95%" in rendered
    assert "后来去了海边" in rendered
    assert "我担心耳压会让第一次潜水失败" not in rendered
    assert "教练问：“准备好了吗？”" not in rendered
    assert validated["questions"][0].endswith("？")
    assert "尚未由用户确认，需要补充或删除" in validated["uncertainties"][0]


@pytest.mark.parametrize(
    ("field", "unsafe_value", "expected_rule"),
    [
        (
            "questions",
            "你在海南第一次潜水时最担心什么？",
            "unsupported personal-history presupposition",
        ),
        (
            "uncertainties",
            "用户曾在海边体验过潜水",
            "unsupported user-history assertion",
        ),
    ],
)
def test_source_starter_guard_covers_questions_and_uncertainties_without_leaking_text(
    field: str,
    unsafe_value: str,
    expected_rule: str,
) -> None:
    candidate = _validation_candidate("先从一个真实问题开始。")
    candidate[field] = (
        [unsafe_value, "你最想先弄清什么？"] if field == "questions" else [unsafe_value]
    )

    with pytest.raises(ValueError) as error:
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=candidate,
        )

    message = str(error.value)
    assert expected_rule in message
    assert unsafe_value not in message
    assert "海南" not in message
    assert "海边" not in message


def test_source_starter_exploration_outline_rejects_omitted_personal_history() -> None:
    unsafe = "第一次来到海边，看见潜水员以后开始认真考虑这件事。"
    task_input = _validation_task_input(mode="exploration_outline", intent=None)
    candidate = _validation_candidate(unsafe)
    candidate["mode"] = "exploration_outline"

    with pytest.raises(ValueError) as error:
        validate_source_starter_output(task_input=task_input, content=candidate)

    assert "unsupported personal-history assertion" in str(error.value)
    assert unsafe not in str(error.value)


def test_source_starter_guard_requires_external_fact_verification_marker() -> None:
    unsafe = "开放水域证书允许下潜18米。"
    candidate = _validation_candidate(unsafe)

    with pytest.raises(ValueError) as error:
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=candidate,
        )

    assert "unverified external factual assertion" in str(error.value)
    assert unsafe not in str(error.value)

    safe = _validation_candidate("[待核实：开放水域证书是否允许下潜18米]")
    result = validate_source_starter_output(
        task_input=_validation_task_input(),
        content=safe,
    )
    assert result["starter_text"] == safe["starter_text"]


def test_source_starter_guard_requires_questions_and_explicit_unknowns() -> None:
    not_a_question = _validation_candidate("先从一个真实问题开始。")
    not_a_question["questions"] = ["最早接触潜水的场景", "你想先弄清什么？"]
    with pytest.raises(ValueError, match="not phrased as a question"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=not_a_question,
        )

    hidden_claim = _validation_candidate("先从一个真实问题开始。")
    hidden_claim["uncertainties"] = ["潜水经历与安全顾虑"]
    with pytest.raises(ValueError, match="did not identify an unknown"):
        validate_source_starter_output(
            task_input=_validation_task_input(),
            content=hidden_claim,
        )
