from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.editor_schemas import (
    BUILD_PODCAST_DRAFT,
    PodcastDraftTaskInput,
    validate_podcast_draft_output,
)
from epiphany.models import Artifact, Run, Task
from epiphany.research_schemas import EpisodeResearchPayload
from epiphany.runtime.editor_prompts import build_editor_prompt
from epiphany.runtime.orchestrator import GUIDED_REVISION_WORKFLOW_VERSION
from epiphany.writing_style_ab_schemas import (
    FrozenWritingStyleABInput,
    WritingStyleABArm,
)

EDITOR_MODEL = "deepseek-v4-flash"
REVIEWER_MODEL = "deepseek-v4-pro"
EDITOR_TEMPERATURE = 0.2
REVIEWER_TEMPERATURE = 0.0
PLANNED_LIVE_CALL_COUNT = 4


class WritingStyleABError(RuntimeError):
    code = "writing_style_ab_error"


class WritingStyleABRunNotFound(WritingStyleABError):
    code = "writing_style_ab_run_not_found"


class WritingStyleABSourceInvalid(WritingStyleABError):
    code = "writing_style_ab_source_invalid"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def database_url_for_path(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.expanduser().resolve()}"


def build_arm_inputs(
    frozen: FrozenWritingStyleABInput,
) -> dict[WritingStyleABArm, PodcastDraftTaskInput]:
    """Derive the two arms while changing only the writing-style context."""

    with_sample = PodcastDraftTaskInput.model_validate(
        frozen.editor_task_input.model_dump(mode="json")
    )
    without_sample_payload = with_sample.model_dump(mode="json")
    without_sample_payload["writing_style_profile"] = None
    without_sample_payload["writing_style_segments"] = None
    without_sample = PodcastDraftTaskInput.model_validate(without_sample_payload)
    return {
        "without_sample": without_sample,
        "with_sample": with_sample,
    }


def _common_editor_payload(task_input: PodcastDraftTaskInput) -> dict[str, Any]:
    return task_input.model_dump(
        mode="json",
        exclude={"writing_style_profile", "writing_style_segments"},
    )


def build_preflight(
    *,
    frozen: FrozenWritingStyleABInput,
    max_editor_bundle_chars: int,
    max_editor_tokens: int,
    max_quality_bundle_chars: int,
    max_quality_tokens: int,
    api_key_present: bool,
    database_path: Path,
) -> dict[str, Any]:
    """Build a content-free, zero-network proof that the experiment is controlled."""

    arms = build_arm_inputs(frozen)
    common_hashes = {
        arm: _sha256(_common_editor_payload(task_input)) for arm, task_input in arms.items()
    }
    prompt_hashes = {
        arm: _sha256(
            build_editor_prompt(
                task_input=task_input.model_dump(mode="json"),
                max_bundle_chars=max_editor_bundle_chars,
            ).messages
        )
        for arm, task_input in arms.items()
    }
    treatment_reaches_prompt = prompt_hashes["without_sample"] != prompt_hashes["with_sample"]
    with_sample = arms["with_sample"]
    style_payload = {
        "writing_style_profile": with_sample.writing_style_profile.model_dump(mode="json"),
        "writing_style_segments": [
            segment.model_dump(mode="json") for segment in with_sample.writing_style_segments or []
        ],
    }
    only_variable_is_writing_sample = (
        common_hashes["without_sample"] == common_hashes["with_sample"]
        and arms["without_sample"].writing_style_profile is None
        and arms["without_sample"].writing_style_segments is None
        and arms["with_sample"].writing_style_profile is not None
        and bool(arms["with_sample"].writing_style_segments)
    )
    if not only_variable_is_writing_sample:
        raise WritingStyleABSourceInvalid("A/B arms differ outside the writing-style context")
    if not treatment_reaches_prompt:
        raise WritingStyleABSourceInvalid(
            "writing-style treatment does not change the Editor prompt"
        )

    common_experiment_contract = {
        "editor_input_without_style": _common_editor_payload(arms["with_sample"]),
        "quality_config": frozen.quality_config.model_dump(mode="json"),
        "editor": {
            "model": EDITOR_MODEL,
            "temperature": EDITOR_TEMPERATURE,
            "max_tokens": max_editor_tokens,
            "max_bundle_chars": max_editor_bundle_chars,
        },
        "reviewer": {
            "model": REVIEWER_MODEL,
            "temperature": REVIEWER_TEMPERATURE,
            "max_tokens": max_quality_tokens,
            "max_bundle_chars": max_quality_bundle_chars,
            "shared_style_context_sha256": _sha256(style_payload),
        },
    }

    profile = frozen.editor_task_input.writing_style_profile
    assert profile is not None
    return {
        "event": "writing_style_ab.preflight",
        "slice": "m3.7a_frozen_input_only",
        "mode": "dry-run",
        "network_enabled": False,
        "paid_api_call_possible": False,
        "provider_calls_executed": 0,
        "api_key_status": "present" if api_key_present else "absent",
        "source_run_id": frozen.source_run_id,
        "database_path": str(database_path),
        "only_variable_is_writing_sample": only_variable_is_writing_sample,
        "treatment_reaches_editor_prompt": treatment_reaches_prompt,
        "common_editor_input_sha256": common_hashes["without_sample"],
        "common_experiment_contract_sha256": _sha256(common_experiment_contract),
        "style_context_sha256": _sha256(style_payload),
        "arm_prompt_sha256": prompt_hashes,
        "writing_style_readiness": profile.readiness.status,
        "writing_style_source_count": profile.stats.source_count,
        "writing_style_segment_count": profile.stats.segment_count,
        "writing_style_non_whitespace_char_count": (profile.stats.non_whitespace_char_count),
        "planned_live_protocol": {
            "implemented_in_this_slice": False,
            "editor_model": EDITOR_MODEL,
            "editor_temperature": EDITOR_TEMPERATURE,
            "editor_max_tokens": max_editor_tokens,
            "editor_max_bundle_chars": max_editor_bundle_chars,
            "reviewer_model": REVIEWER_MODEL,
            "reviewer_temperature": REVIEWER_TEMPERATURE,
            "reviewer_max_tokens": max_quality_tokens,
            "reviewer_max_bundle_chars": max_quality_bundle_chars,
            "editor_calls": 2,
            "reviewer_calls": 2,
            "total_calls": PLANNED_LIVE_CALL_COUNT,
            "sample_sent_call_count": 3,
            "human_blind_rating_is_primary": True,
        },
        "privacy": {
            "contains_source_text": False,
            "contains_writing_sample_text": False,
            "contains_prompt_text": False,
            "contains_api_key": False,
        },
    }


async def load_frozen_input_from_run(
    *,
    database_url: str,
    run_id: str,
) -> FrozenWritingStyleABInput:
    """Load one completed v8 Run without changing it or re-running upstream Agents."""

    database = Database(database_url, read_only=True)
    try:
        async with database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise WritingStyleABRunNotFound("source Run does not exist")
            if (
                run.workflow_type != "episode-research"
                or run.workflow_version != GUIDED_REVISION_WORKFLOW_VERSION
                or run.status != "succeeded"
                or run.output_artifact_id is None
            ):
                raise WritingStyleABSourceInvalid(
                    "source Run must be a completed workflow-v8 episode-research Run"
                )

            tasks = list(
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.run_id == run_id,
                            Task.kind == BUILD_PODCAST_DRAFT,
                        )
                        .order_by(Task.created_at, Task.id)
                    )
                )
                .scalars()
                .all()
            )
            if len(tasks) != 1:
                raise WritingStyleABSourceInvalid(
                    "source Run must contain exactly one original Editor task"
                )
            task = tasks[0]
            if task.status != "succeeded" or task.output_artifact_id is None:
                raise WritingStyleABSourceInvalid(
                    "source Editor task must have completed successfully"
                )
            artifact = await session.get(Artifact, task.output_artifact_id)
            if (
                artifact is None
                or artifact.run_id != run_id
                or artifact.kind != f"{BUILD_PODCAST_DRAFT}_result"
                or run.output_artifact_id != artifact.id
            ):
                raise WritingStyleABSourceInvalid(
                    "source Editor output Artifact is missing or inconsistent"
                )

            try:
                payload = EpisodeResearchPayload.model_validate(
                    {
                        field_name: run.input_json[field_name]
                        for field_name in EpisodeResearchPayload.model_fields
                        if field_name in run.input_json
                    }
                )
                editor_input = PodcastDraftTaskInput.model_validate(task.input_json)
                if (
                    payload.creative_brief is None
                    or payload.draft_quality is None
                    or not payload.draft_quality.enabled
                    or payload.writing_style_reference is None
                ):
                    raise ValueError("source Run has no consented writing-style contract")
                if editor_input.topic != payload.topic:
                    raise ValueError("Editor topic differs from the persisted Run topic")
                if editor_input.creative_brief != payload.creative_brief:
                    raise ValueError(
                        "Editor Creative Brief differs from the persisted Run contract"
                    )
                style_source_ids = {
                    sample.source_id for sample in payload.writing_style_reference.samples
                }
                selected_style_source_ids = {
                    segment.source_id for segment in editor_input.writing_style_segments or []
                }
                if not selected_style_source_ids or not (
                    selected_style_source_ids <= style_source_ids
                ):
                    raise ValueError(
                        "Editor style segments do not match the consented style Sources"
                    )
                persisted_profile = run.input_json.get("writing_style_profile")
                if (
                    persisted_profile is None
                    or editor_input.writing_style_profile is None
                    or editor_input.writing_style_profile.model_dump(mode="json")
                    != persisted_profile
                ):
                    raise ValueError("Editor style profile differs from the persisted Run profile")
                draft_content = {
                    key: value
                    for key, value in artifact.content_json.items()
                    if key != "_execution"
                }
                validate_podcast_draft_output(
                    task_input=editor_input.model_dump(mode="json"),
                    content=draft_content,
                )
                return FrozenWritingStyleABInput(
                    source_run_id=run_id,
                    editor_task_input=editor_input,
                    quality_config=payload.draft_quality,
                )
            except (ValidationError, ValueError, TypeError) as error:
                raise WritingStyleABSourceInvalid(
                    "source Run does not contain a valid, consented, ready A/B input"
                ) from error
    finally:
        await database.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove that a completed v8 Run can support a controlled writing-style A/B. "
            "M3.7a is always a zero-network dry run and writes no experiment artifacts."
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/epiphany.db"),
    )
    return parser


def _print_json(value: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=2),
        file=sys.stdout if stream is None else stream,
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend_dir = Path(__file__).resolve().parents[2]
    settings = Settings(_env_file=backend_dir / ".env")
    api_key_present = bool(
        settings.deepseek_api_key and settings.deepseek_api_key.get_secret_value().strip()
    )
    database_path = args.database.expanduser().resolve()
    try:
        frozen = asyncio.run(
            load_frozen_input_from_run(
                database_url=database_url_for_path(database_path),
                run_id=args.run_id,
            )
        )
        preflight = build_preflight(
            frozen=frozen,
            max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
            max_editor_tokens=settings.deepseek_editor_max_tokens,
            max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
            max_quality_tokens=settings.deepseek_quality_review_max_tokens,
            api_key_present=api_key_present,
            database_path=database_path,
        )
    except Exception as error:
        _print_json(
            {
                "event": "writing_style_ab.blocked",
                "mode": "dry-run",
                "network_enabled": False,
                "provider_calls_executed": 0,
                "error_code": getattr(error, "code", "writing_style_ab_source_invalid"),
                "error_type": type(error).__name__,
                "message": "The source Run could not be frozen for a controlled A/B.",
            },
            stream=sys.stderr,
        )
        return 2
    _print_json(preflight)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
