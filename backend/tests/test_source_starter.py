from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import epiphany.project_service as project_service_module
from epiphany.config import Settings
from epiphany.main import create_app
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.runtime.source_starter_prompts import build_source_starter_prompt
from epiphany.source_starter_schemas import BUILD_SOURCE_STARTER, validate_source_starter_output


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


class InvalidSourceStarterProvider(FakeProvider):
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
        provider=InvalidSourceStarterProvider(),
    )
    await invalid_app.state.database.create_schema()
    invalid_transport = httpx.ASGITransport(app=invalid_app)
    async with httpx.AsyncClient(transport=invalid_transport, base_url="http://test") as client:
        project_id = await _create_project(client)
        created = await client.post(
            f"/projects/{project_id}/source-starters",
            json=_starter_body(submission_id="invalid-starter"),
        )
        run_id = created.json()["id"]
        assert await invalid_app.state.worker.run_until_idle() == 1
        failed = (await client.get(f"/runs/{run_id}")).json()
        assert failed["status"] == "failed"
        task = failed["tasks"][0]
        assert task["error_code"] == "task_output_invalid"
        assert InvalidSourceStarterProvider.leaked_value not in task["error_message"]
        assert task["error_message"] == (
            "model output failed strict validation (task_output_invalid)"
        )
    await invalid_app.state.database.close()


async def test_source_starter_rejects_live_inferred_first_person_failure_without_leak(
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

        assert await app.state.worker.run_until_idle() == 1
        failed = (await client.get(f"/runs/{run_id}")).json()

        assert failed["status"] == "failed"
        assert failed["tasks"][0]["error_code"] == "task_output_invalid"
        assert failed["tasks"][0]["error_message"] == (
            "model output failed strict validation (task_output_invalid)"
        )
        assert UnsupportedFirstPersonSourceStarterProvider.leaked_assertion not in str(failed)
        assert "耳压" not in str(failed)

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
    assert "逐字复用" in system
    assert "[待补充：……]" in system
    assert "[待核实：……]" in system
    assert "潜水学习" in user
    assert "想探索一个陌生领域" in user


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
