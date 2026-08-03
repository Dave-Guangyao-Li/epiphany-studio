from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from epiphany.schemas import SourceView

BUILD_SOURCE_STARTER = "build_source_starter"
SOURCE_STARTER_WORKFLOW_TYPE = "source-starter"
SOURCE_STARTER_WORKFLOW_VERSION = "v1"

SourceStarterMode = Literal["exploration_outline", "starter_draft"]
SourceStarterSourceType = Literal["journal", "podcast_draft", "other"]


class SourceStarterOutputValidationError(ValueError):
    """A safe validation failure category that never embeds generated text."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CreateSourceStarterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1, max_length=160)
    source_title: str | None = Field(default=None, max_length=200)
    source_type: SourceStarterSourceType = "journal"
    mode: SourceStarterMode = "starter_draft"
    intent: str | None = Field(default=None, max_length=2_000)

    @field_validator("submission_id")
    @classmethod
    def submission_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("submission_id must contain non-whitespace characters")
        return normalized

    @field_validator("source_title", "intent")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SourceStarterProjectSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    title: str
    description: str | None


class SourceStarterTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_kind: Literal["build_source_starter"] = BUILD_SOURCE_STARTER
    project: SourceStarterProjectSnapshot
    source_title: str | None
    source_type: SourceStarterSourceType
    mode: SourceStarterMode
    intent: str | None


class SourceStarterSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_user_confirmation: Literal[True] = True
    factual_claims_require_verification: Literal[True] = True


class SourceStarterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["source-starter-candidate.v1"] = "source-starter-candidate.v1"
    mode: SourceStarterMode
    source_title: str | None
    source_type: SourceStarterSourceType
    starter_text: str = Field(min_length=1, max_length=6_000)
    questions: list[str] = Field(min_length=2, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    safety: SourceStarterSafety = Field(default_factory=SourceStarterSafety)

    @field_validator("starter_text")
    @classmethod
    def starter_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("starter_text must contain non-whitespace characters")
        return value.strip()

    @field_validator("questions", "uncertainties")
    @classmethod
    def list_items_must_not_be_blank(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list items must contain non-whitespace characters")
        return normalized


class ConfirmSourceStarterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    source_type: SourceStarterSourceType
    text: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("submission_id", "title")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator("text")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace characters")
        return value


class SourceStarterConfirmationResponse(BaseModel):
    created: bool
    linked: bool
    idempotent_replay: bool
    source: SourceView
    source_starter_run_id: str
    candidate_artifact_id: str
    confirmation_artifact_id: str


_PLACEHOLDER_PATTERN = re.compile(r"\[(?:待补充|待核实)[：:].*?\]", re.DOTALL)
_FIRST_PERSON_CLAUSE_PATTERN = re.compile(r"我[^，,。！？!?;；：:\n\r]*")
_SECOND_PERSON_CONCRETE_FIRST_PATTERN = re.compile(
    r"你在(?!哪里|何时|什么时候|什么时间|什么场景|哪(?:里|个|次|天|年))"
    r"[^，,。！？!?;；\n\r]{1,32}第一次"
)
_USER_HISTORY_PATTERN = re.compile(r"用户(?:曾经?|已经|过去|当时|后来|第一次)")
_OMITTED_HISTORY_PATTERN = re.compile(
    r"^\s*(?:第一次|后来|那天|当时|小时候|大学时|毕业后|搬家后|工作后|回国后|"
    r"去年|前年|有一次)[^。！？!?\n]{0,80}"
    r"(?:来到|去了|参加|看见|看到|听见|听到|遇到|搬到|开始|决定|感到|担心|"
    r"发现|尝试|经历|录下|打开|按下|住在)"
)
_EXTERNAL_FACT_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:%|％|米|公尺|公斤|千克|摄氏度|度|元|美元|人民币))|"
    r"(?:(?:研究|数据|统计|法规|法律|行业标准|官方|专家|机构|认证)"
    r"[^。！？!?\n]{0,36}(?:表明|显示|规定|要求|建议|证明|必须|通常|一般|会导致))|"
    r"(?:一定会|必然会|绝对安全|最安全|成功率|风险率)"
)
_DIRECT_QUOTE_PATTERN = re.compile(
    r"(?:“([^”\n]{1,120})”|「([^」\n]{1,120})」|『([^』\n]{1,120})』)"
)
_QUESTION_TOKENS = (
    "为什么",
    "是否",
    "什么",
    "哪里",
    "何时",
    "怎么",
    "如何",
    "哪个",
    "哪次",
    "几",
    "会不会",
    "能否",
    "要不要",
    "是不是",
    "有没有",
    "吗",
)
_UNCERTAINTY_MARKERS = (
    "未知",
    "尚未",
    "未提供",
    "未核实",
    "不确定",
    "需要核实",
    "需要确认",
    "需要补充",
    "需核实",
    "需确认",
    "需补充",
    "待核实",
    "待补充",
    "无法判断",
    "不清楚",
    "缺少",
)
_TITLE_QUESTION_PREFIXES = (
    "我为什么",
    "我是否",
    "我想要什么",
    "我是谁",
    "我要什么",
    "我在想什么",
    "我该不该",
    "我能否",
    "我能不能",
    "我要不要",
    "我怎么",
    "我如何",
    "我是什么",
    "我会不会",
    "我可不可以",
)


def _server_snapshotted_text(parsed_input: SourceStarterTaskInput) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            parsed_input.project.title,
            parsed_input.project.description,
            parsed_input.source_title,
            parsed_input.intent,
        )
        if value
    )


def _strip_placeholders(value: str) -> str:
    """Remove user-fillable regions before checking first-person assertions."""

    return _PLACEHOLDER_PATTERN.sub("", value)


def _is_question_clause(*, clause: str, sentence: str) -> bool:
    normalized_clause = clause.strip("《》〈〉「」『』“”'\"() ")
    return normalized_clause.startswith(_TITLE_QUESTION_PREFIXES) or (
        sentence.rstrip().endswith(("?", "？"))
        and any(token in normalized_clause for token in _QUESTION_TOKENS)
    )


def _is_verbatim_supported(*, clause: str, inputs: tuple[str, ...]) -> bool:
    normalized = clause.strip("《》〈〉「」『』“”'\"() ")
    return len(normalized) > 1 and any(normalized in input_text for input_text in inputs)


def _is_subject_projection_supported(*, clause: str, inputs: tuple[str, ...]) -> bool:
    """Allow an explicitly supplied fact to be written in the user's voice.

    Project descriptions and intents often omit the subject (for example,
    ``2025年9月从成都搬到上海``) or describe a synthetic/user persona in
    third person. A starter draft should be able to turn that exact predicate
    into ``我从成都搬到上海`` without treating the pronoun change as a new
    autobiographical fact. The predicate itself must still occur verbatim in a
    server-snapshotted input, so this does not permit invented details or
    paraphrased claims.
    """

    normalized = clause.strip("《》〈〉「」『』“”'\"() ")
    if not normalized.startswith("我"):
        return False
    predicate = normalized.removeprefix("我").strip()
    return len(predicate) > 1 and any(predicate in input_text for input_text in inputs)


def _subject_projection_inputs(parsed_input: SourceStarterTaskInput) -> tuple[str, ...]:
    """Facts may be projected from prose context, never from a title alone."""

    return tuple(
        value
        for value in (parsed_input.project.description, parsed_input.intent)
        if value and "?" not in value and "？" not in value
    )


def _sentences(value: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？!?;；])|\n+", value)
        if sentence.strip()
    ]


def _reject_unsupported_first_person_assertions(
    *, value: str, parsed_input: SourceStarterTaskInput, field: str
) -> None:
    inputs = _server_snapshotted_text(parsed_input)
    projection_inputs = _subject_projection_inputs(parsed_input)
    sentences = _sentences(_strip_placeholders(value))
    checked_fragment_index = 0
    for sentence in sentences:
        for match in _FIRST_PERSON_CLAUSE_PATTERN.finditer(sentence):
            checked_fragment_index += 1
            clause = match.group(0)
            if _is_question_clause(clause=clause, sentence=sentence):
                continue
            if _is_verbatim_supported(clause=clause, inputs=inputs):
                continue
            if _is_subject_projection_supported(
                clause=clause,
                inputs=projection_inputs,
            ):
                continue
            raise SourceStarterOutputValidationError(
                code="source_starter_unsupported_first_person",
                message=(
                    "source starter output contained an unsupported first-person assertion "
                    f"({field} fragment {checked_fragment_index})"
                ),
            )


def _reject_bounded_inventions(
    *,
    value: str,
    parsed_input: SourceStarterTaskInput,
    field: str,
    exploration_outline: bool,
) -> None:
    inputs = _server_snapshotted_text(parsed_input)
    for quote_index, quote_match in enumerate(_DIRECT_QUOTE_PATTERN.finditer(value), start=1):
        quote = next(group for group in quote_match.groups() if group is not None)
        if not any(quote in input_text for input_text in inputs):
            raise SourceStarterOutputValidationError(
                code="source_starter_unsupported_direct_quote",
                message=(
                    "source starter output contained an unsupported direct quotation "
                    f"({field} quotation {quote_index})"
                ),
            )
    for index, sentence in enumerate(_sentences(value), start=1):
        visible = _strip_placeholders(sentence).strip()
        if not visible:
            continue
        if _USER_HISTORY_PATTERN.search(visible):
            raise SourceStarterOutputValidationError(
                code="source_starter_unsupported_user_history",
                message=(
                    "source starter output contained an unsupported user-history assertion "
                    f"({field} fragment {index})"
                ),
            )
        concrete_first = _SECOND_PERSON_CONCRETE_FIRST_PATTERN.search(visible)
        if concrete_first and not _is_verbatim_supported(
            clause=concrete_first.group(0), inputs=inputs
        ):
            raise SourceStarterOutputValidationError(
                code="source_starter_history_presupposition",
                message=(
                    "source starter output contained an unsupported personal-history "
                    f"presupposition ({field} fragment {index})"
                ),
            )
        if (
            exploration_outline
            and _OMITTED_HISTORY_PATTERN.search(visible)
            and not _is_verbatim_supported(clause=visible, inputs=inputs)
        ):
            raise SourceStarterOutputValidationError(
                code="source_starter_omitted_history",
                message=(
                    "source starter exploration outline contained an unsupported "
                    f"personal-history assertion ({field} fragment {index})"
                ),
            )
        if (
            _EXTERNAL_FACT_PATTERN.search(visible)
            and "[待核实：" not in sentence
            and "[待核实:" not in sentence
        ):
            raise SourceStarterOutputValidationError(
                code="source_starter_unverified_external_fact",
                message=(
                    "source starter output contained an unverified external factual "
                    f"assertion ({field} fragment {index})"
                ),
            )


def _validate_user_visible_safety(
    *, parsed: SourceStarterCandidate, parsed_input: SourceStarterTaskInput
) -> None:
    fields: list[tuple[str, str]] = [("starter_text", parsed.starter_text)]
    fields.extend(
        (f"questions item {index}", value) for index, value in enumerate(parsed.questions, 1)
    )
    fields.extend(
        (f"uncertainties item {index}", value)
        for index, value in enumerate(parsed.uncertainties, 1)
    )
    for field, value in fields:
        _reject_unsupported_first_person_assertions(
            value=value,
            parsed_input=parsed_input,
            field=field,
        )
        _reject_bounded_inventions(
            value=value,
            parsed_input=parsed_input,
            field=field,
            exploration_outline=(parsed.mode == "exploration_outline" and field == "starter_text"),
        )

    for index, question in enumerate(parsed.questions, start=1):
        if not question.rstrip().endswith(("?", "？")):
            raise SourceStarterOutputValidationError(
                code="source_starter_question_invalid",
                message=(
                    "source starter question was not phrased as a question "
                    f"(questions item {index})"
                ),
            )
    for index, uncertainty in enumerate(parsed.uncertainties, start=1):
        if not any(marker in uncertainty for marker in _UNCERTAINTY_MARKERS):
            raise SourceStarterOutputValidationError(
                code="source_starter_uncertainty_invalid",
                message=(
                    "source starter uncertainty did not identify an unknown "
                    f"(uncertainties item {index})"
                ),
            )


def validate_source_starter_output(
    *,
    task_input: dict[str, object],
    content: dict[str, object],
) -> dict[str, object]:
    parsed_input = SourceStarterTaskInput.model_validate(task_input)
    parsed = SourceStarterCandidate.model_validate(content)
    if parsed.mode != parsed_input.mode:
        raise SourceStarterOutputValidationError(
            code="source_starter_mode_mismatch",
            message="source starter output mode did not match the task",
        )
    if parsed.source_type != parsed_input.source_type:
        raise SourceStarterOutputValidationError(
            code="source_starter_type_mismatch",
            message="source starter output source_type did not match the task",
        )
    if parsed.source_title != parsed_input.source_title:
        raise SourceStarterOutputValidationError(
            code="source_starter_title_mismatch",
            message="source starter output source_title did not match the task",
        )
    _validate_user_visible_safety(parsed=parsed, parsed_input=parsed_input)
    return parsed.model_dump(mode="json")
