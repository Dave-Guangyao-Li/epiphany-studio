from __future__ import annotations

import pytest
from pydantic import ValidationError

from epiphany.human_input_schemas import ResumeRunRequest


def test_resume_request_normalizes_valid_user_input() -> None:
    request = ResumeRunRequest.model_validate(
        {
            "checkpoint": "interview_scaffold",
            "submission_id": "  ep0   round 1  ",
            "source_ids": ["  src_first  ", "src_second"],
        }
    )

    assert request.model_dump() == {
        "checkpoint": "interview_scaffold",
        "submission_id": "ep0 round 1",
        "source_ids": ["src_first", "src_second"],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("submission_id", " \n\t "),
        ("source_ids", ["src_first", " \t "]),
    ],
)
def test_resume_request_rejects_blank_identifiers(field: str, value: object) -> None:
    payload = {
        "checkpoint": "interview_scaffold",
        "submission_id": "ep0-round-1",
        "source_ids": ["src_first"],
        field: value,
    }

    with pytest.raises(ValidationError):
        ResumeRunRequest.model_validate(payload)


def test_resume_request_rejects_source_ids_duplicated_after_normalization() -> None:
    with pytest.raises(ValidationError, match="source_ids must be unique"):
        ResumeRunRequest.model_validate(
            {
                "checkpoint": "interview_scaffold",
                "submission_id": "ep0-round-1",
                "source_ids": ["src_first", " src_first "],
            }
        )


def test_resume_request_rejects_unknown_fields_and_checkpoint() -> None:
    valid_payload = {
        "checkpoint": "interview_scaffold",
        "submission_id": "ep0-round-1",
        "source_ids": ["src_first"],
    }

    with pytest.raises(ValidationError):
        ResumeRunRequest.model_validate({**valid_payload, "unexpected": True})

    with pytest.raises(ValidationError):
        ResumeRunRequest.model_validate({**valid_payload, "checkpoint": "editor_review"})


def test_resume_request_enforces_source_count_bounds() -> None:
    valid_payload = {
        "checkpoint": "interview_scaffold",
        "submission_id": "ep0-round-1",
    }

    with pytest.raises(ValidationError):
        ResumeRunRequest.model_validate({**valid_payload, "source_ids": []})

    with pytest.raises(ValidationError):
        ResumeRunRequest.model_validate(
            {
                **valid_payload,
                "source_ids": [f"src_{index}" for index in range(21)],
            }
        )
