from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from epiphany.editor_schemas import GroundedDraftParagraph, PodcastDraftOutput
from epiphany.quality_contract_schemas import CreativeBrief
from epiphany.schemas import ArtifactView, SourceReference

PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW = "plan_draft_supplemental_interview"
SUPPLEMENTAL_INTERVIEW_PLAN_VERSION = "draft_supplemental_interview_plan_v1"
MAX_DRAFT_QUESTION_ANCHORS = 24
MAX_ANCHOR_EXCERPT_CHARS = 1_200

SupplementalDetailType = Literal[
    "scene",
    "action",
    "dialogue",
    "sensory",
    "emotion",
    "motivation",
    "reflection",
    "contrast",
]
SupplementalInterviewGenerationMode = Literal["model", "deterministic_fallback"]

_SECTION_PARAGRAPH_PATH = re.compile(
    r"^podcast_script\.sections\[(?P<section>\d+)\]\.paragraphs\[(?P<paragraph>\d+)\]$"
)
_INTERNAL_IDENTIFIER = re.compile(
    r"\b(?:src|seg|art|run|task)_[A-Za-z0-9][A-Za-z0-9_-]*\b",
    re.IGNORECASE,
)


class SupplementalInterviewOutputError(ValueError):
    code = "supplemental_interview_output_invalid"


class SupplementalInterviewSchemaError(SupplementalInterviewOutputError):
    code = "supplemental_interview_schema_invalid"


class InvalidSupplementalInterviewAnchor(SupplementalInterviewOutputError):
    code = "supplemental_interview_anchor_invalid"


class ReusedSupplementalInterviewQuestion(SupplementalInterviewOutputError):
    code = "supplemental_interview_question_reused"


class SupplementalInterviewInternalIdentifierLeak(SupplementalInterviewOutputError):
    code = "supplemental_interview_internal_identifier_leak"


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _normalize_unique_text_list(value: list[str]) -> list[str]:
    normalized = [_normalize_required_text(item) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError("items must be unique")
    return normalized


def _question_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _reference_keys(references: Iterable[SourceReference]) -> tuple[tuple[str, str], ...]:
    return tuple((reference.source_id, reference.source_segment_id) for reference in references)


class SupplementalInterviewDurationGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_duration_minutes: float = Field(ge=0, le=180)
    minimum_duration_minutes: float = Field(gt=0, le=180)
    target_duration_minutes: float = Field(gt=0, le=180)
    missing_duration_minutes: float = Field(gt=0, le=180)

    @model_validator(mode="after")
    def values_must_describe_a_real_shortfall(self) -> SupplementalInterviewDurationGap:
        if self.minimum_duration_minutes > self.target_duration_minutes:
            raise ValueError("minimum duration cannot exceed target duration")
        if self.actual_duration_minutes >= self.minimum_duration_minutes:
            raise ValueError("duration gap requires actual duration below the minimum")
        expected_missing = self.minimum_duration_minutes - self.actual_duration_minutes
        if not math.isclose(
            self.missing_duration_minutes,
            expected_missing,
            rel_tol=0.001,
            abs_tol=0.02,
        ):
            raise ValueError("missing duration must equal minimum duration minus actual duration")
        return self


class DraftQuestionAnchor(BaseModel):
    """Trusted pointer to one exact paragraph in the latest spoken Draft."""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1, max_length=300)
    path: str = Field(min_length=1, max_length=300)
    section_title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=4, max_length=MAX_ANCHOR_EXCERPT_CHARS)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _text_is_not_blank = field_validator(
        "anchor_id",
        "path",
        "section_title",
        "excerpt",
    )(_normalize_required_text)

    @model_validator(mode="after")
    def anchor_id_must_be_its_stable_path(self) -> DraftQuestionAnchor:
        if self.anchor_id != self.path:
            raise ValueError("anchor_id must exactly equal the stable Draft path")
        keys = _reference_keys(self.source_refs)
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return self


class SupplementalInterviewQualityFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=1_000)
    location: str = Field(min_length=1, max_length=500)

    _text_is_not_blank = field_validator(
        "code",
        "explanation",
        "location",
    )(_normalize_required_text)


class DraftSupplementalInterviewTaskInput(BaseModel):
    """Trusted latest-Draft context supplied to the follow-up question planner."""

    model_config = ConfigDict(extra="forbid")

    task_kind: Literal["plan_draft_supplemental_interview"]
    draft_artifact_id: str = Field(min_length=1, max_length=200)
    quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    creative_brief: CreativeBrief
    duration_gap: SupplementalInterviewDurationGap
    podcast_draft: PodcastDraftOutput
    draft_anchors: list[DraftQuestionAnchor] = Field(
        min_length=3,
        max_length=MAX_DRAFT_QUESTION_ANCHORS,
    )
    quality_focus: list[SupplementalInterviewQualityFocus] = Field(
        default_factory=list,
        max_length=12,
    )
    previous_questions: list[str] = Field(default_factory=list, max_length=12)
    round_number: Literal[1, 2] = 1
    max_rounds: Literal[2] = 2
    status: Literal["awaiting_user"] = "awaiting_user"

    _previous_questions_are_unique = field_validator("previous_questions")(
        _normalize_unique_text_list
    )

    @model_validator(mode="after")
    def trusted_metadata_must_match_the_latest_draft(
        self,
    ) -> DraftSupplementalInterviewTaskInput:
        if self.round_number > self.max_rounds:
            raise ValueError("round_number cannot exceed max_rounds")
        if not math.isclose(
            self.duration_gap.target_duration_minutes,
            float(self.creative_brief.target_duration_minutes),
            abs_tol=0.01,
        ):
            raise ValueError("duration gap target must match the Creative Brief")

        anchor_ids = [anchor.anchor_id for anchor in self.draft_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("draft anchor IDs must be unique")
        for anchor in self.draft_anchors:
            paragraph, expected_title = _resolve_spoken_paragraph(
                self.podcast_draft,
                anchor.path,
            )
            if anchor.section_title != expected_title:
                raise ValueError("draft anchor section title does not match its path")
            if anchor.excerpt not in paragraph.text:
                raise ValueError("draft anchor excerpt must be copied from the exact paragraph")
            if _INTERNAL_IDENTIFIER.search(anchor.excerpt):
                raise ValueError("draft anchor excerpt must not expose an internal identifier")
            if _reference_keys(anchor.source_refs) != _reference_keys(paragraph.source_refs):
                raise ValueError("draft anchor source_refs must match the exact paragraph")
        return self


class SupplementalInterviewModelQuestion(BaseModel):
    """Question fields authored by the model, without trusted identifiers."""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1, max_length=300)
    anchor_quote: str = Field(min_length=4, max_length=120)
    prompt: str = Field(min_length=1, max_length=1_000)
    purpose: str = Field(min_length=1, max_length=1_000)
    detail_type: SupplementalDetailType
    answer_cues: list[str] = Field(min_length=2, max_length=4)
    estimated_new_character_count: int = Field(ge=80, le=3_000)

    _text_is_not_blank = field_validator(
        "anchor_id",
        "anchor_quote",
        "prompt",
        "purpose",
    )(_normalize_required_text)
    _answer_cues_are_unique = field_validator("answer_cues")(_normalize_unique_text_list)


class SupplementalInterviewQuestion(SupplementalInterviewModelQuestion):
    """Persisted question with a code-generated plan-local stable ID."""

    question_id: str = Field(pattern=r"^q[1-6]$")


class SupplementalInterviewModelOutput(BaseModel):
    """The only fields the model is allowed to author."""

    model_config = ConfigDict(extra="forbid")

    questions: list[SupplementalInterviewModelQuestion] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def questions_must_be_unique(self) -> SupplementalInterviewModelOutput:
        keys = [_question_key(question.prompt) for question in self.questions]
        if len(keys) != len(set(keys)):
            raise ValueError("question prompts must be unique")
        return self


class SupplementalInterviewPlan(BaseModel):
    """Persisted plan combining model questions with trusted workflow metadata."""

    model_config = ConfigDict(extra="forbid")

    plan_contract_version: Literal["draft_supplemental_interview_plan_v1"] = (
        SUPPLEMENTAL_INTERVIEW_PLAN_VERSION
    )
    task_kind: Literal["plan_draft_supplemental_interview"] = PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW
    generation_mode: SupplementalInterviewGenerationMode
    draft_artifact_id: str = Field(min_length=1, max_length=200)
    quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    creative_brief: CreativeBrief
    duration_gap: SupplementalInterviewDurationGap
    draft_anchors: list[DraftQuestionAnchor] = Field(
        min_length=3,
        max_length=MAX_DRAFT_QUESTION_ANCHORS,
    )
    quality_focus: list[SupplementalInterviewQualityFocus] = Field(
        default_factory=list,
        max_length=12,
    )
    questions: list[SupplementalInterviewQuestion] = Field(min_length=3, max_length=6)
    round_number: Literal[1, 2]
    max_rounds: Literal[2] = 2
    status: Literal["awaiting_user"] = "awaiting_user"

    @model_validator(mode="after")
    def question_ids_must_be_stable_and_sequential(self) -> SupplementalInterviewPlan:
        expected = [f"q{index}" for index in range(1, len(self.questions) + 1)]
        if [question.question_id for question in self.questions] != expected:
            raise ValueError("question IDs must be stable sequential plan-local IDs")
        return self


class SupplementalInterviewPlanRecord(BaseModel):
    """API-ready persisted Plan paired with its durable Artifact."""

    model_config = ConfigDict(extra="forbid")

    plan: SupplementalInterviewPlan
    artifact: ArtifactView


def _resolve_spoken_paragraph(
    draft: PodcastDraftOutput,
    path: str,
) -> tuple[GroundedDraftParagraph, str]:
    if path == "podcast_script.opening":
        return draft.podcast_script.opening, "开场"
    if path == "podcast_script.closing":
        return draft.podcast_script.closing, "收束"
    match = _SECTION_PARAGRAPH_PATH.fullmatch(path)
    if match is None:
        raise ValueError("draft anchor path must point to spoken Draft prose")
    section_index = int(match.group("section"))
    paragraph_index = int(match.group("paragraph"))
    try:
        section = draft.podcast_script.sections[section_index]
        paragraph = section.paragraphs[paragraph_index]
    except IndexError as error:
        raise ValueError("draft anchor path is outside the latest Draft") from error
    return paragraph, section.title


def _anchor_for_paragraph(
    *,
    path: str,
    section_title: str,
    paragraph: GroundedDraftParagraph,
) -> DraftQuestionAnchor | None:
    excerpt = paragraph.text[:MAX_ANCHOR_EXCERPT_CHARS]
    if len(excerpt.strip()) < 4 or _INTERNAL_IDENTIFIER.search(excerpt):
        return None
    return DraftQuestionAnchor(
        anchor_id=path,
        path=path,
        section_title=section_title,
        excerpt=excerpt,
        source_refs=paragraph.source_refs,
    )


def build_draft_question_anchors(
    draft: PodcastDraftOutput | dict[str, Any],
) -> list[DraftQuestionAnchor]:
    """Select stable, diverse anchors from spoken prose only.

    Opening, the first paragraph of every section, and closing are considered
    first. Remaining slots are filled round-robin by paragraph position so a
    long section cannot crowd every other section out of the 24-anchor bound.
    The returned list is restored to document order for deterministic display.
    """

    parsed = PodcastDraftOutput.model_validate(draft)
    candidates: list[tuple[int, int, DraftQuestionAnchor]] = []
    order = 0
    opening_anchor = _anchor_for_paragraph(
        path="podcast_script.opening",
        section_title="开场",
        paragraph=parsed.podcast_script.opening,
    )
    if opening_anchor is not None:
        candidates.append((order, -1, opening_anchor))
    order += 1
    for section_index, section in enumerate(parsed.podcast_script.sections):
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            anchor = _anchor_for_paragraph(
                path=(f"podcast_script.sections[{section_index}].paragraphs[{paragraph_index}]"),
                section_title=section.title,
                paragraph=paragraph,
            )
            if anchor is not None:
                candidates.append((order, paragraph_index, anchor))
            order += 1
    closing_anchor = _anchor_for_paragraph(
        path="podcast_script.closing",
        section_title="收束",
        paragraph=parsed.podcast_script.closing,
    )
    if closing_anchor is not None:
        candidates.append((order, -1, closing_anchor))
    if len(candidates) < 3:
        raise ValueError("Draft must contain at least three anchorable spoken paragraphs")
    if len(candidates) <= MAX_DRAFT_QUESTION_ANCHORS:
        return [anchor for _, _, anchor in candidates]

    selected_orders = {
        position for position, paragraph_index, _ in candidates if paragraph_index in {-1, 0}
    }
    paragraph_index = 1
    while len(selected_orders) < MAX_DRAFT_QUESTION_ANCHORS:
        added = False
        for position, candidate_paragraph_index, _ in candidates:
            if candidate_paragraph_index != paragraph_index:
                continue
            selected_orders.add(position)
            added = True
            if len(selected_orders) == MAX_DRAFT_QUESTION_ANCHORS:
                break
        if not added:
            break
        paragraph_index += 1
    return [anchor for position, _, anchor in candidates if position in selected_orders]


def _natural_language_fields(
    question: SupplementalInterviewModelQuestion | SupplementalInterviewQuestion,
) -> list[str]:
    return [question.anchor_quote, question.prompt, question.purpose, *question.answer_cues]


def _assert_no_internal_identifier_leak(
    *,
    parsed_input: DraftSupplementalInterviewTaskInput,
    questions: list[SupplementalInterviewModelQuestion] | list[SupplementalInterviewQuestion],
) -> None:
    forbidden_literals = {
        parsed_input.draft_artifact_id,
        parsed_input.quality_report_artifact_id,
        *(
            identifier
            for anchor in parsed_input.draft_anchors
            for reference in anchor.source_refs
            for identifier in (reference.source_id, reference.source_segment_id)
        ),
    }
    for question in questions:
        for text in _natural_language_fields(question):
            if _INTERNAL_IDENTIFIER.search(text) or any(
                identifier and identifier in text for identifier in forbidden_literals
            ):
                raise SupplementalInterviewInternalIdentifierLeak(
                    "supplemental interview prose must not expose internal identifiers"
                )


def _persisted_plan(
    *,
    parsed_input: DraftSupplementalInterviewTaskInput,
    questions: list[SupplementalInterviewModelQuestion],
    generation_mode: SupplementalInterviewGenerationMode,
) -> SupplementalInterviewPlan:
    persisted_questions = [
        SupplementalInterviewQuestion(
            question_id=f"q{index}",
            **question.model_dump(mode="json"),
        )
        for index, question in enumerate(questions, start=1)
    ]
    return SupplementalInterviewPlan(
        generation_mode=generation_mode,
        draft_artifact_id=parsed_input.draft_artifact_id,
        quality_report_artifact_id=parsed_input.quality_report_artifact_id,
        creative_brief=parsed_input.creative_brief,
        duration_gap=parsed_input.duration_gap,
        draft_anchors=parsed_input.draft_anchors,
        quality_focus=parsed_input.quality_focus,
        questions=persisted_questions,
        round_number=parsed_input.round_number,
        max_rounds=parsed_input.max_rounds,
        status=parsed_input.status,
    )


def validate_supplemental_interview_output(
    *,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    try:
        parsed_input = DraftSupplementalInterviewTaskInput.model_validate(task_input)
        model_output = SupplementalInterviewModelOutput.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise SupplementalInterviewSchemaError(
            "supplemental interview output did not match the strict planner schema"
        ) from error

    allowed_anchor_ids = {anchor.anchor_id for anchor in parsed_input.draft_anchors}
    if any(question.anchor_id not in allowed_anchor_ids for question in model_output.questions):
        raise InvalidSupplementalInterviewAnchor(
            "supplemental interview question referenced an unknown Draft anchor"
        )
    anchors_by_id = {anchor.anchor_id: anchor for anchor in parsed_input.draft_anchors}
    if any(
        question.anchor_quote not in anchors_by_id[question.anchor_id].excerpt
        for question in model_output.questions
    ):
        raise InvalidSupplementalInterviewAnchor(
            "supplemental interview anchor_quote was not copied from its exact Draft anchor"
        )

    previous_keys = {_question_key(question) for question in parsed_input.previous_questions}
    if any(_question_key(question.prompt) in previous_keys for question in model_output.questions):
        raise ReusedSupplementalInterviewQuestion(
            "supplemental interview question reused a previous-round question"
        )
    _assert_no_internal_identifier_leak(
        parsed_input=parsed_input,
        questions=model_output.questions,
    )
    return _persisted_plan(
        parsed_input=parsed_input,
        questions=model_output.questions,
        generation_mode="model",
    ).model_dump(mode="json")


def _anchor_quote(excerpt: str, *, limit: int = 72) -> str:
    return excerpt[:limit].rstrip()


def build_fallback_supplemental_interview_plan(
    task_input: DraftSupplementalInterviewTaskInput | dict[str, Any],
) -> dict[str, Any]:
    """Build three conservative questions after final Provider failure."""

    parsed = (
        task_input
        if isinstance(task_input, DraftSupplementalInterviewTaskInput)
        else DraftSupplementalInterviewTaskInput.model_validate(task_input)
    )
    candidates: list[DraftQuestionAnchor] = []
    seen_sections: set[str] = set()
    for anchor in parsed.draft_anchors:
        if anchor.section_title in seen_sections:
            continue
        candidates.append(anchor)
        seen_sections.add(anchor.section_title)
    candidate_ids = {item.anchor_id for item in candidates}
    candidates.extend(
        anchor for anchor in parsed.draft_anchors if anchor.anchor_id not in candidate_ids
    )
    offset = 3 if parsed.round_number == 2 and len(candidates) > 3 else 0
    rotated = [*candidates[offset:], *candidates[:offset]]
    selected = rotated[:3]

    estimated_total = math.ceil(
        parsed.duration_gap.missing_duration_minutes
        * parsed.creative_brief.speaking_rate_chars_per_minute
    )
    estimated_per_question = max(180, min(1_600, math.ceil(estimated_total / 3)))
    variant_sets: dict[
        int,
        list[tuple[SupplementalDetailType, str, str, list[str]]],
    ] = {
        1: [
            (
                "scene",
                "你能回到这句话对应的时刻，补充当时在哪里、周围有什么吗？"
                "如果不记得或没有更多细节，也可以直接说。",
                "把稿件中的概括落到一个可核对的具体场景，同时允许用户明确没有细节。",
                ["时间和地点", "周围一个具体细节", "不记得或没有也可以"],
            ),
            (
                "action",
                "这句话前后，你当时具体做了什么，事情又是怎样继续的？"
                "如果没有可补充的动作或经过，也可以直接说。",
                "补足叙事动作和前后连接，避免只用结论重复扩写。",
                ["前一个动作", "接下来发生的事", "没有更多经过也可以"],
            ),
            (
                "reflection",
                "现在再看这句话，你会怎样描述当时还没有说清楚的想法或矛盾？"
                "如果现在仍没有答案，也可以保留这种不确定。",
                "补充真实认知变化，而不是要求用户事后制造一个完整结论。",
                ["当时相信什么", "现在怎么看", "仍没答案也可以"],
            ),
        ],
        2: [
            (
                "dialogue",
                "这句话对应的时刻，有没有一句你或别人真的说过的话值得留下？"
                "如果没有或不记得，不需要补造对白。",
                "用真实原话补足人物关系，同时明确禁止为了完整而编造对白。",
                ["谁说的", "大致原话", "没有或不记得也可以"],
            ),
            (
                "sensory",
                "除了稿子已经写到的内容，当时有没有一个身体感受或感官细节？"
                "如果没有明显感觉，也可以直接说没有。",
                "寻找尚未出现的感官证据，而不是换一种说法重复结论。",
                ["身体反应", "声音或光线", "没有明显感觉也可以"],
            ),
            (
                "contrast",
                "把这句话里的当时和现在放在一起，最具体的一处变化是什么？"
                "如果没有清晰变化，也可以保留这种模糊。",
                "用可描述的前后差异补足变化，不强迫用户总结出成长结论。",
                ["当时的做法", "现在的做法", "没有清晰变化也可以"],
            ),
        ],
    }
    variants = variant_sets[parsed.round_number]
    previous_keys = {_question_key(question) for question in parsed.previous_questions}
    model_questions: list[SupplementalInterviewModelQuestion] = []
    for anchor, (detail_type, prompt, purpose, cues) in zip(
        selected,
        variants,
        strict=True,
    ):
        rendered_prompt = (
            f"稿子在“{anchor.section_title}”写到“{_anchor_quote(anchor.excerpt)}”。{prompt}"
        )
        if _question_key(rendered_prompt) in previous_keys:
            rendered_prompt += " 这一轮请只补一个上次没有回答过的新细节；没有也可以直说。"
        model_questions.append(
            SupplementalInterviewModelQuestion(
                anchor_id=anchor.anchor_id,
                anchor_quote=_anchor_quote(anchor.excerpt),
                prompt=rendered_prompt,
                purpose=purpose,
                detail_type=detail_type,
                answer_cues=cues,
                estimated_new_character_count=estimated_per_question,
            )
        )
    _assert_no_internal_identifier_leak(parsed_input=parsed, questions=model_questions)
    return _persisted_plan(
        parsed_input=parsed,
        questions=model_questions,
        generation_mode="deterministic_fallback",
    ).model_dump(mode="json")
