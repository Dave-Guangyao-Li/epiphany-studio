from __future__ import annotations

from typing import Any

from epiphany.draft_quality_schemas import (
    REVIEW_PODCAST_DRAFT,
    validate_model_self_review_output,
)
from epiphany.editor_schemas import BUILD_PODCAST_DRAFT, validate_podcast_draft_output
from epiphany.interview_schemas import (
    BUILD_INTERVIEW_SCAFFOLD,
    validate_interview_scaffold_output,
)
from epiphany.research_schemas import THEME_RESEARCH, TIMELINE_RESEARCH, validate_research_output

UNVALIDATED_FAKE_TASKS = frozenset(
    {
        "prepare_sources",
        "fake_research",
        "assemble_artifact",
    }
)


class TaskOutputValidationMissing(ValueError):
    code = "task_output_validation_missing"


def validate_task_output(
    *,
    task_kind: str,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch strict validation at the Worker boundary for every Agent task."""

    if task_kind == BUILD_INTERVIEW_SCAFFOLD:
        return validate_interview_scaffold_output(
            task_input=task_input,
            content=content,
        )
    if task_kind == BUILD_PODCAST_DRAFT:
        return validate_podcast_draft_output(
            task_input=task_input,
            content=content,
        )
    if task_kind == REVIEW_PODCAST_DRAFT:
        return validate_model_self_review_output(
            task_input=task_input,
            content=content,
        )
    if task_kind in {TIMELINE_RESEARCH, THEME_RESEARCH}:
        return validate_research_output(
            task_kind=task_kind,
            task_input=task_input,
            content=content,
        )
    if task_kind in UNVALIDATED_FAKE_TASKS:
        return content
    raise TaskOutputValidationMissing(f"no output validator is registered for task: {task_kind}")
