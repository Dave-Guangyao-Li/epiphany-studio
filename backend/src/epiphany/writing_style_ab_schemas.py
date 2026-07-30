from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epiphany.editor_schemas import PodcastDraftTaskInput
from epiphany.quality_contract_schemas import DraftQualityConfig

WRITING_STYLE_AB_INPUT_VERSION = "writing_style_ab_input_v1"

WritingStyleABArm = Literal["without_sample", "with_sample"]


class FrozenWritingStyleABInput(BaseModel):
    """One immutable Editor bundle used to derive both experimental arms."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["writing_style_ab_input_v1"] = WRITING_STYLE_AB_INPUT_VERSION
    source_run_id: str = Field(min_length=1, max_length=64)
    editor_task_input: PodcastDraftTaskInput
    quality_config: DraftQualityConfig

    @model_validator(mode="after")
    def style_context_must_be_ready(self) -> FrozenWritingStyleABInput:
        profile = self.editor_task_input.writing_style_profile
        segments = self.editor_task_input.writing_style_segments
        if profile is None or segments is None:
            raise ValueError("A/B input requires an explicitly selected writing sample")
        if profile.readiness.status != "ready":
            raise ValueError("A/B input requires a ready writing-style profile")
        return self
