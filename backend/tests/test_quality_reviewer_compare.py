from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from epiphany.db import Database
from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_deterministic_quality_facts,
)
from epiphany.draft_quality_schemas import REVIEW_DIMENSIONS, REVIEW_PODCAST_DRAFT
from epiphany.models import Artifact, Run, Task
from epiphany.quality_contract_schemas import CreativeBrief
from epiphany.quality_reviewer_compare import (
    COMPARISON_INPUT_SCHEMA_VERSION,
    TRUSTED_REVIEWER_MODELS,
    ComparisonProviderInvalid,
    FrozenReviewerComparisonInput,
    build_preflight,
    compare_reviewers,
    comparison_bundle_sha256,
    database_url_for_path,
    deterministic_result_sha256,
    frozen_input_sha256,
    load_comparison_input_from_run,
    main,
)
from epiphany.runtime.providers import ProviderResponseError, ProviderResult, TaskInvocation


def _reference(index: int) -> dict[str, str]:
    return {
        "source_id": f"src_{index}",
        "source_segment_id": f"seg_{index}",
    }


def _grounded(text: str, index: int) -> dict[str, object]:
    return {"text": text, "source_refs": [_reference(index)]}


def _draft() -> dict[str, object]:
    return {
        "title": "五年后重新打开播客",
        "podcast_script": {
            "opening": _grounded(
                "五年前，我在一个下雨的下午录下了第一段音频。",
                0,
            ),
            "sections": [
                {
                    "title": "重新听见过去",
                    "source_refs": [_reference(0)],
                    "paragraphs": [
                        _grounded(
                            "听到那三秒停顿时，我意识到自己怀念的是仍愿意开口的人。",
                            0,
                        )
                    ],
                },
                {
                    "title": "先开始，再慢慢修改",
                    "source_refs": [_reference(1)],
                    "paragraphs": [
                        _grounded(
                            "我决定每一期只回答一个问题，并先完成一版能听的内容。",
                            1,
                        )
                    ],
                },
            ],
            "closing": _grounded("这一次，我想先把麦克风接上。", 1),
        },
        "show_notes": {
            "summary": _grounded("一段旧声音推动了一次重新开始。", 0),
            "key_points": [
                _grounded("声音如何保存语气、呼吸和停顿。", 0),
                _grounded("为什么不再等待完全准备好。", 1),
            ],
        },
    }


def _bundle() -> FrozenReviewerComparisonInput:
    creative_brief = CreativeBrief.model_validate(
        {
            "target_duration_minutes": 10,
            "scenario": "reflective_solo",
            "target_audience": "刚开始记录生活的普通人",
            "communication_goal": "解释为什么重新开始记录",
            "tone": ["真诚", "克制"],
            "must_include": ["旧声音", "重新开始"],
            "avoid_patterns": ["总而言之"],
        }
    )
    deterministic = analyze_podcast_draft(
        draft=_draft(),
        creative_brief=creative_brief,
    )
    task_input = {
        "task_kind": REVIEW_PODCAST_DRAFT,
        "draft_artifact_id": "art_draft",
        "deterministic_metrics_artifact_id": "art_metrics",
        "deterministic_quality_facts": build_deterministic_quality_facts(deterministic).model_dump(
            mode="json"
        ),
        "creative_brief": creative_brief.model_dump(mode="json"),
        "quality_config": {
            "enabled": True,
            "profile": "podcast_draft_v1",
        },
        "podcast_draft": _draft(),
        "allowed_source_refs": [_reference(0), _reference(1)],
        "referenced_source_segments": [
            {
                **_reference(0),
                "text": "录音里留下了窗外雨声，以及一句话后的三秒停顿。",
            },
            {
                **_reference(1),
                "text": "重新开始时，我给自己设定了只回答一个问题、先完成再润色的边界。",
            },
        ],
    }
    return FrozenReviewerComparisonInput.model_validate(
        {
            "schema_version": COMPARISON_INPUT_SCHEMA_VERSION,
            "task_input": task_input,
            "deterministic_result": deterministic.model_dump(mode="json"),
            "editor_execution": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
            "source_run_id": "run_frozen_quality",
        }
    )


def _review_output(score: int) -> dict[str, object]:
    dimensions: list[dict[str, object]] = []
    opening = _draft()["podcast_script"]["opening"]["text"]  # type: ignore[index]
    for dimension in REVIEW_DIMENSIONS:
        dimensions.append(
            {
                "dimension": dimension,
                "assessable": True,
                "score": score,
                "assessment": f"{dimension} 有可核对的初稿证据。",
                "limitation": None,
                "evidence": [
                    {
                        "location": "podcast_script.opening",
                        "exact_quote": opening,
                        "source_refs": (
                            [_reference(0)] if dimension == "source_faithfulness" else []
                        ),
                    }
                ],
            }
        )
    return {
        "review_kind": "model_self_review",
        "advisory": True,
        "dimensions": dimensions,
    }


class StubDeepSeekReviewer:
    name = "deepseek"
    billing_currency = "CNY"

    def __init__(self, *, model: str, score: int) -> None:
        self.model = model
        self.score = score
        self.invocations: list[TaskInvocation] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations.append(deepcopy(invocation))
        multiplier = 1 if self.model == "deepseek-v4-flash" else 3
        return ProviderResult(
            content=_review_output(self.score),
            provider=self.name,
            model=self.model,
            input_tokens=1_200,
            output_tokens=600,
            estimated_cost_micros=multiplier * 2_400,
            cost_currency=self.billing_currency,
        )


class InvalidSchemaDeepSeekReviewer(StubDeepSeekReviewer):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations.append(deepcopy(invocation))
        return ProviderResult(
            content={"review_kind": "model_self_review", "advisory": True, "dimensions": []},
            provider=self.name,
            model=self.model,
            input_tokens=1_111,
            output_tokens=222,
            estimated_cost_micros=3_333,
            cost_currency=self.billing_currency,
        )


class AccountedFailureDeepSeekReviewer(StubDeepSeekReviewer):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations.append(deepcopy(invocation))
        raise ProviderResponseError(
            "synthetic response failed after usage was parsed",
            accounting_result=ProviderResult(
                content={},
                provider=self.name,
                model=self.model,
                input_tokens=2_222,
                output_tokens=333,
                estimated_cost_micros=4_444,
                cost_currency=self.billing_currency,
            ),
        )


async def test_compare_reviewers_uses_one_frozen_input_without_regenerating_draft() -> None:
    bundle = _bundle()
    flash = StubDeepSeekReviewer(model="deepseek-v4-flash", score=4)
    pro = StubDeepSeekReviewer(model="deepseek-v4-pro", score=3)

    result = await compare_reviewers(
        bundle=bundle,
        providers={
            "deepseek-v4-flash": flash,
            "deepseek-v4-pro": pro,
        },
        max_bundle_chars=80_000,
    )

    assert result["passed"] is True
    assert result["protocol"]["podcast_draft_regenerated"] is False
    assert result["protocol"]["deterministic_origin"] == "persisted_artifact"
    assert result["protocol"]["call_order"] == list(TRUSTED_REVIEWER_MODELS)
    assert result["same_frozen_input_for_every_call"] is True
    assert result["frozen_input_sha256"] == frozen_input_sha256(bundle)
    assert result["deterministic_result_sha256"] == deterministic_result_sha256(bundle)
    assert result["comparison_bundle_sha256"] == comparison_bundle_sha256(bundle)
    assert flash.invocations[0].input_json == pro.invocations[0].input_json
    assert flash.invocations[0].kind == REVIEW_PODCAST_DRAFT
    assert [item["model"] for item in result["results"]] == list(TRUSTED_REVIEWER_MODELS)
    assert all(item["input_sha256"] == result["frozen_input_sha256"] for item in result["results"])
    assert len({item["prompt_sha256"] for item in result["results"]}) == 1
    assert result["results"][0]["raw_dimension_scores"] == {
        dimension: 4 for dimension in REVIEW_DIMENSIONS
    }
    assert result["results"][1]["raw_dimension_scores"] == {
        dimension: 3 for dimension in REVIEW_DIMENSIONS
    }
    assert result["results"][0]["input_tokens"] == 1_200
    assert result["results"][1]["estimated_cost_micros"] == 7_200
    assert result["comparison"]["model_score_delta_pro_minus_flash"] == -20.0
    # The intentionally short sample is a deterministic blocker. Neither model
    # can score-compensate its final decision into a publishable candidate.
    assert {item["decision"] for item in result["results"]} == {"blocked"}


async def test_schema_failure_still_records_safe_paid_usage_fields() -> None:
    bundle = _bundle()
    flash = InvalidSchemaDeepSeekReviewer(model="deepseek-v4-flash", score=4)
    pro = StubDeepSeekReviewer(model="deepseek-v4-pro", score=3)

    result = await compare_reviewers(
        bundle=bundle,
        providers={
            "deepseek-v4-flash": flash,
            "deepseek-v4-pro": pro,
        },
        max_bundle_chars=80_000,
    )

    assert result["passed"] is False
    assert [item["model"] for item in result["results"]] == ["deepseek-v4-pro"]
    assert len(result["failures"]) == 1
    failure = result["failures"][0]
    assert failure["model"] == "deepseek-v4-flash"
    assert failure["error_code"] == "model_self_review_schema_invalid"
    assert failure["input_tokens"] == 1_111
    assert failure["output_tokens"] == 222
    assert failure["estimated_cost_micros"] == 3_333
    assert failure["cost_currency"] == "CNY"
    assert failure["schema_issues"]


async def test_provider_error_still_records_parsed_paid_usage_fields() -> None:
    bundle = _bundle()
    flash = AccountedFailureDeepSeekReviewer(model="deepseek-v4-flash", score=4)
    pro = StubDeepSeekReviewer(model="deepseek-v4-pro", score=3)

    result = await compare_reviewers(
        bundle=bundle,
        providers={
            "deepseek-v4-flash": flash,
            "deepseek-v4-pro": pro,
        },
        max_bundle_chars=80_000,
    )

    assert result["passed"] is False
    failure = result["failures"][0]
    assert failure["error_code"] == "provider_response_invalid"
    assert failure["input_tokens"] == 2_222
    assert failure["output_tokens"] == 333
    assert failure["estimated_cost_micros"] == 4_444
    assert failure["cost_currency"] == "CNY"


async def test_compare_rejects_any_provider_outside_the_two_trusted_tiers() -> None:
    bundle = _bundle()
    flash = StubDeepSeekReviewer(model="deepseek-v4-flash", score=4)

    try:
        await compare_reviewers(
            bundle=bundle,
            providers={"deepseek-v4-flash": flash},
            max_bundle_chars=80_000,
        )
    except ComparisonProviderInvalid as error:
        assert error.code == "comparison_provider_invalid"
    else:
        raise AssertionError("comparison accepted an incomplete model set")


async def test_load_from_run_bounds_execution_metadata_to_provider_and_model(
    tmp_path: Path,
) -> None:
    source = _bundle()
    database_url = database_url_for_path(tmp_path / "comparison.db")
    database = Database(database_url)
    await database.create_schema()
    try:
        async with database.sessions() as session, session.begin():
            run = Run(
                id="run_frozen_quality",
                workflow_type="episode-research",
                workflow_version="v7",
                status="succeeded",
                current_step="complete",
                input_json={},
            )
            session.add(run)
            session.add_all(
                [
                    Artifact(
                        id="art_draft",
                        run_id=run.id,
                        kind="build_podcast_draft_result",
                        content_json={
                            **source.task_input.podcast_draft.model_dump(mode="json"),
                            "_execution": {
                                "provider": "deepseek",
                                "model": "deepseek-v4-flash",
                                "attempt": 1,
                            },
                        },
                        idempotency_key="comparison:draft",
                    ),
                    Artifact(
                        id="art_metrics",
                        run_id=run.id,
                        kind="draft_metrics_report",
                        content_json=source.deterministic_result.model_dump(mode="json"),
                        idempotency_key="comparison:metrics",
                    ),
                ]
            )
            session.add(
                Task(
                    id="task_reviewer",
                    run_id=run.id,
                    kind=REVIEW_PODCAST_DRAFT,
                    agent_type="quality_reviewer",
                    status="queued",
                    input_json=source.task_input.model_dump(mode="json"),
                    idempotency_key="comparison:reviewer",
                )
            )
    finally:
        await database.close()

    loaded = await load_comparison_input_from_run(
        database_url=database_url,
        run_id="run_frozen_quality",
    )

    assert loaded.editor_execution.model_dump() == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert loaded.task_input == source.task_input
    assert loaded.deterministic_result == source.deterministic_result

    recomputed = await load_comparison_input_from_run(
        database_url=database_url,
        run_id="run_frozen_quality",
        recompute_current_rules=True,
    )
    assert recomputed.deterministic_origin == "recomputed_current_rules"
    assert recomputed.task_input.deterministic_quality_facts == (
        build_deterministic_quality_facts(recomputed.deterministic_result)
    )


def test_preflight_is_dry_run_safe_and_never_accepts_a_key_value() -> None:
    preflight = build_preflight(
        execute=False,
        api_key_present=True,
        input_origin="persisted_run",
        frozen_sha256="a" * 64,
        deterministic_sha256="c" * 64,
        bundle_sha256="d" * 64,
        prompt_hash="b" * 64,
        output_path="artifacts/comparison.json",
        billing_currency="CNY",
    )

    assert preflight["mode"] == "dry-run"
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["api_key_status"] == "present"
    assert preflight["deterministic_origin"] == "persisted_artifact"
    assert preflight["deterministic_result_sha256"] == "c" * 64
    assert preflight["comparison_bundle_sha256"] == "d" * 64
    assert preflight["regenerates_podcast_draft"] is False
    assert "api_key" not in preflight


def test_execute_refuses_to_overwrite_existing_comparison_before_any_model_call(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    input_path = tmp_path / "frozen-input.json"
    output_path = tmp_path / "comparison.json"
    input_path.write_text(
        _bundle().model_dump_json(indent=2),
        encoding="utf-8",
    )
    output_path.write_text('{"do_not_replace": true}\n', encoding="utf-8")
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_API_KEY", "synthetic-never-used")

    exit_code = main(
        [
            "--input",
            str(input_path),
            "--execute",
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "comparison_output_exists" in captured.err
    assert output_path.read_text(encoding="utf-8") == '{"do_not_replace": true}\n'
