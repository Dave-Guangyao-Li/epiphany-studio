from __future__ import annotations

import html
import re
from typing import Any

from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.episode_markdown import contains_internal_source_identifier
from epiphany.interview_markdown import RawSourceIdentifierInMarkdown

_MARKDOWN_CONTROL = re.compile(r"([\\`*_[\]{}()#+\-.!|>])")

_DECISION_LABELS = {
    "blocked": "存在硬性问题，建议先修订",
    "automated_review_incomplete": "自动审阅不完整，仍需人工检查",
    "revision_recommended": "建议修订后再录制",
    "candidate_ready_for_human_review": "可以进入人工审稿",
}

_DIMENSION_LABELS = {
    "brief_adherence": "创作要求匹配",
    "source_faithfulness": "来源忠实度",
    "coverage_and_specificity": "覆盖度与具体性",
    "structure_and_coherence": "结构与连贯性",
    "oral_naturalness_and_voice_fit": "口播自然度与声音匹配",
    "conciseness_and_non_redundancy": "精炼度与非重复性",
    "personal_style_match": "个人写作风格匹配",
}


def _safe(value: object) -> str:
    escaped = html.escape(str(value), quote=False)
    return _MARKDOWN_CONTROL.sub(r"\\\1", escaped)


def render_draft_quality_markdown(content: dict[str, Any]) -> str:
    report = DraftQualityReport.model_validate(content)
    metrics = report.deterministic.metrics
    lines = [
        "# 口播稿质量报告",
        "",
        f"结论：**{_safe(_DECISION_LABELS[report.decision])}**",
        "",
        "> 这是一份辅助审稿报告，不是事实证明、真实听众评价或 AI 生成概率。"
        "模型部分属于建议性自评，最终判断始终需要人完成。",
        "",
        "## 可复现指标",
        "",
        f"- 确定性规则版本：`{_safe(metrics.rules_version)}`",
        "- 中文表达启发式版本："
        + (
            f"`{_safe(metrics.chinese_style_heuristic_version)}`"
            if metrics.chinese_style_heuristic_version is not None
            else "未启用（legacy）"
        ),
        f"- 目标时长：{metrics.target_duration_minutes} 分钟",
        f"- 估算时长：{metrics.estimated_duration_minutes:.2f} 分钟",
        f"- 估算口径：每分钟 {metrics.speaking_rate_chars_per_minute} 个非空白字符",
        f"- 正文字符数：{metrics.script_character_count}",
        f"- 段落引用覆盖率：{metrics.paragraph_citation_coverage:.1%}",
        f"- 引用来源：{metrics.unique_source_count} 个 Source / "
        f"{metrics.unique_segment_count} 个片段",
        f"- 完全重复段落：{metrics.exact_duplicate_paragraph_count}",
        f"- 固定口语填充短语命中：{metrics.filler_phrase_count}",
        "- 固定口语填充短语密度："
        f"{metrics.filler_phrase_density_per_1000_chars:.2f} 次 / 1000 字符",
        f"- 模板化短语命中：{metrics.template_phrase_count}",
        f"- “不是……而是……”句式命中：{metrics.not_but_pattern_count}",
        f"- 疑似编辑说明泄漏：{metrics.editorial_instruction_phrase_count}",
        "- `must_include` 逐字未命中："
        f"{metrics.must_include_missing_count}（仅表示字符串未出现，不代表语义缺失）",
        "",
        f"确定性分数（实验性）：{report.deterministic.deterministic_score}/100",
        "",
        "## 规则发现",
        "",
    ]
    actionable = [
        finding
        for finding in report.deterministic.findings
        if finding.status in {"warning", "blocker"}
    ]
    if not actionable:
        lines.append("- 未发现确定性 warning 或 blocker。")
    for finding in actionable:
        label = "阻断" if finding.status == "blocker" else "提醒"
        lines.append(
            f"- [{label}] {_safe(finding.code)}：观测值 {_safe(finding.observed)}；"
            f"阈值 {_safe(finding.threshold)}"
        )
        lines.append(f"  - 位置：`{_safe(finding.location)}`")
        if finding.exact_quote:
            lines.append(f"  - 证据摘录：“{_safe(finding.exact_quote)}”")
    informational = [
        finding for finding in report.deterministic.findings if finding.status == "info"
    ]
    if informational:
        lines.extend(["", "### 信息性观察（不扣分、不触发评分上限）", ""])
        for finding in informational:
            lines.append(
                f"- {_safe(finding.code)}：观测值 {_safe(finding.observed)}；"
                f"说明 {_safe(finding.threshold)}"
            )

    lines.extend(["", "## 模型建议性自评", ""])
    if report.scoring_formula_version == "draft_quality_v3_personal_style_non_compensatory_caps":
        style_status = {
            "ready": "样本已达到评估门槛；仅用于比较表达风格，不作为本期事实来源。",
            "limited": "样本量有限；本次不评价、也不声称稿子是否像本人。",
            "not_provided": "未提供个人写作样本；本次不评价、也不声称稿子是否像本人。",
        }[report.writing_style_context_status]
        lines.extend(
            [
                f"- 个人写作样本：{_safe(style_status)}",
                "",
            ]
        )
    if report.model_self_review is None:
        lines.append(
            "- 本次模型自评不可用："
            f"{_safe(report.model_review_unavailable_reason or '原因未记录')}。"
        )
    else:
        relation = {
            "same_model": "与写稿模型相同",
            "cross_tier_same_family": "与写稿模型同属 DeepSeek V4、但使用不同能力档位",
            "different_model": "与写稿模型不同",
            "unknown": "模型关系未知",
        }.get(report.reviewer_relation, "模型关系未知")
        lines.extend(
            [
                f"- Reviewer 关系：{relation}",
                "- 性质：advisory（仅供人工审稿参考）",
                "",
            ]
        )
        for dimension in report.model_self_review.dimensions:
            label = _DIMENSION_LABELS[dimension.dimension]
            if not dimension.assessable:
                lines.extend(
                    [
                        f"### {label}：无法可靠评价",
                        "",
                        _safe(dimension.limitation or dimension.assessment),
                        "",
                    ]
                )
                continue
            lines.extend(
                [
                    f"### {label}：{dimension.score}/5",
                    "",
                    _safe(dimension.assessment),
                    "",
                ]
            )
            for evidence in dimension.evidence:
                lines.append(
                    f"- 证据位置：`{_safe(evidence.location)}`；"
                    f"摘录：“{_safe(evidence.exact_quote)}”"
                )
            for evidence in dimension.style_sample_evidence:
                lines.append(
                    f"- 写作样本证据：`{_safe(evidence.location)}`；"
                    f"摘录：“{_safe(evidence.exact_quote)}”"
                )
            lines.append("")

    if report.scoring_formula_version in {
        "draft_quality_v2_non_compensatory_caps",
        "draft_quality_v3_personal_style_non_compensatory_caps",
    }:
        model_score_description = (
            "模型六个基础维度与个人风格维度的加权结果"
            "（个人风格占模型分的 30%，未应用硬性上限，仅用于偏差研究）："
            if report.writing_style_context_status == "ready"
            else "模型六个局部维度的简单平均（个人风格不可评估，未应用硬性上限，仅用于偏差研究）："
            if report.scoring_formula_version
            == "draft_quality_v3_personal_style_non_compensatory_caps"
            else "模型六个局部维度的简单平均（未应用硬性上限，仅用于偏差研究）："
        )
        lines.extend(
            [
                "## 实验性评分校准",
                "",
                f"- 评分公式：`{_safe(report.scoring_formula_version)}`",
                f"- {model_score_description}"
                + (
                    f"{report.experimental_model_score:.2f}/100"
                    if report.experimental_model_score is not None
                    else "不可用"
                ),
                "- 未校准加权综合分："
                + (
                    f"{report.experimental_uncapped_overall_score:.2f}/100"
                    if report.experimental_uncapped_overall_score is not None
                    else "不可用"
                ),
                "- 代码拥有的非补偿式上限："
                + (
                    f"{report.code_owned_score_cap}/100"
                    if report.code_owned_score_cap is not None
                    else "未记录"
                ),
                "- 校准后实验性综合分："
                + (
                    f"{report.experimental_overall_score:.2f}/100"
                    if report.experimental_overall_score is not None
                    else "不可用"
                ),
                "",
                "该分数只用于同一版本规则下的回归比较，不代表客观质量，"
                "也不能替代真实录制与听众反馈。",
                "",
            ]
        )
        if report.score_cap_reasons:
            lines.extend(["### 上限原因", ""])
            for reason in report.score_cap_reasons:
                lines.append(
                    f"- `{_safe(reason.code)}`（上限 {reason.cap}）：{_safe(reason.explanation)}"
                )
            lines.append("")
        if report.model_review_conflicts:
            lines.extend(
                [
                    "### 模型意见与代码事实的冲突",
                    "",
                    "> 下列冲突不会改写原始模型评分卡，只会由代码校准最终综合分。",
                    "",
                ]
            )
            for conflict in report.model_review_conflicts:
                lines.append(f"- `{_safe(conflict.code)}`：{_safe(conflict.explanation)}")
                lines.append(
                    "  - 相关确定性规则："
                    + "、".join(f"`{_safe(code)}`" for code in conflict.deterministic_finding_codes)
                )
            lines.append("")
    elif report.experimental_overall_score is not None:
        lines.extend(
            [
                "## 实验性综合分（旧版公式）",
                "",
                f"{report.experimental_overall_score:.2f}/100",
                "",
                "这是旧版可补偿式加权结果，仅为兼容历史报告保留。",
                "",
            ]
        )
    lines.extend(
        [
            "## 下一步",
            "",
            "- 人工确认事实、隐私、声音是否像自己，以及是否愿意直接录制。",
            "- 若时长明显不足，优先补充具体场景、对话和变化过程，不用同义反复凑字数。",
            "- 录制后以真实音频时长和实际听感修正这份文字阶段的估算。",
            "",
        ]
    )
    markdown = "\n".join(lines)
    if contains_internal_source_identifier(markdown):
        raise RawSourceIdentifierInMarkdown(
            "draft quality export contains an internal source identifier"
        )
    return markdown
