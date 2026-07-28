from __future__ import annotations

from typing import Any

from epiphany.interview_schemas import (
    BUILD_INTERVIEW_SCAFFOLD,
    validate_interview_scaffold_output,
)
from epiphany.research_schemas import validate_research_output


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
    return validate_research_output(
        task_kind=task_kind,
        task_input=task_input,
        content=content,
    )
