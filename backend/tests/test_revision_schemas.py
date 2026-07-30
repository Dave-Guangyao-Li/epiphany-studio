from __future__ import annotations

import pytest
from pydantic import ValidationError

from epiphany.revision_schemas import (
    CreateDraftRevisionRequest,
    SelectedRevisionFeedback,
)


def test_revision_request_requires_actions_to_match_the_selected_inputs() -> None:
    with pytest.raises(
        ValidationError,
        match="add_supplemental_material must match source_ids",
    ):
        CreateDraftRevisionRequest(
            submission_id="revision-1",
            selected_actions=["add_supplemental_material"],
        )

    with pytest.raises(
        ValidationError,
        match="lower_target_duration must match target_duration_minutes",
    ):
        CreateDraftRevisionRequest(
            submission_id="revision-1",
            selected_actions=["lower_target_duration"],
        )

    with pytest.raises(
        ValidationError,
        match="apply_selected_feedback must match selected feedback",
    ):
        CreateDraftRevisionRequest(
            submission_id="revision-1",
            selected_actions=["apply_selected_feedback"],
        )


def test_revision_request_is_normalized_unique_and_explicit() -> None:
    parsed = CreateDraftRevisionRequest(
        submission_id="  ep0   revision-1 ",
        selected_actions=[
            "lower_target_duration",
            "apply_selected_feedback",
        ],
        selected_feedback_artifact_ids=["art_feedback"],
        selected_gap_codes=["  duration.coverage_low  "],
        target_duration_minutes=10,
        revision_instruction="  开场保留停顿感，第二段说得更口语。 ",
    )

    assert parsed.submission_id == "ep0 revision-1"
    assert parsed.selected_gap_codes == ["duration.coverage_low"]
    assert parsed.revision_instruction == "开场保留停顿感，第二段说得更口语。"

    with pytest.raises(ValidationError, match="items must be unique"):
        CreateDraftRevisionRequest(
            submission_id="revision-duplicate",
            selected_actions=[
                "apply_selected_feedback",
                "apply_selected_feedback",
            ],
            selected_feedback_artifact_ids=["art_feedback"],
        )


def test_revision_feedback_uses_the_persisted_user_feedback_vocabulary() -> None:
    selected = SelectedRevisionFeedback(
        artifact_id="art_feedback",
        feedback_origin="human",
        decision="needs_revision",
        overall_rating=3,
        voice_match_rating=2,
        recordability_rating=3,
        usefulness_rating=4,
        tone_fit_rating=3,
        would_record_as_is=False,
        observed_duration_minutes=6.8,
        comment="第二段还不像我平时说话。",
    )

    assert selected.decision == "needs_revision"
    assert selected.feedback_origin == "human"
