# M3.4：Draft Quality Report、模型自评与用户反馈

更新时间：2026-07-29

## 1. 这一阶段解决什么问题

M3.3 已经能在写稿前判断素材是否**明显不够**，但“素材够了”和“稿子好用”
不是一回事。

例如，用户选择生成 15 分钟的口播稿，Editor 可能仍然出现：

- 实际文字只能支持 7 分钟；
- 为了凑长度反复说同一件事；
- 每段都有来源 ID，但内容仍然空泛；
- 大量使用模板句、排比句或“不是……而是……”；
- 语气不符合 Creative Brief；
- 看起来结构完整，用户却不愿意真的开麦录制。

M3.4 在 Editor 后面增加一份**可解释的质量报告**。它不是“AI 判卷器”，
而是把三种性质不同的证据放在一起：

```text
可重复计算的规则
  + 有逐字证据的模型建议
  + 与自动流程分开保存的用户评价
```

这样，系统不仅能生成稿子，还能回答：

1. 稿子是否接近请求的 10 / 15 / 30 分钟；
2. 哪些可观察问题是代码确定发现的；
3. 模型为什么给某一维度打这个分；
4. 模型看不出来的地方，是否诚实标记为无法评价；
5. 用户本人是否觉得“像我”“能录”“有用”。

## 2. 一个不需要技术背景的类比

可以把 M3.4 想成出版前的三轮检查：

| 检查者 | 擅长什么 | 不能冒充什么 |
| --- | --- | --- |
| 计算器 | 数字数、查重复、检查引用、匹配明确禁用词 | 不能判断一段故事是否动人 |
| 编辑助理 | 按六个维度阅读，并指出原稿中的逐字证据 | 不能假装自己是作者或真实听众 |
| 作者本人 | 判断是否像自己的声音、是否愿意直接录 | 不应和自动化测试反馈混在一起 |

计算器得到的是**确定性指标**：输入不变、规则版本不变，结果就不变。
编辑助理得到的是**模型自评**：它有参考价值，但可能受模型偏好影响。
作者本人提交的是**用户反馈**：它独立保存，不会被模型分数覆盖。

## 3. v6 Workflow 如何运行

带 `creative_brief` 的新 Run 默认启用 `draft_quality`，因此使用 workflow
`v6`。如果调用方显式提交
`"draft_quality": {"enabled": false}`，则保留 v5 行为，不产生 Reviewer
调用和质量报告。

v6 在 M3.3 的完整流程后继续：

```text
Researchers 并行研究
  -> 确定性 fan-in
  -> Interviewer 生成采访脚手架
  -> 确定性 Material Readiness
  -> waiting_for_user / awaiting_more_material
  -> 用户补充已转成文字的 Source
  -> 幂等 Resume
  -> Editor 生成口播稿与 Show Notes
  -> 代码立即计算 DraftMetrics
  -> 排队一个串行 Quality Reviewer Task
  -> 校验 Reviewer 的六维结果和逐字证据
  -> 代码合成 DraftQualityReport
  -> Run succeeded，最终 output 仍然是口播稿
  -> 用户可以另行提交真实反馈
```

Reviewer 是一个 `parent_task_id=None` 的串行根 Task，不是新的并行
Researcher，也不会递归创建 Subagent。正常一次 v6 流程共有六个 Task：

1. Manager；
2. Timeline Researcher；
3. Theme Researcher；
4. Interviewer；
5. Editor；
6. Quality Reviewer。

默认使用同一个 Provider 时，Editor 和 Reviewer 可能是同一个模型。因此
报告会明确写 `reviewer_relation="same_model"`，并始终写
`model_review_advisory=true`。这叫**同模型自评**，不是独立专家评价。

## 4. 10 / 15 / 30 分钟是怎样估算的

Creative Brief 允许选择 `10`、`15` 或 `30` 分钟，并保存
`speaking_rate_chars_per_minute`。默认值是每分钟 280 个非空白字符。

首版公式很简单：

```text
估算分钟数 = 口播正文的非空白字符数 / 每分钟字符数
```

例如，正文是 2,520 个非空白字符：

```text
2520 / 280 = 9 分钟
```

这只是**文字阶段估算**，不是录音实测。停顿、笑声、语速、英文比例和临场
发挥都会改变真正时长。用户录完以后，可以在反馈中填写
`observed_duration_minutes`，把真实录制结果与估算分开保存。

时长偏离有两层：

- 超出目标上下 15%：warning；
- 少于目标 50% 或超过目标 150%：blocker。

如果素材不足，系统不会要求模型重复同义句凑字数。M3.3 会尽量在 Editor
以前要求继续补充材料；如果 Editor 最终仍明显太短，M3.4 会在质量报告中
留下明确的时长 blocker 或 warning，建议补充具体场景、对话、动作和认知
变化，而不是添加 filler。

## 5. 确定性指标检查什么

`analyze_podcast_draft` 只检查能被普通代码稳定观察到的东西：

### 5.1 时长

- 目标分钟数；
- 非空白字符数；
- 配置的每分钟字符数；
- 估算分钟数；
- 是否落在允许范围。

### 5.2 来源

- 口播段落总数；
- 有 SourceReference 的段落数；
- 段落引用覆盖率；
- 使用了多少个独立 Source 和 Segment。

引用覆盖率只证明“这段有可追踪引用”，不自动证明每句话都被来源语义蕴含。
事实和隐私仍需要人工确认。

### 5.3 重复

- 完全相同的规范化段落；
- 重复八字符窗口比例。

八字符窗口是一种跨中文文本也能工作的局部重复信号：代码把正文滑动切成
连续八字符片段，再统计重复的多余次数。它比只按英文单词分词更适合当前
中文口播场景，但仍只是启发式规则。

### 5.4 Brief 与表达模式

- `must_include` 中哪些明确文字没有出现；
- `avoid_patterns` 中哪些明确模式出现；
- 固定口语填充词命中数；
- 常见模板化短语命中数；
- “不是……而是……”句式命中数。

这些命中不是在判断“是不是 AI 写的”。真人也会使用这些表达，好的文章也
可能有排比。报告只说明：**这个可观察模式出现了几次，超过了当前规则的
哪个阈值**。

确定性分数是实验性的回归指标。它适合比较同一规则版本下的两版稿子，
不代表文学质量，也不应跨 profile 直接比较。

## 6. 为什么不输出“AI 味概率”

项目不会输出“这篇稿子有 83% 是 AI 写的”一类结论。

原因不是技术上不想做，而是这个数字很容易制造虚假的确定感：

- OpenAI 曾公开提供 AI 文本分类器，后来因准确率低而下线；
- LLM-as-a-judge 研究记录了位置偏差、冗长偏差和自我增强偏差；
- 同一个模型评价自己生成的稿子，更不能包装成客观第三方结论。

因此 M3.4 用更诚实的方式呈现问题：

```text
不说：“AI 味 76%”

而说：
- “值得注意的是”出现 4 次，阈值为 2；
- “不是……而是……”出现 5 次，阈值为 2；
- 发现 2 个规范化后完全重复的段落；
- Reviewer 对口播自然度打 2/5，并引用了具体原句。
```

这让用户可以检查证据，也可以不同意系统的建议。

## 7. 六维模型自评

Reviewer 必须且只能返回以下六个维度：

1. `brief_adherence`：是否符合场景、听众、目标和语气；
2. `source_faithfulness`：表述是否忠于被 Draft 实际引用的素材；
3. `coverage_and_specificity`：是否覆盖关键内容并包含具体细节；
4. `structure_and_coherence`：开场、章节、转场、收束是否连贯；
5. `oral_naturalness_and_voice_fit`：是否像能说出口的话，是否贴合
   Creative Brief 中的声音；
6. `conciseness_and_non_redundancy`：是否简洁、有没有同义反复。

每个维度不是都必须硬打分。Reviewer 可以返回：

```json
{
  "assessable": false,
  "score": null,
  "limitation": "现有文字无法证明真实录制时的自然度",
  "evidence": []
}
```

如果 `assessable=true`，则必须有：

- 1 到 5 分；
- 简短 assessment；
- Draft 中的稳定 `location`；
- 该位置中的 `exact_quote`；
- 来源忠实度维度还必须提供与证据对应的 SourceReference。

## 8. 为什么要校验 quote 和 location

让模型“给理由”还不够，因为模型可能写出听起来合理、却不存在于草稿中的
理由。M3.4 会用代码做第二次校验：

1. `location` 必须是 Draft 真实存在的字段路径；
2. `exact_quote` 必须逐字存在于该字段；
3. 证据里的 SourceReference 必须属于 Reviewer 获准读取的范围；
4. SourceReference 还必须真正挂在该 Draft block 上；
5. `source_faithfulness` 如果可评价，至少一条证据必须带来源引用；
6. assessment、limitation 和 quote 不得泄露内部 `src_...` / `seg_...` ID。

因此模型不能只说“第二段不自然”，也不能伪造一句正文。它必须指向例如：

```text
location:
podcast_script.sections[1].paragraphs[2]

exact_quote:
“我当时没有马上关掉录音，而是把那三秒停顿重新听了一遍。”
```

## 9. 最终 decision 为什么由代码决定

Reviewer 只提交六张建议卡片，不提交最终结论。最终 decision 由应用代码
组合确定性结果和模型结果：

| decision | 含义 |
| --- | --- |
| `blocked` | 确定性规则发现硬性 blocker；优先级最高 |
| `automated_review_incomplete` | 没有确定性 blocker，但 Reviewer 不可用或六维不能完整折算 |
| `revision_recommended` | 有 warning，或至少一个模型维度不高于 2 分 |
| `candidate_ready_for_human_review` | 自动检查未发现上述问题，可以交给人审稿 |

实验性综合分使用有版本号的 60 / 40 公式：

```text
综合分 = 确定性分数 × 60% + 六维模型平均分换算值 × 40%
```

只要六维中有一维不可评价，就不生成综合分，避免用一个数字掩盖信息缺口。
无论 decision 和分数是什么，`requires_human_review` 永远为 `true`。

## 10. Reviewer 失败时为什么不让整条 Run 失败

Editor 已经生成并通过严格来源 Schema 校验的 Draft，Reviewer 属于后置的
辅助审稿。如果 Reviewer 因鉴权、预算、网络或最终重试失败而不可用：

- Reviewer Task 会保留真实失败状态和错误码；
- 已生成的 Draft 不会丢失；
- 确定性 `draft_metrics_report` 仍然保留；
- 系统生成 `model_review_status="unavailable"` 的质量报告；
- 若确定性规则已有 blocker，decision 仍为 `blocked`；否则为
  `automated_review_incomplete`；
- Run 仍可 `succeeded`；
- `output_artifact_id` 继续指向 Editor Draft；
- 用户仍可导出口播稿并自己审核。

这叫 graceful degradation（优雅降级）：辅助能力坏了，不应该把已经可用的
核心产物一起藏起来。Reviewer 的 retry、lease recovery、取消、调用预算和
ModelCall 账本仍复用 Worker 的既有可靠性机制。

## 11. 用户反馈为什么独立保存

自动报告回答“系统观察到了什么”，用户反馈回答“我是否愿意使用它”。

`POST /runs/{run_id}/quality-feedback` 支持：

- `decision`：accepted / needs_revision / rejected；
- overall、voice match、recordability、usefulness、tone fit 五项 1–5 分；
- `would_record_as_is`；
- 可选真实录制时长；
- 可选评论；
- 稳定 `submission_id`，用于网络重放幂等。

`feedback_origin` 必须明确为：

- `human`：调用方声明为真实用户评价，
  `human_signal_eligible=true`；
- `synthetic_test`：自动 E2E 的模拟反馈，
  `human_signal_eligible=false`。

系统根据 origin 自己计算 `human_signal_eligible`，客户端不能直接提交这个
派生字段。但当前本地 MVP 没有鉴权，因此 origin 本身仍是调用方自报标签，
不是已验证的真人身份。生产统计需要在未来接入用户身份后才把它当作可信
信号。同一个 submission 和相同内容重放不会产生第二份 Artifact；同 ID
改成另一份内容会返回 409。

评论正文只进入 `draft_user_feedback` Artifact，不进入 Event 和 stdout
日志；它仍可通过本地 Run/API/SQLite 数据读取，并不等于加密保密。事件只
记录 origin、decision、总体分、是否愿意直接录等非正文摘要。

## 12. 数据保存在哪里

M3.4 没有新增数据库表，也没有 Alembic migration。它复用现有结构：

| 数据 | 保存位置 |
| --- | --- |
| 确定性指标 | `artifacts.kind="draft_metrics_report"` |
| Reviewer 严格六维输出 | `artifacts.kind="review_podcast_draft_result"` |
| 代码合成的最终报告 | `artifacts.kind="draft_quality_report"` |
| 每次用户评价 | `artifacts.kind="draft_user_feedback"` |
| Reviewer 调用 Token、耗时、费用 | `model_calls` |
| 排队、完成、降级、反馈记录 | `events` |

原始 Source 文本和 Draft 正文不会复制进 Event 或 stdout 日志。Reviewer 输入
只包含 Draft 实际引用的 SourceSegment，而不是 Run 曾经导入过的全部素材。

更完整的只读查询见
[SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)。

## 13. API 与 Swagger 手动验证

启动本地后端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
alembic upgrade head
uvicorn epiphany.main:app --reload
```

浏览器打开：

```text
http://127.0.0.1:8000/docs
```

创建带 Creative Brief 的 Run 时，省略 `draft_quality` 就会默认启用 v6：

```json
{
  "workflow_type": "episode-research",
  "payload": {
    "topic": "五年后重新开始记录生活",
    "source_ids": ["替换成真实 Source ID"],
    "creative_brief": {
      "target_duration_minutes": 10,
      "speaking_rate_chars_per_minute": 280,
      "scenario": "reflective_solo",
      "target_audience": "正在经历人生转折的普通听众",
      "communication_goal": "用具体经历解释为什么重新开始记录",
      "tone": ["真诚", "克制", "自然口语"],
      "must_include": ["重新开始"],
      "avoid_patterns": ["空泛排比", "强行金句"]
    }
  }
}
```

走完 Source、等待点和 Resume 后，依次检查：

```text
GET /runs/{run_id}
GET /runs/{run_id}/quality-report
GET /runs/{run_id}/exports/quality-report.md
GET /runs/{run_id}/exports/podcast-draft.md
GET /runs/{run_id}/events
```

提交一份真实用户反馈：

```json
{
  "submission_id": "ep0-review-1",
  "feedback_origin": "human",
  "decision": "needs_revision",
  "overall_rating": 4,
  "voice_match_rating": 3,
  "recordability_rating": 4,
  "usefulness_rating": 5,
  "tone_fit_rating": 4,
  "would_record_as_is": false,
  "observed_duration_minutes": 8.7,
  "comment": "第二节还是有一点像总结，希望保留更多当时说话的犹豫。"
}
```

调用：

```text
POST /runs/{run_id}/quality-feedback
GET  /runs/{run_id}/quality-feedback
```

## 14. 自动化测试

只验证 M3.4 的重点：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

pytest \
  tests/test_draft_quality.py \
  tests/test_draft_quality_provider.py \
  tests/test_draft_quality_workflow.py \
  tests/test_draft_feedback_api.py -vv
```

这些测试覆盖：

- v6 默认开启和显式 opt-out 回到 v5；
- 时长、引用、重复、模板、filler 与 Brief 规则；
- 六维 Schema、quote/location 和 SourceReference 越权拒绝；
- Editor 后只排队一个 Reviewer；
- 正常完成、重试、预算拒绝、永久失败降级；
- lease 过期恢复和取消；
- Draft 始终是 Run 的最终 output；
- 用户反馈创建、幂等重放、冲突、列表和 origin 隔离；
- 反馈评论不进入 Event 或日志，但仍保存在 Artifact 中。

一条命令运行隐私安全、零费用的 v6 Fake E2E：

```bash
python -m epiphany.draft_quality_e2e --provider fake --execute
```

运行完整验证：

```bash
pytest
ruff check .
ruff format --check .
alembic upgrade head
alembic check
```

2026-07-29 的阶段验收快照：

```text
ruff check: passed
ruff format --check: 71 files passed
pytest: 205 / 205 passed
alembic upgrade + check: passed, No new upgrade operations
M3.3 Fake regression E2E: passed
M3.4 Fake E2E: passed
```

205 是这次 commit 前的可复现验证快照，不是项目以后永远不变的测试总数。

## 15. 日志和 Event 看什么

M3.4 的稳定 Event 包括：

```text
workflow.draft_metrics.evaluated
workflow.draft_self_review.queued
workflow.draft_self_review.completed
workflow.draft_self_review.unavailable
workflow.draft_quality.completed
workflow.draft_quality.feedback_recorded
```

常用关联字段：

- `run_id`
- `task_id`
- `draft_artifact_id`
- `metrics_artifact_id`
- `quality_report_id`
- `quality_decision`
- `hard_blocker_count`
- `warning_count`
- `feedback_origin`
- `human_signal_eligible`

日志不应包含 Source 正文、Draft 正文、模型完整输出、反馈评论或 API Key。
排查顺序仍然是：

1. `GET /runs/{run_id}` 看状态；
2. `GET /runs/{run_id}/events` 看持久时间线；
3. 用 `run_id` / `task_id` 搜 stdout JSON；
4. 最后用只读 SQLite 查 Artifact 和 ModelCall。

## 16. Fake E2E 和真实 DeepSeek E2E 的边界

自动 E2E 可以全自动使用仓库中固定、隐私安全的合成 Source 和补充材料，
并提交一份 `synthetic_test` 反馈。这样可以稳定测试工程流程，不要求每次由
用户本人现场口述。

但必须区分：

- Fake Provider E2E：证明状态机、持久化、引用、报告和 API 可重复工作；
- DeepSeek E2E：证明真实生成和 Reviewer 可以通过相同合同；
- synthetic feedback：只证明反馈接口可用；
- human feedback：才代表用户本人对声音和可录性的评价。

Fake E2E 的质量 decision 即使是 `blocked` 或 `revision_recommended` 也可以
算工程测试成功，因为 Fake 输出的目标是稳定和可重复，不是冒充高质量创作。

### 16.1 真实 DeepSeek 成功 Run

2026-07-29 使用同一份隐私安全合成 fixture 完成受限 v6 E2E：

```text
run_id: run_276a3bce22394eb8a56edd6af8760012
provider/model: deepseek / deepseek-v4-flash
workflow: v6
Tasks: 6 / 6 succeeded
ModelCalls: 5 / 5 succeeded
Artifacts: 11；提交 synthetic feedback 后为 12
input tokens: 26,618
output tokens: 11,239
combined Provider duration: 61,669 ms
local estimated cost: CNY 0.049096
```

五次调用依次覆盖两个 Researchers、Interviewer、Editor 和 Quality
Reviewer。E2E 还验证了：

- 三个 App lifespan 之间的持久暂停与重启；
- Editor 后排队的 Reviewer 在 SQLite 中真实存在，不依赖内存续命；
- supplemental Source 被 Draft 引用；
- `synthetic_test` feedback 第一次创建、相同提交幂等重放；
- 反馈后 Artifact 从 11 变为 12，但不会被标成真人评价；
- 85 行日志全部是可解析 JSON；
- 日志不包含 Source 原文、生成稿正文或反馈评论。

### 16.2 内容质量结果

成功 Run 的质量报告为：

```text
decision: revision_recommended
deterministic score: 72
same-model Reviewer: 6 个维度全部 5/5
experimental overall score: 83.2
```

确定性指标给出的证据是：

| 指标 | 结果 |
| --- | --- |
| 目标时长 | 10 分钟 |
| 非空白正文字符 | 1,429 |
| 估算时长 | 5.1 分钟 |
| 段落引用覆盖 | 100% |
| 来源范围 | 4 Sources / 10 Segments |
| 完全重复段落 | 0 |
| filler 命中 | 1 |
| “不是……而是……” | 4 |

这份结果非常有价值：真实模型给自己的六维评价全部 5/5，但可重复计算的时长
仍然表明稿子只达到目标的一半左右。实验综合分 83.2 也没有把 decision 改成
“可以直接发布”。代码仍然给出 `revision_recommended`。

这不是系统“打架”，而是在展示三种证据的不同性质：

- 同模型 Reviewer 擅长给编辑建议，但可能偏宽松；
- 确定性规则能稳定指出字数和重复问题，但不能评价人生故事是否动人；
- 最终是否像本人、是否愿意录，仍需真实用户反馈。

因此这份稿子的正确下一步是补充具体场景、对话、感受和变化过程，再重新生成；
不是添加空话，把 5.1 分钟机械灌到 10 分钟。

### 16.3 前一次失败为什么也要记录

成功 Run 以前还有一次付费真实尝试。它没有被包装成成功，也没有删除 Trace：

```text
error_code: podcast_draft_missing_supplemental_source_reference
local estimated debugging cost: about CNY 0.039696
```

当时 Editor 虽然返回了 Draft，但 strict validator 发现末尾内容没有满足
补充 Source 引用合同，因此在 Artifact 提交前拒绝。修复不是放松 validator，
而是增强 Editor 输出末尾的引用自检，然后重新运行；下一次才通过。

前一次约 CNY 0.039696 属于开发调试费用，不能并入成功 Run 的 CNY
0.049096，也不能从工程成本记录中抹掉。两笔都是本地价格表估算，不是官方
发票。DeepSeek Dashboard 或最终账单可能因为实际计费规则、缓存输入处理和
用量同步延迟而与本地数字存在差异。

## 17. 为什么采用这套评估边界

本设计参考了以下一手资料，同时刻意保留其限制：

- [OpenAI：AI 文本分类器因准确率低而下线](https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/)：
  支持“不输出 AI 作者概率”的产品边界。
- [G-Eval](https://aclanthology.org/2023.emnlp-main.153/)：
  说明结构化、分维度的模型评价可以辅助文本评估，但相关性不是客观真理。
- [MT-Bench / LLM-as-a-Judge](https://arxiv.org/abs/2306.05685)：
  记录位置、冗长与自我增强等 judge bias，因此同模型结果必须标记
  self-review / advisory。
- [NIST AI RMF Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
  与 [NIST Measure Playbook](https://airc.nist.gov/airmf-resources/playbook/measure/)：
  支持明确指标、限制、证据和人工反馈，而不是只给一个神秘总分。
- [BREAD：跨语言重复检测](https://aclanthology.org/2023.gem-1.27/)：
  为字符 n-gram 重复信号提供研究背景。
- [ALCE：长文本生成中的引用评估](https://aclanthology.org/2023.emnlp-main.398/)：
  支持把引用存在性、正确性与最终人工判断区分开。

## 18. 本阶段明确不做什么

M3.4 不做：

- 自动重写直到分数达标；
- 用同一个模型循环自我改稿；
- 多模型“陪审团”；
- AI 作者身份检测；
- 自动发布；
- 麦克风采集、STT、TTS 或 voice cloning；
- 把 synthetic E2E 评价写成真实用户认可。

下一步更值得做的是最小 Web UI：让用户在页面中看到 Creative Brief、
等待点、Draft、质量报告和反馈表单，并保留后端现有的可追踪执行过程。
