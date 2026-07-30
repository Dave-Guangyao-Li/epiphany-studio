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

    lines.extend(["", "## 模型建议性自评", ""])
    if report.model_self_review is None:
        lines.append(
            "- 本次模型自评不可用："
            f"{_safe(report.model_review_unavailable_reason or '原因未记录')}。"
        )
    else:
        relation = {
            "same_model": "与写稿模型相同",
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
            lines.append("")

    if report.experimental_overall_score is not None:
        lines.extend(
            [
                "## 实验性综合分",
                "",
                f"{report.experimental_overall_score:.2f}/100",
                "",
                "该分数只用于同一版本规则下的回归比较，不代表客观质量，"
                "也不能替代真实录制与听众反馈。",
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
