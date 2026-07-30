# M3.6：写作样本、改进计划与显式修订

这一章解释 M3.6 新增的两条能力：

1. 用户可以自愿提供过去的文章或口述转写，帮助文稿更接近自己的表达习惯；
2. 用户可以根据质量报告选择怎样改稿，并生成一个可追踪的新候选版本。

M3.6 使用 `episode-research` workflow v8。修订稿属于
`podcast-revision`，同样使用 v8。

## 1. 先建立心智地图

```text
事实 Sources -----------+
补充口述 Source --------+--> Editor --> Draft --> Reviewer --> Quality Report
Creative Brief ---------+                                      |
写作样本(style_only) ----+                                      v
                                                       Improvement Plan
                                                               |
                                                    用户明确选择怎样修改
                                                               v
                                                   podcast-revision 子 Run
                                                               |
                                                               v
                                             新 Draft + 新 Report + Comparison
```

四类输入各自回答不同问题：

- 事实 Source：发生了什么；
- Creative Brief：写给谁、用什么语气、达到什么目的；
- 写作样本：这个人平时怎样组织句子和表达；
- 修订请求：这一轮具体改什么。

它们不能混为一谈。

## 2. 什么是用户写作样本

写作样本可以是：

- 用户以前写过的文章、日记或随笔，`sample_kind=written_prose`；
- 用户自己的口述转写，`sample_kind=spoken_transcript`。

Source 的 `source_type` 可以标成 `writing_sample`，方便界面分类。但真正决定
本次 Run 是否把它用于风格参考的，是创建 Run 时显式提交的
`writing_style_reference`。系统不会因为数据库里恰好有一篇文章就自动模仿。

### 2.1 明确授权

`writing_style_reference_v1` 要求：

```json
{
  "samples": [{"source_id": "src_用户选择的样本", "sample_kind": "written_prose"}],
  "ownership_attested": true,
  "model_processing_consent": true,
  "usage": "style_only"
}
```

- `ownership_attested=true`：用户确认自己有权使用这些文字；
- `model_processing_consent=true`：用户同意本次模型处理这些文字。

缺少任意一项，Pydantic 合同都会拒绝请求。

### 2.2 `style_only` 的边界

样本只能帮助观察句长、节奏、直接程度、转折习惯和口语感。它不能：

- 提供本期人物、时间、地点或事件；
- 成为 Draft 的 `source_refs`；
- 把旧观点自动当成用户现在仍认同的观点；
- 把样本里的文字当成 Prompt 指令执行；
- 覆盖本轮用户刚刚给出的要求。

同一个 Source 不能在同一个 Run 中同时出现在事实 `source_ids` 和
`writing_style_reference.samples`。这避免系统一边说“只看风格”，一边又把
同一段文字当成事实引用。

## 3. 写作样本的真实优先级

写作样本是**最高优先级的个人风格依据**，但不是整个系统的最高优先级。

```text
安全规则与来源事实
    >
本轮明确修订要求与 Creative Brief
    >
用户授权写作样本
    >
系统默认写法
```

例如，旧文章习惯长句，但本轮用户明确说“开场短一点、直接一点”，应当遵守本轮
要求。旧文章写过美国经历，也不能因此把美国经历加入一个无关主题。

## 4. `writing_style_profile` 保存什么

系统最多接收五份样本，并使用
`deterministic_round_robin_v1` 从不同 Source 轮流选片段：

- 最多 20 个片段；
- 最多 12,000 个非空白字符；
- 顺序、选择和统计都可重复计算。

持久化的 `writing_style_profile_v1` 保存片段引用、SHA-256、字符/句子/段落
统计、样本类型统计、选择方法、选择 hash 和 readiness。它不复制写作样本全文。

原文仍存在用户导入的 `sources` / `source_segments` 中；Editor 和 Reviewer
的受限 Task 输入可以按需读取授权片段。Event 与结构化日志只记录数量、状态和
Artifact ID，不应复制私人正文。

## 5. `ready` 与 `limited`

样本只有同时满足以下门槛才是 `ready`：

- 至少 800 个非空白字符；
- 至少 5 句话。

否则是 `limited`，并记录
`insufficient_non_whitespace_chars` 或 `insufficient_sentences`。

`limited` 不代表 Run 失败。Editor 只能把它当成弱提示；Reviewer 也不能声称
“这篇稿子像本人”。达到 `ready` 后，Reviewer 才增加第七维
`personal_style_match`。

## 6. 防复制与 Reviewer 边界

系统要求 Prompt 只抽象表达特征；Draft 的 `source_refs` 只能来自事实 Source；
确定性代码还会检查 Draft 是否复制了样本中独有的连续 24 字符窗口。若命中的
长句并不存在于事实 Source，输出会以
`podcast_draft_writing_style_sample_leak` 被拒绝。

样本为 `ready` 时，Reviewer 使用
`model_self_review_task_v3_writing_style` 和质量公式
`draft_quality_v3_personal_style_non_compensatory_caps`。第七维
`personal_style_match` 必须同时给出 Draft 与样本的逐字证据，并解释句式、
节奏、措辞或口语感的异同。

在模型建议分内部，个人风格是权重最高的单一维度：它占 30%，原有六维合计
占 70%。模型建议分仍只占实验综合分的 40%，确定性规则占 60%，而 blocker、
时长不足和 warning 的 39/59/79 分硬上限不可被风格高分补偿。

它不能判断作者身份或“AI 生成概率”，也不能因风格像就放过事实错误、时长不足
或重复。Reviewer 仍是 advisory；真正最有价值的信号仍是用户提交的
`voice_match_rating`、`recordability_rating` 和文字反馈。

## 7. Improvement Plan：不用模型的改稿导航

成功的 Draft 和 Quality Report 可以通过：

```text
GET /runs/{run_id}/improvement-plan
```

得到 `draft_improvement_plan_v1`。它由普通代码确定性生成，所以不产生新的
模型费用；重复读取返回同一个 Artifact，也不会偷偷创建修订 Run。

计划会计算口播正文的目标字符、实际字符、估算时长和缺口，并区分三条路径。

### 路径 A：复用未使用的事实材料

如果事实 Source 中还有 Draft 未引用的片段，计划列出
`unused_source_refs`，推荐 `reuse_unused_material`。素材已经存在，用户不用
再讲一遍。

### 路径 B：有针对性地补充材料

如果现有事实仍不足，计划从 Interview Scaffold 生成 3 至 6 个
`targeted_questions`。每题带 `prompt`、`purpose`、`anchor_path`、
`anchor_text`、关键词与事实引用。

用户回答后先通过 `POST /sources` 导入文字，再在 Revision 请求中选择
`add_supplemental_material`。

### 路径 C：降低目标时长

如果素材自然只支持更短的稿子，计划可以提供 `lower_target_duration` 和更低
的 10/15 分钟预设。这比让模型反复说同一件事来凑时长更诚实。

三条路径可以组合，例如先使用遗漏材料，不足部分再回答两个具体问题。

## 8. 显式、不可变的子 Revision Run

只有用户提交：

```text
POST /runs/{parent_run_id}/revisions
```

系统才会创建 `podcast-revision` 子 Run。父 Run 保持不可变：

- 原 Draft、Quality Report 和 Feedback 不被覆盖；
- 原 `output_artifact_id` 不变；
- 历史 `model_calls` 不变。

子 Run 通过 `runs.parent_run_id` 指向父 Run，并拥有自己的
`revise_podcast_draft`、新 Draft、`review_podcast_draft`、新 Quality Report
以及独立计算的模型调用额度。Revision 正常只需 Editor + Reviewer 两次调用；
自动测试还会把 per-Run 上限收紧到 2，证明父 Run 已经使用五次调用，也不会
占用子 Run 的额度。

Revision 请求必须带稳定 `submission_id`：

- 同 ID + 相同请求：返回原子 Run，`idempotent_replay=true`；
- 同 ID + 不同请求：返回 409 conflict；
- 新 ID：创建新候选。

系统不会让 Reviewer 自动命令 Editor 无限改稿。每次修订都由用户明确选择材料、
时长、Feedback、Gap 和 `revision_instruction`，避免无限烧预算和模型自我迎合。

## 9. 父子稿比较

子 Run 成功后调用：

```text
GET /runs/{child_run_id}/revision-comparison
```

`draft_revision_comparison_v1` 比较正文字符、估算时长、确定性分数、可用时的
实验综合分、blocker 和 warning 数量。它不复制正文，也不自动选赢家：

```json
{
  "automatic_winner_selected": false,
  "requires_human_review": true
}
```

数字能说明硬指标是否改善，但最终采用哪一稿仍由用户决定。

## 10. 本地启动

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn epiphany.main:app --reload
```

打开 Swagger：<http://127.0.0.1:8000/docs>。

手动验证建议使用 `EPIPHANY_MODEL_PROVIDER=fake`。Fake 不花模型费用，适合先
检查工作流、状态、数据库和日志。切换 DeepSeek 后必须创建新 Run；旧 Run
不会被改写。

## 11. Swagger 手动测试

### 11.1 导入 Source

通过 `POST /sources` 导入：

1. 一份或多份事实素材；
2. 一份至少 800 非空白字符、至少 5 句话的个人写作样本；
3. 一份补充口述转写。

记录三个响应中的 `source.id`。当前“口述”只是已转成文字的
`voice_note_transcript`，还不包含麦克风权限或 STT。

### 11.2 创建父 Run

调用 `POST /runs`：

```json
{
  "workflow_type": "episode-research",
  "payload": {
    "topic": "五年后重新开始记录生活",
    "source_ids": ["src_事实素材"],
    "creative_brief": {
      "target_duration_minutes": 15,
      "target_audience": "正在经历人生转折、想重新开始记录的普通听众",
      "communication_goal": "用具体经历解释为什么重新开始记录"
    },
    "writing_style_reference": {
      "samples": [{"source_id": "src_写作样本", "sample_kind": "written_prose"}],
      "ownership_attested": true,
      "model_processing_consent": true,
      "usage": "style_only"
    }
  }
}
```

事实 ID 与样本 ID 必须不同。轮询 `GET /runs/{run_id}`；需要补素材时应看到
`status=waiting_for_user`、`current_step=awaiting_more_material`。

### 11.3 恢复父 Run

调用 `POST /runs/{run_id}/resume`：

```json
{
  "checkpoint": "material_readiness",
  "submission_id": "manual-material-round-1",
  "source_ids": ["src_补充口述"]
}
```

父 Run 成功后检查：

- `GET /runs/{run_id}/exports/podcast-draft.md`；
- `GET /runs/{run_id}/quality-report`；
- `GET /runs/{run_id}/events`。

### 11.4 提交真人反馈

调用 `POST /runs/{run_id}/quality-feedback`：

```json
{
  "submission_id": "manual-human-review-1",
  "feedback_origin": "human",
  "decision": "needs_revision",
  "overall_rating": 3, "voice_match_rating": 2,
  "recordability_rating": 3, "usefulness_rating": 4, "tone_fit_rating": 3,
  "would_record_as_is": false,
  "observed_duration_minutes": 7.2,
  "comment": "第二段信息有用，但语气还不像我，希望保留更多自然停顿。"
}
```

复制返回的 Feedback `artifact.id`。

### 11.5 读取计划并创建修订

先调用 `GET /runs/{run_id}/improvement-plan`，查看 `options`、`gaps` 和
`targeted_questions`。下面的最小 Revision 只应用真人反馈：

```json
{
  "submission_id": "manual-revision-1",
  "selected_actions": ["apply_selected_feedback"],
  "selected_feedback_artifact_ids": ["art_反馈ID"],
  "selected_gap_codes": [], "source_ids": [],
  "target_duration_minutes": null,
  "revision_instruction": "保留事实边界，开场更直接，第二段改成更自然的口语。"
}
```

提交到 `POST /runs/{parent_run_id}/revisions`，复制子 `run.id`。

若选择 `add_supplemental_material`，`source_ids` 必须包含全新的事实 Source；
若选择 `lower_target_duration`，时长必须是 Plan 实际提供且低于父稿的预设。

### 11.6 检查子 Run

轮询 `GET /runs/{child_run_id}`，预期：

- `workflow_type=podcast-revision`；
- `parent_run_id` 等于父 Run；
- Tasks 包含 `revise_podcast_draft` 和 `review_podcast_draft`；
- 最终 `status=succeeded`。

再读取子稿、子 Quality Report、`revision-comparison` 和父子 Events。最后重新
读取父 Run，确认父稿、父报告、`output_artifact_id`、`model_call_count`
均未变化。

## 12. 数据库与日志

只读打开数据库：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
sqlite3 -readonly data/epiphany.db
```

检查父子关系、独立预算、M3.6 产物和事件：

```sql
.headers on
.mode column
SELECT id, parent_run_id, workflow_type, status, model_call_count
FROM runs ORDER BY created_at DESC LIMIT 10;
SELECT run_id, provider, model, status, estimated_cost_micros, cost_currency
FROM model_calls ORDER BY started_at;
SELECT run_id, kind, id, created_at
FROM artifacts
WHERE kind IN ('writing_style_profile', 'draft_improvement_plan',
               'draft_revision_request', 'draft_revision_comparison')
ORDER BY created_at;
SELECT run_id, sequence, type
FROM events
WHERE type LIKE 'workflow.draft_revision.%'
   OR type IN ('workflow.writing_style_profile.created',
               'workflow.draft_improvement.planned')
ORDER BY run_id, sequence;
```

Uvicorn 结构化日志可用 `request_id`、`run_id`、`child_run_id`、`task_id` 和
`artifact_id` 串起一次操作。日志应描述状态和计数，不应打印 API Key、完整
写作样本或完整反馈正文。

## 13. 自动测试

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_revision_schemas.py \
       tests/test_revision_workflow.py \
       tests/test_draft_improvement.py \
       tests/test_writing_style.py \
       tests/test_draft_quality_provider.py \
       tests/test_guided_revision_e2e.py -vv
python -m epiphany.guided_revision_e2e --execute
pytest -q
ruff check .
ruff format --check .
```

测试覆盖样本授权与隔离、Profile/readiness、复制与引用拦截、Plan 幂等、
显式子 Run、请求重放与冲突、父稿不可变、子预算独立、重新评分、Comparison
不选赢家，以及私人反馈不进入 Event。

## 14. 自动化与人的边界

系统自动完成：

- 有界选择样本并计算 Profile/readiness；
- Research、Interview、Editor、Reviewer 编排；
- 引用、复制、时长、重复和预算约束；
- Improvement Plan、持久化和父子比较。

用户必须决定：

- 是否选择并授权写作样本；
- 本期受众、目标、语气与禁用模式；
- 是否补充材料、降低时长或应用反馈；
- 是否创建 Revision；
- 新稿是否像本人、是否可录；
- 最终采用哪一稿、是否发布。

M3.6 的自动化目标是减少机械工作，不是夺走编辑决定。

## 15. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 没有 `personal_style_match` | 看 `writing_style_context_status`；只有达到 800 字符和 5 句话的 `ready` 样本才启用第七维 |
| 创建 Run 返回 422 | 同一个 Source 可能同时进入事实与风格通道；将两者分开 |
| Improvement Plan 返回 409 | 父 Run 必须成功，并已有 Draft 与 Quality Report |
| Revision 返回 409 | 检查 `submission_id`、Action/参数匹配、新 Source、Plan 时长选项及 Feedback/Gap 归属 |
| 子 Run 一直 `queued` | 确认 Worker 已启用并查看 Task claim 日志；不要用重复提交来催促 |
| Comparison 返回 409 | 等子 Run 生成新 Draft 和新 Quality Report |
| `podcast_draft_writing_style_sample_leak` | 模型复制了样本独特长句；减少模仿式指令后再显式修订 |
| `no such table` 或缺少 `parent_run_id` | 在 `backend` 激活 `.venv` 后执行 `alembic upgrade head`；M3.6 迁移为 `0004_run_lineage` |

## 16. 本阶段学到什么

M3.6 把“更像我”拆成了显式授权、风格/事实隔离、确定性 Profile、固定 Prompt
优先级、输出校验、人工选择、不可变父子 Run、幂等请求和独立预算。模型评价
不能替代硬规则与真人选择；最终“这像不像我、我愿不愿意录”仍由用户回答。
