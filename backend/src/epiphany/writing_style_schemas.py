from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WRITING_STYLE_REFERENCE_VERSION = "writing_style_reference_v1"
WRITING_STYLE_PROFILE_VERSION = "writing_style_profile_v1"
WRITING_STYLE_SELECTION_METHOD = "deterministic_round_robin_v1"
MAX_WRITING_SAMPLES = 5
MAX_STYLE_SEGMENTS = 20
MAX_STYLE_NON_WHITESPACE_CHARS = 12_000
MIN_READY_STYLE_CHARS = 800
MIN_READY_STYLE_SENTENCES = 5

WritingSampleKind = Literal["written_prose", "spoken_transcript"]
WritingStyleUsage = Literal["style_only"]
WritingStyleReadinessStatus = Literal["ready", "limited"]
WritingStyleReadinessGap = Literal[
    "insufficient_non_whitespace_chars",
    "insufficient_sentences",
]


def _normalize_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("identifier must contain non-whitespace characters")
    return normalized


class WritingSampleReference(BaseModel):
    """One user-selected Source used only as a writing-style sample."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    sample_kind: WritingSampleKind

    _normalize_source_id = field_validator("source_id")(_normalize_identifier)


class WritingStyleReference(BaseModel):
    """Explicit, consented opt-in contract for style processing.

    The ordered ``samples`` list is also the priority order used by the
    deterministic selector. No workflow may infer this contract merely because
    writing-like Sources happen to exist.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["writing_style_reference_v1"] = WRITING_STYLE_REFERENCE_VERSION
    samples: list[WritingSampleReference] = Field(
        min_length=1,
        max_length=MAX_WRITING_SAMPLES,
    )
    ownership_attested: Literal[True]
    model_processing_consent: Literal[True]
    usage: WritingStyleUsage = "style_only"

    @field_validator("samples")
    @classmethod
    def samples_must_reference_unique_sources(
        cls,
        value: list[WritingSampleReference],
    ) -> list[WritingSampleReference]:
        source_ids = [sample.source_id for sample in value]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("writing samples must reference unique source_ids")
        return value


class WritingStyleSegmentInput(BaseModel):
    """Ephemeral, untrusted Source text used to calculate a style profile.

    This input object may contain personal text. It must not be persisted as the
    profile or logged. The persisted profile contains only references, hashes,
    and aggregate statistics.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    source_segment_id: str = Field(min_length=1, max_length=200)
    position: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2_000_000)

    _normalize_ids = field_validator("source_id", "source_segment_id")(_normalize_identifier)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("style segment text must contain non-whitespace characters")
        return value


class WritingStyleSegmentReference(BaseModel):
    """Persistable provenance for one selected segment; never contains text."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_segment_id: str
    position: int = Field(ge=0)
    sample_kind: WritingSampleKind
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    non_whitespace_char_count: int = Field(ge=1)
    sentence_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=1)


class WritingStyleObservableStats(BaseModel):
    """Small, reproducible measurements, not a claim about authorship."""

    model_config = ConfigDict(extra="forbid")

    source_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    non_whitespace_char_count: int = Field(ge=0)
    sentence_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    written_prose_segment_count: int = Field(ge=0)
    spoken_transcript_segment_count: int = Field(ge=0)
    average_sentence_char_count: float | None = Field(default=None, ge=0)
    minimum_sentence_char_count: int | None = Field(default=None, ge=1)
    maximum_sentence_char_count: int | None = Field(default=None, ge=1)
    question_mark_count: int = Field(ge=0)
    exclamation_mark_count: int = Field(ge=0)


class WritingStyleReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WritingStyleReadinessStatus
    minimum_non_whitespace_char_count: int = MIN_READY_STYLE_CHARS
    minimum_sentence_count: int = MIN_READY_STYLE_SENTENCES
    observed_non_whitespace_char_count: int = Field(ge=0)
    observed_sentence_count: int = Field(ge=0)
    gaps: list[WritingStyleReadinessGap] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def status_and_gaps_must_agree(self) -> WritingStyleReadiness:
        expected_gaps: list[WritingStyleReadinessGap] = []
        if self.observed_non_whitespace_char_count < self.minimum_non_whitespace_char_count:
            expected_gaps.append("insufficient_non_whitespace_chars")
        if self.observed_sentence_count < self.minimum_sentence_count:
            expected_gaps.append("insufficient_sentences")
        expected_status = "ready" if not expected_gaps else "limited"
        if self.status != expected_status or self.gaps != expected_gaps:
            raise ValueError("writing style readiness status and gaps do not match observations")
        return self


class WritingStyleProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_version: Literal["writing_style_reference_v1"] = WRITING_STYLE_REFERENCE_VERSION
    selection_method: Literal["deterministic_round_robin_v1"] = WRITING_STYLE_SELECTION_METHOD
    requested_sample_count: int = Field(ge=1, le=MAX_WRITING_SAMPLES)
    candidate_segment_count: int = Field(ge=0)
    selected_segment_count: int = Field(ge=0, le=MAX_STYLE_SEGMENTS)
    excluded_segment_count: int = Field(ge=0)
    selected_source_count: int = Field(ge=0, le=MAX_WRITING_SAMPLES)
    maximum_segment_count: int = MAX_STYLE_SEGMENTS
    maximum_non_whitespace_char_count: int = MAX_STYLE_NON_WHITESPACE_CHARS
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WritingStyleSafetyPolicy(BaseModel):
    """Machine-readable boundary for all downstream prompt builders."""

    model_config = ConfigDict(extra="forbid")

    input_trust: Literal["untrusted"] = "untrusted"
    usage: WritingStyleUsage = "style_only"
    may_supply_factual_evidence: Literal[False] = False
    may_supply_instructions: Literal[False] = False


class WritingStyleProfile(BaseModel):
    """Persistable style profile with no copied full-text writing sample."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["writing_style_profile_v1"] = WRITING_STYLE_PROFILE_VERSION
    usage: WritingStyleUsage = "style_only"
    readiness: WritingStyleReadiness
    selected_segments: list[WritingStyleSegmentReference] = Field(
        default_factory=list,
        max_length=MAX_STYLE_SEGMENTS,
    )
    stats: WritingStyleObservableStats
    provenance: WritingStyleProvenance
    safety: WritingStyleSafetyPolicy = Field(default_factory=WritingStyleSafetyPolicy)

    @model_validator(mode="after")
    def aggregate_counts_must_match_selected_references(
        self,
    ) -> WritingStyleProfile:
        source_count = len({segment.source_id for segment in self.selected_segments})
        expected_counts = {
            "segment_count": len(self.selected_segments),
            "source_count": source_count,
            "non_whitespace_char_count": sum(
                segment.non_whitespace_char_count for segment in self.selected_segments
            ),
            "sentence_count": sum(segment.sentence_count for segment in self.selected_segments),
            "paragraph_count": sum(segment.paragraph_count for segment in self.selected_segments),
            "written_prose_segment_count": sum(
                segment.sample_kind == "written_prose" for segment in self.selected_segments
            ),
            "spoken_transcript_segment_count": sum(
                segment.sample_kind == "spoken_transcript" for segment in self.selected_segments
            ),
        }
        if any(
            getattr(self.stats, field_name) != expected
            for field_name, expected in expected_counts.items()
        ):
            raise ValueError("writing style stats do not match selected segment references")
        if self.stats.non_whitespace_char_count > MAX_STYLE_NON_WHITESPACE_CHARS:
            raise ValueError("writing style profile exceeds the character selection limit")
        if (
            self.provenance.selected_segment_count != len(self.selected_segments)
            or self.provenance.selected_source_count != source_count
            or self.provenance.selected_segment_count + self.provenance.excluded_segment_count
            != self.provenance.candidate_segment_count
        ):
            raise ValueError("writing style provenance counts do not match selected segments")
        if (
            self.readiness.observed_non_whitespace_char_count
            != self.stats.non_whitespace_char_count
            or self.readiness.observed_sentence_count != self.stats.sentence_count
        ):
            raise ValueError("writing style readiness observations do not match stats")
        return self
