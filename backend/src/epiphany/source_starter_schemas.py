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

SOURCE_STARTER_TEXT_MAX_LENGTH = 6_000
SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH = 120
SOURCE_STARTER_NEUTRALIZED_EXCERPT_MAX_LENGTH = 480

_SAFE_FALLBACK_SUBJECT_MAX_LENGTH = 160
_SAFE_FALLBACK_PROJECT_CONTEXT_MAX_LENGTH = 2_500
_SAFE_FALLBACK_DIRECTION_MAX_LENGTH = 1_000


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
    starter_text: str = Field(min_length=1, max_length=SOURCE_STARTER_TEXT_MAX_LENGTH)
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


_PLACEHOLDER_PATTERN = re.compile(
    rf"\[(?:待补充|待核实)[：:].*?\]|\[句式示例[：:][^\]]"
    rf"{{1,{SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH}}}\]",
    re.DOTALL,
)
_WRITING_EXAMPLE_PATTERN = re.compile(r"\[句式示例[：:]([^\]]*)\]")
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
_DIALOGUE_ATTRIBUTION_PATTERN = re.compile(
    r"(?:说|问|回答|告诉|喊|写道|回复|回应|听见|听到|想到|心想|嘀咕|"
    r"念叨|发来|引用)\s*[：:]?\s*$"
)
_QUOTED_LABEL_CONTEXT_PATTERN = re.compile(
    r"(?:AI\s*提供|AI\s*候选|候选角度|可选角度|标题|主题|概念|关键词|"
    r"命题|方向|表述|所谓|例如|比如|叫作|称为|理解为|围绕|聚焦)"
    r"[^。！？!?\n]{0,32}$"
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
_FIRST_PERSON_SELF_INQUIRY_PATTERNS = (
    re.compile(
        r"^我(?:真正|最|目前|现在)?(?:好奇|担心|害怕|在意|期待|疑惑|"
        r"想知道|想弄清|想确认)(?:的)?(?:是)?(?:什么|哪些|哪一|为什么|"
        r"从哪里|在哪里|如何|怎么|是否|会不会|能否|有没有|多少)"
    ),
    re.compile(
        r"^我对[^，,。！？!?;；：:\n\r]{1,40}的(?:好奇|担忧|疑问|顾虑)"
        r"(?:是|来自|从)?(?:什么|哪些|哪一|为什么|哪里|从哪里|如何|怎么|"
        r"是否|会不会|能否|有没有|多少)"
    ),
)


def _safe_fallback_excerpt(value: str, *, max_length: int) -> str:
    """Bound untrusted text rendered inside a fallback placeholder.

    Square brackets are reserved for the ``[待补充]`` / ``[待核实]`` contract.
    Full-width brackets preserve readability without letting an input ``]``
    close the placeholder early. Independent bounds keep the deterministic
    fallback within the public ``starter_text`` schema when both Project
    description and intent are at their request maxima.
    """

    safe = value.translate(str.maketrans({"[": "［", "]": "］"}))
    if len(safe) <= max_length:
        return safe
    return safe[: max_length - 1].rstrip() + "…"


def build_safe_source_starter_candidate(*, task_input: dict[str, object]) -> dict[str, object]:
    """Build a deterministic, visibly non-factual candidate from server input.

    This is both the zero-cost Fake Provider implementation and the final
    safety fallback when a hosted model twice returns prose that cannot pass
    the grounding contract.  It deliberately provides a useful writing shape
    without pretending that the user has already supplied a personal story.
    Confirmation remains a separate human checkpoint.
    """

    parsed = SourceStarterTaskInput.model_validate(task_input)
    subject = _safe_fallback_excerpt(
        parsed.source_title or parsed.project.title,
        max_length=_SAFE_FALLBACK_SUBJECT_MAX_LENGTH,
    )
    example_subject = subject[:60]
    project_context = (
        _safe_fallback_excerpt(
            parsed.project.description,
            max_length=_SAFE_FALLBACK_PROJECT_CONTEXT_MAX_LENGTH,
        )
        if parsed.project.description
        else "Project 的背景和目标尚未提供"
    )
    direction = (
        _safe_fallback_excerpt(
            parsed.intent,
            max_length=_SAFE_FALLBACK_DIRECTION_MAX_LENGTH,
        )
        if parsed.intent
        else "这次最想探索或记录的具体方向尚未提供"
    )

    if parsed.mode == "exploration_outline":
        starter_text = (
            "【探索提纲｜AI 候选问题地图，不是事实记录】\n\n"
            f"[待补充：素材标题暂定为：{subject}；确认后删除本提示]\n\n"
            "[待补充：请确认以下 Project 背景是否准确，只保留准备写进正文的真实"
            f"信息：{project_context}]\n\n"
            "[待补充：请确认本次探索方向并改成自己的话："
            f"{direction}]\n\n"
            "一、先找个人入口\n"
            "- [待补充：第一次产生这个念头的具体时间、地点和触发物]\n"
            "- [待补充：目前最吸引你的一个画面、矛盾或问题]\n"
            "- [待补充：这件事与你现在的生活有什么具体关系]\n\n"
            "二、把主题拆成可以回答的问题\n"
            "- 如果它来自一段真实经历，那段经历可以从哪个现场开始讲？\n"
            "- 如果还没有亲身经历，最想先观察、尝试或验证什么？\n"
            "- 最后希望留下一个答案，还是保留一个尚未想明白的问题？\n\n"
            "三、把事实边界标出来\n"
            "- [待补充：只写亲自经历、观察或真实感受]\n"
            "- [待核实：需要另外查证的术语、数据、规则或专业结论]"
        )
    else:
        starter_text = (
            "【示例草稿｜AI 候选半成品；方括号内容不是事实，确认后才可导入】\n\n"
            "# 写作起点\n\n"
            f"[待补充：素材标题暂定为：{subject}；确认后删除本提示]\n\n"
            "[待补充：请确认以下 Project 背景是否准确，只保留准备写进正文的真实"
            f"信息：{project_context}]\n\n"
            "[待补充：请确认本次写作方向并改成自己的话："
            f"{direction}]\n\n"
            "[待补充：用两三句写开场现场，包括具体时间、地点、一个能看见或"
            "听见的细节，以及当时正在做的动作。]\n\n"
            "事情开始以前，[待补充：交代真实背景，以及为什么偏偏在这个时候"
            "注意到它。]\n\n"
            "真正发生变化的是，[待补充：按顺序写原计划、意外、第一反应和最后"
            "真正做出的动作。]\n\n"
            f"[句式示例：可以先把主题 {example_subject} 放进一个具体现场，再写一个不太"
            "体面或有点好笑的失败细节和处理动作；请整段替换成真实经历，这句话"
            "本身不是事实。]\n\n"
            "回到现在，[待补充：写下它改变的一个具体习惯、判断或仍未解决的"
            "问题，不必强行升华。]\n\n"
            "[待核实：文中需要外部知识支持的术语、数据或结论；如果没有可删除。]"
        )

    return SourceStarterCandidate(
        mode=parsed.mode,
        source_title=parsed.source_title,
        source_type=parsed.source_type,
        starter_text=starter_text,
        questions=[
            "这次最想写的主题，能确认的具体时间、地点和触发物分别是什么？",
            "如果它来自一段真实经历，最值得展开的三个动作是什么？",
            "如果目前还没有亲身经历，最想先观察或验证的一个问题是什么？",
            "有哪些内容需要另外查证，不能只凭记忆或想象下结论？",
        ],
        uncertainties=[
            "用户尚未提供可直接写成第一人称事实的完整现场",
            "涉及外部知识的内容尚未核实",
        ],
    ).model_dump(mode="json")


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
    return (
        normalized_clause.startswith(_TITLE_QUESTION_PREFIXES)
        or any(pattern.search(normalized_clause) for pattern in _FIRST_PERSON_SELF_INQUIRY_PATTERNS)
        or (
            sentence.rstrip().endswith(("?", "？"))
            and any(token in normalized_clause for token in _QUESTION_TOKENS)
        )
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
    # Strip visibly provisional regions before sentence splitting.  Splitting
    # first would let punctuation inside a bracket end the fragment before its
    # closing ``]`` and accidentally reinterpret an explicit placeholder as a
    # factual assertion.
    for index, sentence in enumerate(_sentences(_strip_placeholders(value)), start=1):
        visible = sentence.strip()
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
    writing_examples = _WRITING_EXAMPLE_PATTERN.findall(parsed.starter_text)
    if len(writing_examples) > 2:
        raise SourceStarterOutputValidationError(
            code="source_starter_writing_example_invalid",
            message="source starter output contained too many writing examples",
        )
    for index, example in enumerate(writing_examples, start=1):
        if (
            len(example) > SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH
            or "替换" not in example
            or not ("真实" in example or "不是事实" in example)
        ):
            raise SourceStarterOutputValidationError(
                code="source_starter_writing_example_invalid",
                message=(
                    "source starter writing example was not bounded and visibly provisional "
                    f"(writing example {index})"
                ),
            )

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


def neutralize_source_starter_first_person_candidate(
    *,
    task_input: dict[str, object],
    content: dict[str, object],
) -> dict[str, object]:
    """Preserve a useful candidate while making unsupported ``我`` prose provisional.

    Hosted models sometimes follow every structural instruction yet phrase an
    otherwise useful brainstorming angle as ``我担心……``.  Retrying the same
    model cannot be the final safety boundary.  This bounded local repair keeps
    lines that already satisfy the grounding guard and wraps only failing
    first-person lines in an explicit user-fillable region.  It never turns an
    AI guess into a user fact, and the repaired object still has to pass the
    complete public validator before it can be persisted.

    This function intentionally handles only
    ``source_starter_unsupported_first_person``.  Quotes, external facts,
    history presuppositions, schema mismatches, and every other failure keep
    their existing strict behavior.
    """

    parsed_input = SourceStarterTaskInput.model_validate(task_input)
    parsed = SourceStarterCandidate.model_validate(content)

    def is_supported(value: str, *, field: str) -> bool:
        try:
            _reject_unsupported_first_person_assertions(
                value=value,
                parsed_input=parsed_input,
                field=field,
            )
        except SourceStarterOutputValidationError as error:
            if error.code != "source_starter_unsupported_first_person":
                raise
            return False
        return True

    def safe_excerpt(value: str) -> str:
        # Do not persist the rejected first-person wording verbatim. Keep its
        # topical predicate as a visibly provisional prompt instead.
        neutral = value.replace("我们", "相关的人").replace("我自己", "本人")
        neutral = neutral.replace("我的", "相关的").replace("我", "")
        return _safe_fallback_excerpt(
            neutral.strip(),
            max_length=SOURCE_STARTER_NEUTRALIZED_EXCERPT_MAX_LENGTH,
        )

    starter_lines: list[str] = []
    for line_index, line in enumerate(parsed.starter_text.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if not body.strip() or is_supported(body, field=f"starter_text line {line_index}"):
            starter_lines.append(line)
            continue
        indent = body[: len(body) - len(body.lstrip())]
        starter_lines.append(
            indent
            + "[待补充：以下内容由 AI 提出，不是用户事实；请按真实情况确认、改写或删除："
            + safe_excerpt(body)
            + "]"
            + ending
        )

    questions: list[str] = []
    for index, question in enumerate(parsed.questions, start=1):
        if is_supported(question, field=f"questions item {index}"):
            questions.append(question)
            continue
        questions.append(
            "[待补充：以下问题包含 AI 尚未确认的第一人称表达；请按真实情况改写："
            + safe_excerpt(question)
            + "]？"
        )

    uncertainties: list[str] = []
    for index, uncertainty in enumerate(parsed.uncertainties, start=1):
        if is_supported(uncertainty, field=f"uncertainties item {index}"):
            uncertainties.append(uncertainty)
            continue
        uncertainties.append(
            "AI 候选内容尚未由用户确认，需要补充或删除：[待补充：" + safe_excerpt(uncertainty) + "]"
        )

    repaired = parsed.model_copy(
        update={
            "starter_text": "".join(starter_lines),
            "questions": questions,
            "uncertainties": uncertainties,
        }
    )
    # Re-parse so length and collection bounds are enforced after wrapping.
    return SourceStarterCandidate.model_validate(repaired.model_dump(mode="json")).model_dump(
        mode="json"
    )


def neutralize_source_starter_direct_quote_candidate(
    *,
    task_input: dict[str, object],
    content: dict[str, object],
) -> dict[str, object]:
    """Keep AI concept labels while making invented dialogue provisional.

    Chinese prose often quotes a tentative theme or label; that typography is
    different from claiming the user literally heard a line of dialogue.
    Unsupported quoted spans are rewritten as explicit AI candidate wording.
    If the surrounding text attributes the words to a speaker, or the span
    otherwise looks spoken, the whole line becomes user-fillable. Supported
    verbatim quotes remain untouched. The normal full validator still decides
    whether the transformed candidate is safe enough to persist.
    """

    parsed_input = SourceStarterTaskInput.model_validate(task_input)
    parsed = SourceStarterCandidate.model_validate(content)
    inputs = _server_snapshotted_text(parsed_input)

    def normalize(value: str) -> tuple[str, bool]:
        pieces: list[str] = []
        cursor = 0
        dialogue_like = False
        changed = False
        for match in _DIRECT_QUOTE_PATTERN.finditer(value):
            quote = next(group for group in match.groups() if group is not None)
            if any(quote in input_text for input_text in inputs):
                continue
            changed = True
            prefix = value[max(0, match.start() - 120) : match.start()]
            current_line_prefix = prefix.rsplit("\n", 1)[-1]
            safe_label_context = bool(_QUOTED_LABEL_CONTEXT_PATTERN.search(prefix))
            attributed = bool(_DIALOGUE_ATTRIBUTION_PATTERN.search(current_line_prefix))
            looks_spoken = bool(re.search(r"[。！？!?]", quote)) or any(
                pronoun in quote for pronoun in ("我", "你", "咱")
            )
            dialogue_like = dialogue_like or attributed or (looks_spoken and not safe_label_context)
            pieces.append(value[cursor : match.start()])
            safe_quote = _safe_fallback_excerpt(
                quote,
                max_length=SOURCE_STARTER_NEUTRALIZED_EXCERPT_MAX_LENGTH,
            )
            pieces.append(f"〔AI 候选表述，并非用户原话：{safe_quote}〕")
            cursor = match.end()
        if not changed:
            return value, False
        pieces.append(value[cursor:])
        return "".join(pieces), dialogue_like

    def provisional(value: str, *, label: str, question: bool = False) -> str:
        safe = _safe_fallback_excerpt(
            value,
            max_length=SOURCE_STARTER_NEUTRALIZED_EXCERPT_MAX_LENGTH,
        )
        return f"[待补充：{label}；请按真实情况确认、改写或删除：{safe}]" + (
            "？" if question else ""
        )

    starter_lines: list[str] = []
    for line in parsed.starter_text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        normalized, dialogue_like = normalize(body)
        if dialogue_like:
            indent = body[: len(body) - len(body.lstrip())]
            normalized = indent + provisional(
                normalized.strip(),
                label="AI 候选中包含未经确认的对话或引语，不是用户事实",
            )
        starter_lines.append(normalized + ending)

    questions: list[str] = []
    for question in parsed.questions:
        normalized, dialogue_like = normalize(question)
        if dialogue_like:
            normalized = provisional(
                normalized,
                label="AI 候选问题包含未经确认的对话或引语，不是用户事实",
                question=True,
            )
        questions.append(normalized)

    uncertainties: list[str] = []
    for uncertainty in parsed.uncertainties:
        normalized, dialogue_like = normalize(uncertainty)
        if dialogue_like:
            normalized = "AI 候选内容尚未由用户确认，需要补充或删除：" + provisional(
                normalized,
                label="其中包含未经确认的对话或引语",
            )
        uncertainties.append(normalized)

    repaired = parsed.model_copy(
        update={
            "starter_text": "".join(starter_lines),
            "questions": questions,
            "uncertainties": uncertainties,
        }
    )
    return SourceStarterCandidate.model_validate(repaired.model_dump(mode="json")).model_dump(
        mode="json"
    )


def ground_source_starter_candidate(
    *,
    task_input: dict[str, object],
    content: dict[str, object],
) -> dict[str, object]:
    """Ground a schema-valid hosted candidate line by line.

    Model prose can contain more than one safety problem. Handling only the
    first reported code causes a whack-a-mole retry: after an unsupported
    first-person line is repaired, an unsupported quote or presupposed history
    can become the next failure and discard the whole useful candidate.

    This deterministic pass keeps each line/item that independently satisfies
    the existing strict guards. Only failing material becomes an explicitly
    provisional ``[待补充]`` region. The topical wording remains available to
    spark the user's thinking, but rejected first-person and quotation
    typography are neutralized rather than persisted as user facts. The caller
    must still run ``validate_source_starter_output`` on the whole result.
    """

    parsed_input = SourceStarterTaskInput.model_validate(task_input)
    parsed = SourceStarterCandidate.model_validate(content)

    def safe_excerpt(value: str) -> str:
        neutral = value.replace("我们", "相关的人").replace("我自己", "本人")
        neutral = neutral.replace("我的", "相关的").replace("我", "")
        neutral = neutral.translate(
            str.maketrans(
                {
                    "“": "‹",
                    "”": "›",
                    "「": "‹",
                    "」": "›",
                    "『": "‹",
                    "』": "›",
                }
            )
        )
        return _safe_fallback_excerpt(
            neutral.strip(),
            max_length=SOURCE_STARTER_NEUTRALIZED_EXCERPT_MAX_LENGTH,
        )

    def passes_safety(value: str, *, field: str, exploration_outline: bool) -> bool:
        try:
            _reject_unsupported_first_person_assertions(
                value=value,
                parsed_input=parsed_input,
                field=field,
            )
            _reject_bounded_inventions(
                value=value,
                parsed_input=parsed_input,
                field=field,
                exploration_outline=exploration_outline,
            )
        except SourceStarterOutputValidationError:
            return False
        return True

    starter_lines: list[str] = []
    kept_writing_examples = 0
    for index, line in enumerate(parsed.starter_text.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        examples = _WRITING_EXAMPLE_PATTERN.findall(body)
        examples_valid = all(
            len(example) <= SOURCE_STARTER_WRITING_EXAMPLE_MAX_LENGTH
            and "替换" in example
            and ("真实" in example or "不是事实" in example)
            for example in examples
        )
        examples_fit = kept_writing_examples + len(examples) <= 2
        safe = not body.strip() or (
            examples_valid
            and examples_fit
            and passes_safety(
                body,
                field=f"starter_text line {index}",
                exploration_outline=parsed.mode == "exploration_outline",
            )
        )
        if safe:
            kept_writing_examples += len(examples)
            starter_lines.append(line)
            continue
        indent = body[: len(body) - len(body.lstrip())]
        starter_lines.append(
            indent + "[待补充：以下是 AI 提供的主题相关候选，不是用户事实；"
            "请按真实情况确认、改写或删除：" + safe_excerpt(body) + "]" + ending
        )

    questions: list[str] = []
    for index, question in enumerate(parsed.questions, start=1):
        if question.rstrip().endswith(("?", "？")) and passes_safety(
            question,
            field=f"questions item {index}",
            exploration_outline=False,
        ):
            questions.append(question)
            continue
        questions.append(
            "[待补充：以下是 AI 提供的候选问题，不是用户事实；请确认并改写："
            + safe_excerpt(question)
            + "]？"
        )

    uncertainties: list[str] = []
    for index, uncertainty in enumerate(parsed.uncertainties, start=1):
        if any(marker in uncertainty for marker in _UNCERTAINTY_MARKERS) and passes_safety(
            uncertainty,
            field=f"uncertainties item {index}",
            exploration_outline=False,
        ):
            uncertainties.append(uncertainty)
            continue
        uncertainties.append(
            "AI 候选内容尚未由用户确认，需要补充或删除：[待补充：" + safe_excerpt(uncertainty) + "]"
        )

    repaired = parsed.model_copy(
        update={
            "starter_text": "".join(starter_lines),
            "questions": questions,
            "uncertainties": uncertainties,
        }
    )
    return SourceStarterCandidate.model_validate(repaired.model_dump(mode="json")).model_dump(
        mode="json"
    )
