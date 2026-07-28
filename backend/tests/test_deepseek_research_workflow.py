from __future__ import annotations

import json

import httpx

from epiphany.db import Database
from epiphany.runtime.providers import DeepSeekProvider
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_service import SourceService

SYNTHETIC_SOURCE = "2019年第一次记录项目，2024年重新整理旧笔记。"


async def _create_run(
    database: Database,
    service: RunService,
) -> tuple[str, dict[str, str]]:
    imported = await SourceService(database).import_text(
        title="DeepSeek Mock 合成素材",
        source_type="podcast_draft",
        text=SYNTHETIC_SOURCE,
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [imported.source.id],
        },
    )
    return created.id, {
        "source_id": imported.source.id,
        "source_segment_id": imported.source.segments[0].id,
    }


def _task_kind(request: httpx.Request) -> str:
    body = json.loads(request.content)
    return body["messages"][1]["name"]


def _response_for(
    request: httpx.Request,
    reference: dict[str, str],
    *,
    invalid_reference: bool = False,
    finish_reason: str = "stop",
) -> dict:
    if invalid_reference:
        reference = {
            "source_id": "src_outside_scope",
            "source_segment_id": "seg_outside_scope",
        }

    task_kind = _task_kind(request)
    if task_kind == "timeline_research":
        content = {
            "timeline_events": [
                {
                    "label": "重新整理记录",
                    "description": "素材中出现了跨年份的记录节点。",
                    "time_expression": "2024年",
                    "confidence": 0.91,
                    "source_refs": [reference],
                }
            ],
            "open_questions": [],
        }
    elif task_kind == "theme_research":
        content = {
            "themes": [
                {
                    "theme": "持续记录",
                    "insight": "记录让不同年份的经历形成联系。",
                    "confidence": 0.88,
                    "source_refs": [reference],
                }
            ],
            "quotes": [
                {
                    "quote": SYNTHETIC_SOURCE,
                    "context": "测试严格逐字引用。",
                    "source_ref": reference,
                }
            ],
        }
    else:
        grounded_statement = {
            "text": "素材把不同年份的记录联系了起来。",
            "source_refs": [reference],
        }
        grounded_question = {
            "prompt": "重新整理这些记录时，你最先注意到了什么变化？",
            "purpose": "补充已有研究结果没有呈现的具体感受。",
            "keywords": ["记录", "变化"],
            "source_refs": [reference],
        }
        content = {
            "title": "五年后重新开始录播客",
            "episode_intent": {
                "text": "从已有证据出发，继续补充经历和认知变化。",
                "source_refs": [reference],
            },
            "opening": {
                "text": "这一次，我们从重新整理旧记录的时刻开始。",
                "source_refs": [reference],
            },
            "sections": [
                {
                    "title": "回到记录现场",
                    "source_refs": [reference],
                    "known_context": [grounded_statement],
                    "transition": grounded_statement,
                    "questions": [grounded_question],
                },
                {
                    "title": "理解后来的变化",
                    "source_refs": [reference],
                    "known_context": [grounded_statement],
                    "transition": grounded_statement,
                    "questions": [grounded_question],
                },
            ],
            "material_gaps": [],
            "closing": {
                "text": "新的细节出现时，再顺着它继续追问。",
                "source_refs": [reference],
            },
        }

    return {
        "id": "chatcmpl_runtime_test",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, ensure_ascii=False),
                },
            }
        ],
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 80,
        },
    }


async def _install_provider(
    worker: Worker,
    handler: httpx.MockTransport,
    *,
    billing_currency: str = "USD",
) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=handler)
    worker.provider = DeepSeekProvider(
        api_key="runtime-test-secret",
        billing_currency=billing_currency,
        client=client,
    )
    return client


async def test_mocked_deepseek_research_succeeds_end_to_end(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, reference = await _create_run(database, service)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_response_for(request, reference))

    client = await _install_provider(worker, httpx.MockTransport(handler))
    try:
        assert await worker.run_until_idle() == 3
        completed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert completed.status == "waiting_for_user"
    assert request_count == 3
    assert completed.model_call_count == 3
    assert len(completed.model_calls) == 3
    assert all(call.provider == "deepseek" for call in completed.model_calls)
    assert all(call.model == "deepseek-v4-flash" for call in completed.model_calls)
    assert all(call.status == "succeeded" for call in completed.model_calls)
    assert sum(call.input_tokens for call in completed.model_calls) == 240
    assert sum(call.output_tokens for call in completed.model_calls) == 60
    assert sum(call.estimated_cost_micros for call in completed.model_calls) == 51
    assert {artifact.kind for artifact in completed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
        "build_interview_scaffold_result",
    }


async def test_cny_estimate_is_persisted_in_model_call_ledger(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, reference = await _create_run(database, service)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_for(request, reference))

    client = await _install_provider(
        worker,
        httpx.MockTransport(handler),
        billing_currency="CNY",
    )
    try:
        assert await worker.run_until_idle() == 3
        completed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert completed.status == "waiting_for_user"
    assert len(completed.model_calls) == 3
    assert all(call.cost_currency == "CNY" for call in completed.model_calls)
    # Per call: 80 cache-miss input * CNY 1/M + 20 output * CNY 2/M.
    assert [call.estimated_cost_micros for call in completed.model_calls] == [120, 120, 120]


async def test_rate_limit_retries_in_worker_and_accounts_each_request(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_concurrency = 1
    run_id, reference = await _create_run(database, service)
    request_counts = {
        "timeline_research": 0,
        "theme_research": 0,
        "build_interview_scaffold": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        kind = _task_kind(request)
        request_counts[kind] += 1
        if kind == "timeline_research" and request_counts[kind] == 1:
            return httpx.Response(429, json={"error": {"message": "synthetic rate limit"}})
        return httpx.Response(200, json=_response_for(request, reference))

    client = await _install_provider(
        worker,
        httpx.MockTransport(handler),
        billing_currency="CNY",
    )
    try:
        assert await worker.run_until_idle() == 4
        completed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert completed.status == "waiting_for_user"
    assert request_counts == {
        "timeline_research": 2,
        "theme_research": 1,
        "build_interview_scaffold": 1,
    }
    assert completed.model_call_count == 4
    assert [call.status for call in completed.model_calls].count("failed") == 1
    assert [call.status for call in completed.model_calls].count("succeeded") == 3
    assert any(call.error_code == "provider_rate_limited" for call in completed.model_calls)
    assert all(call.cost_currency == "CNY" for call in completed.model_calls)


async def test_auth_failure_is_terminal_and_cancels_sibling(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_concurrency = 1
    run_id, _ = await _create_run(database, service)
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(401, json={"error": {"message": "synthetic auth failure"}})

    client = await _install_provider(
        worker,
        httpx.MockTransport(handler),
        billing_currency="CNY",
    )
    try:
        assert await worker.run_until_idle() == 1
        failed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert failed.status == "failed"
    assert request_count == 1
    assert failed.model_call_count == 1
    assert failed.model_calls[0].error_code == "provider_authentication_failed"
    assert failed.model_calls[0].cost_currency == "CNY"
    children = [task for task in failed.tasks if task.kind != "research_manager"]
    assert sorted(task.status for task in children) == ["cancelled", "failed"]
    failed_child = next(task for task in children if task.status == "failed")
    assert failed_child.attempt == 1
    assert failed_child.error_code == "provider_authentication_failed"


async def test_http_timeout_is_timed_out_and_bounded(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_concurrency = 1
    run_id, _ = await _create_run(database, service)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    client = await _install_provider(
        worker,
        httpx.MockTransport(handler),
        billing_currency="CNY",
    )
    try:
        assert await worker.run_until_idle() == 2
        failed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert failed.status == "failed"
    assert request_count == 2
    assert failed.model_call_count == 2
    assert all(call.status == "timed_out" for call in failed.model_calls)
    assert all(call.error_code == "provider_timeout" for call in failed.model_calls)
    assert all(call.cost_currency == "CNY" for call in failed.model_calls)


async def test_http_success_with_invalid_citation_fails_business_validation(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_concurrency = 1
    run_id, reference = await _create_run(database, service)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_for(request, reference, invalid_reference=True),
        )

    client = await _install_provider(worker, httpx.MockTransport(handler))
    try:
        assert await worker.run_until_idle() == 1
        failed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert failed.status == "failed"
    assert failed.model_calls[0].status == "succeeded"
    children = [task for task in failed.tasks if task.kind != "research_manager"]
    assert sorted(task.status for task in children) == ["cancelled", "failed"]
    assert next(task for task in children if task.status == "failed").error_code == (
        "invalid_source_reference"
    )


async def test_paid_but_truncated_response_keeps_usage_and_cost(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_concurrency = 1
    run_id, reference = await _create_run(database, service)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_for(
                request,
                reference,
                finish_reason="length",
            ),
        )

    client = await _install_provider(worker, httpx.MockTransport(handler))
    try:
        assert await worker.run_until_idle() == 1
        failed = await service.get_run(run_id)
    finally:
        await client.aclose()

    assert failed.status == "failed"
    call = failed.model_calls[0]
    assert call.status == "failed"
    assert call.error_code == "provider_output_truncated"
    assert call.input_tokens == 80
    assert call.output_tokens == 20
    assert call.estimated_cost_micros == 17


async def test_deepseek_mode_rejects_fake_workflow_without_http(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    client = await _install_provider(worker, httpx.MockTransport(handler))
    try:
        created = await service.create_run(
            workflow_type="fake-podcast",
            payload={"topic": "must remain offline"},
        )
        assert await worker.run_until_idle() == 1
        failed = await service.get_run(created.id)
    finally:
        await client.aclose()

    assert request_count == 0
    assert failed.status == "failed"
    assert failed.tasks[0].error_code == "research_prompt_invalid"
