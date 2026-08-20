from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from epiphany.interview_schemas import (
    GroundedStatement,
    InterviewQuestion,
    InterviewScaffoldOutput,
    InterviewSection,
    MaterialGap,
)
from epiphany.schemas import ArtifactView

INTERVIEW_SCAFFOLD_RESULT_KIND = "build_interview_scaffold_result"
INTERVIEW_SCAFFOLD_HUMAN_EDIT_KIND = "interview_scaffold_human_edit"
INTERVIEW_SCAFFOLD_HUMAN_EDIT_VERSION = "interview_scaffold_human_edit_v1"

ReferenceKey = tuple[str, str]


class InterviewScaffoldEditError(ValueError):
    code = "interview_scaffold_edit_invalid"


class InterviewScaffoldGroundingChanged(InterviewScaffoldEditError):
    code = "interview_scaffold_edit_grounding_changed"


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


class InterviewScaffoldEditRequest(BaseModel):
    """One explicit human edit of the Interview Scaffold's text fields."""

    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1, max_length=200)
    base_artifact_id: str = Field(min_length=1, max_length=200)
    scaffold: InterviewScaffoldOutput

    _normalize_submission = field_validator("submission_id")(_normalize_required_text)
    _normalize_base = field_validator("base_artifact_id")(_normalize_required_text)


class InterviewScaffoldHumanEdit(BaseModel):
    """Persisted, immutable human-edited version of an Interview Scaffold.

    The original ``build_interview_scaffold_result`` Artifact is never mutated;
    ``base_artifact_id`` links this edit back to it for provenance.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interview_scaffold_human_edit_v1"] = (
        INTERVIEW_SCAFFOLD_HUMAN_EDIT_VERSION
    )
    submission_id: str = Field(min_length=1, max_length=200)
    base_artifact_id: str = Field(min_length=1, max_length=200)
    editor_origin: Literal["human"] = "human"
    scaffold: InterviewScaffoldOutput


class InterviewScaffoldEditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotent_replay: bool
    edit: InterviewScaffoldHumanEdit
    artifact: ArtifactView


def _statement_reference_keys(
    statement: GroundedStatement | InterviewQuestion | InterviewSection | MaterialGap,
) -> list[ReferenceKey]:
    return [
        (reference.source_id, reference.source_segment_id)
        for reference in statement.source_refs
    ]


def scaffold_grounding_signature(scaffold: InterviewScaffoldOutput) -> dict[str, Any]:
    """Return the parts a human edit must preserve.

    This captures the title, the structural shape (number of sections, questions,
    known-context statements, and material gaps), and every ``source_refs`` list
    in stable order. Two scaffolds with the same signature differ only in
    human-facing text, so an edit cannot change citations, structure, or the
    topic-bound title.
    """

    return {
        "title": scaffold.title,
        "episode_intent": _statement_reference_keys(scaffold.episode_intent),
        "opening": _statement_reference_keys(scaffold.opening),
        "closing": _statement_reference_keys(scaffold.closing),
        "sections": [
            {
                "source_refs": _statement_reference_keys(section),
                "known_context": [
                    _statement_reference_keys(statement) for statement in section.known_context
                ],
                "transition": _statement_reference_keys(section.transition),
                "questions": [
                    _statement_reference_keys(question) for question in section.questions
                ],
            }
            for section in scaffold.sections
        ],
        "material_gaps": [_statement_reference_keys(gap) for gap in scaffold.material_gaps],
    }


def validate_scaffold_edit_preserves_grounding(
    *,
    base: InterviewScaffoldOutput,
    edited: InterviewScaffoldOutput,
) -> None:
    """Reject edits that change citations, structure, or the title."""

    if scaffold_grounding_signature(base) != scaffold_grounding_signature(edited):
        raise InterviewScaffoldGroundingChanged(
            "scaffold edits may change human-facing text only, not citations, "
            "structure, or the title"
        )
