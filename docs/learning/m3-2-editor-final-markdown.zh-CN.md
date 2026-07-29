# M3.2：Editor 与最终 Markdown

## 基本信息

- 阶段：M3.2
- 日期：2026-07-29
- Commit：本章节与实现处于同一个 focused commit
- 状态：实现完成；自动化测试、Fake / DeepSeek E2E、Ruff 与 Alembic 已验证

## 1. 为什么做这一步

M3.1 已经能从初始素材生成采访脚手架，可靠地暂停，并接收用户补充的口述
转写。但 Resume 以后，它只是把补充材料保存下来，并没有真正使用这些材料
生成节目文稿。

这会造成一个明显断点：

```text
系统知道还缺什么
  -> 用户认真补充了一段话
  -> 系统保存了它
  -> 没有下一步成品
```

M3.2 补上这个断点。用户提交补充 Source 后，系统自动排队一个 Editor
Task。Editor 同时读取：

- 已验证的 Interview Scaffold；
- Scaffold 引用到的初始 SourceSegment；
- 用户这次补充的 SourceSegment。

它生成一份结构化候选稿，再由确定性代码导出：

- 可审阅的播客口播稿 Markdown；
- Show Notes Markdown。

这里的关键词是“候选稿”。系统不会自动发布，也不宣称模型已经完全理解
用户的人生。最后的事实、语气和是否采用，仍由用户审核。

## 2. 生活化类比

可以把整个流程想成一次有资料约束的编辑合作。

1. 两位研究助理先从用户允许的材料里整理时间线和主题。
2. 采访者根据这些证据列出采访提纲。
3. 用户看完提纲，补录一段回答。
4. 编辑把旧材料、提纲和新回答放在同一张桌上，整理成口播初稿和节目简介。
5. 用户最后审稿，决定哪些内容保留、修改或删除。

这个类比有一个边界：真实编辑能凭经验判断语义和事实是否合理，当前模型
只能在 Prompt、严格 Schema 和引用白名单的约束下生成候选内容。引用合法
只说明“能追到某段素材”，不等于形式化证明每句话都被原文完整蕴含，所以
最终人工审核不能省略。

## 3. 完成了什么

### 3.1 新的 workflow v4

新建的 `episode-research` Run 使用 workflow `v4`：

```text
导入初始文字 Source
  -> 创建 episode-research v4 Run
  -> Timeline / Theme 两个 Researcher 并行
  -> Manager 确定性 fan-in
  -> Interviewer 串行生成采访脚手架
  -> waiting_for_user / awaiting_interview_response
  -> 用户导入已转写的补充 Source
  -> POST /runs/{run_id}/resume
  -> 创建 user_material_submission
  -> 排队 build_podcast_draft Editor Task
  -> Editor 生成严格结构化结果
  -> succeeded / complete
  -> 分别导出 Scaffold、Podcast Draft、Show Notes
  -> 用户最终审核
```

旧 Run 不会被部署偷偷升级：

- v1 仍在 research bundle 后结束；
- v2 仍在 Interview Scaffold 后结束；
- v3 Resume 后仍按 M3.1 语义确定性结束，不产生 Editor 调用；
- 只有新建的 v4 Run 进入 Editor。

### 3.2 一次完整 v4 Run 的数量

到人工等待点：

| 对象 | 数量 | 内容 |
| --- | ---: | --- |
| Task | 4 | Manager、Timeline、Theme、Interviewer |
| Artifact | 4 | 两份研究结果、research bundle、Interview Scaffold |
| ModelCall | 3 | Timeline、Theme、Interviewer |

第一次有效 Resume 后，Editor 进入队列。Editor 成功后的最终状态：

| 对象 | 数量 | 新增内容 |
| --- | ---: | --- |
| Task | 5 | 新增 `build_podcast_draft` |
| Artifact | 6 | 新增 `user_material_submission` 与 `build_podcast_draft_result` |
| ModelCall | 4 | 新增一次 Editor Provider attempt |

Manager 的 fan-in 是普通应用代码，不调用模型，因此不产生 ModelCall。
Fake Provider 的四条调用记录仍会存在，但 Token 与费用都是零。

### 3.3 三个可独立读取的导出

```text
GET /runs/{run_id}/exports/interview-scaffold.md
GET /runs/{run_id}/exports/podcast-draft.md
GET /runs/{run_id}/exports/show-notes.md
```

Scaffold 在等待时就可以读取，而且 Editor 完成后仍保持不变。Podcast Draft
和 Show Notes 只有在 Editor 成功并且最终 Artifact 合法时才返回 200；未就绪
返回 409，不存在的 Run 返回 404。

三个 Markdown 都由后端根据结构化 Artifact 确定性渲染，不让模型直接控制
Markdown 文档结构。正文引用显示为 `[S1]` 等短标签，文末来源索引显示
Source 标题和片段位置；内部 `src_...` / `seg_...` 仍保留在数据库和结构化
Artifact 中，不泄漏到面向用户的正文。

### 3.4 人工与自动化的边界

这一阶段只保留一个有意的中间人工检查点：

```text
自动研究和生成 Scaffold
  -> 人工阅读问题、补充一段口述转写并 Resume
  -> 自动运行 Editor、生成两个最终 Markdown
  -> 人工最终审稿
```

中间必须由人补充内容，因为这正是产品价值：AI 不能替用户编造没有说过的
人生材料。日常自动化 E2E 会使用 committed synthetic fixture 代替真人，
自动导入一份合成补充 Source 并调用 Resume，因此测试时不需要手点 Swagger。

“口述”目前仍是文字：用户先把已经转成文字的内容通过 `POST /sources`
导入。M3.2 不申请麦克风权限，不接收音频，也不做实时 STT、TTS 或语音克隆。

## 4. 代码模块地图

| 文件或目录 | 作用 | 为什么放在这里 |
| --- | --- | --- |
| `backend/src/epiphany/editor_schemas.py` | Editor 输入、Podcast Script、Show Notes 的 strict schema 和引用校验 | 把模型契约与调度代码分开 |
| `backend/src/epiphany/runtime/editor_prompts.py` | 构建有输入大小上限、把素材标记为不可信数据的 Editor Prompt | Provider 只负责发送请求，Prompt 规则集中维护 |
| `backend/src/epiphany/runtime/orchestrator.py` | v4 Resume 后排队 Editor，并在成功后推进 Run | 调度顺序由确定性代码决定 |
| `backend/src/epiphany/runtime/output_validation.py` | 在 Artifact 提交前分派 Editor validator | 所有 Provider 共用同一验证边界 |
| `backend/src/epiphany/runtime/providers/fake.py` | 生成稳定、可读、带初始和补充引用的 Fake 草稿 | 免费回归不依赖网络和模型随机性 |
| `backend/src/epiphany/runtime/providers/deepseek.py` | 为 Editor 使用专用 Prompt、输入上限和输出 Token 上限 | 保留同一 Provider 抽象，同时控制真实调用 |
| `backend/src/epiphany/services.py` | Resume 幂等提交、构造 Editor 输入、查找最终 Artifact、导出 Markdown | HTTP 之外的产品用例集中在 Service |
| `backend/src/epiphany/episode_markdown.py` | 安全渲染 Podcast Draft 和 Show Notes | 模型只生成 JSON，应用代码控制 Markdown |
| `backend/src/epiphany/api.py` | 暴露两个新增最终导出 endpoint | FastAPI 层只做协议和错误映射 |
| `backend/src/epiphany/checkpoint_e2e.py` | 驱动 Source 到最终 Markdown 的完整 API 流程 | 同一命令验证真实应用边界 |
| `backend/tests/test_editor_core.py` | strict schema、引用、Prompt 和 Markdown 单元测试 | 快速定位 Editor 契约问题 |
| `backend/tests/test_editor_workflow.py` | retry、预算、Artifact 与事件的 Workflow 测试 | 验证可靠性而不依赖 HTTP |
| `backend/tests/test_human_checkpoint_api.py` | Resume、v3 兼容、最终导出、取消与重启测试 | 验证 API 产品行为 |
| `backend/tests/test_checkpoint_e2e.py` | Fake E2E、证据文件、日志脱敏与故障路径 | 防止全链路局部都绿、组合后却失败 |

## 5. 背后的技术点

### 5.1 Agent 只产出候选 Artifact，代码决定下一步

Editor 不会自己创建 Task、发布节目或修改 Run。它只接收一个受限输入并返回
候选 JSON。Orchestrator 决定什么时候排队 Editor；Worker 负责领取、调用
Provider、校验、提交 Artifact；最后仍由 Orchestrator 把 Run 改为
`succeeded`。

这保持了项目的核心边界：

- 模型负责内容候选；
- 普通代码负责状态、预算、调度和副作用。

### 5.2 strict grounded Editor

Editor 输出禁止额外字段，并且每个口播段落、section、Show Notes summary
和 key point 都必须带 `source_refs`。

validator 还会检查：

1. `title` 必须逐字等于 Run 的 topic；
2. 所有引用只能来自 Editor 获准读取的初始或补充片段；
3. Podcast Script 至少使用一条初始来源；
4. Podcast Script 至少使用一条补充来源；
5. Show Notes 也至少使用一条补充来源；
6. Scaffold 中的引用必须能解析回初始 SourceSegment。

因此模型不能用格式正确但完全忽略用户新回答的稿子蒙混过关，也不能引用
本次 Task 没有获准读取的 Source。

### 5.3 输入文本是不可信数据

Source 里可能碰巧写着“忽略规则”“输出密钥”等文字。Prompt 明确告诉模型：
topic、Scaffold、初始素材和补充素材都只是数据，不是系统命令。

同时，Editor 输入会先通过 Pydantic 校验，再序列化为有字符上限的 JSON。
这不是绝对安全沙箱，但能建立清楚、可测试的 prompt-injection 边界。

### 5.4 为什么 Editor 有单独的大小和输出限制

Researcher 每次只看部分原始素材，Interviewer 看合并研究结果；Editor 则要
同时读取 Scaffold、初始证据和补充证据，并返回更长的口播稿。三者的输入和
输出规模不同，不能继续共用一个模糊上限。

默认配置：

```text
EPIPHANY_DEEPSEEK_MAX_SOURCE_CHARS=24000
EPIPHANY_DEEPSEEK_MAX_INTERVIEW_BUNDLE_CHARS=24000
EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS=48000
EPIPHANY_DEEPSEEK_MAX_TOKENS=2000
EPIPHANY_DEEPSEEK_EDITOR_MAX_TOKENS=6000
```

E2E 为了控制真实调用风险，会使用更严格的 Editor 输入上限 32,000 字符、
最多四次调用、每个 Task 一次尝试和并发一。

### 5.5 数据库到底保存了什么

M3.2 复用现有表，没有新 migration：

- `sources` / `source_segments`
  - 保存初始文字和补充口述转写的规范化正文、片段、顺序和 hash；
- `runs`
  - 保存 workflow v4、状态、当前步骤和最终 `output_artifact_id`；
- `tasks`
  - 保存五个 Task；
  - Editor Task 的受限 `input_json` 包含已验证 Scaffold、初始引用片段和补充
    引用片段，因为 Worker 必须在重启后仍能继续执行；
- `artifacts`
  - 保存两份研究结果、research bundle、Scaffold；
  - `user_material_submission` 只保存 Source/Segment 引用，不复制转写正文；
  - `build_podcast_draft_result` 保存通过 strict validator 的结构化 Podcast
    Script、Show Notes、引用和运行 metadata；
- `model_calls`
  - 每个 Provider attempt 一行，记录 provider、model、attempt、Token、
    延迟、估算费用、币种、状态和错误码；
- `events`
  - 保存状态变化和对象 ID，不保存素材正文、Prompt、模型完整响应或 Key。

最终 `output_artifact_id` 指向 `build_podcast_draft_result`。旧的 Scaffold
Artifact 并没有被覆盖，因此 Scaffold endpoint 在 Editor 成功后仍能找到并
导出它。

### 5.6 Resume 为什么仍要幂等

浏览器或网络可能在服务端已成功以后超时，用户再次点击 Resume。如果每次
都创建 Editor Task，就可能重复调用付费模型。

调用方提供稳定 `submission_id`：

- 相同 `submission_id` + 相同 Source 列表：返回同一个提交，不新增 Task、
  Artifact、Event 或 ModelCall；
- 相同 `submission_id` + 不同 Source：返回 409；
- v4 第一次合法提交：只创建一个 submission Artifact 和一个 Editor Task。

在写入 submission、改变 Run 状态或排队 Editor 以前，Service 会先验证完整
Editor 输入。如果把原始 Source 又当作补充 Source，或者补充内容超过 500 个
片段，Resume 会返回 409，Run 仍停在 `waiting_for_user`，不会留下半成品，
也不会产生一次注定失败的付费调用。

### 5.7 retry、重启、取消和预算

- **Retry**：Editor 遇到可重试 Provider 错误时复用同一个逻辑 Task，以新
  attempt 再运行；每次 attempt 都有独立 ModelCall。最终只提交一份幂等
  Editor Artifact。
- **重启恢复**：Task、输入、lease 和状态在 SQLite。进程退出后，过期的
  `running` Task 会重新入队，不依赖内存继续。
- **取消**：Run 等待用户或 Editor 排队/运行时都可以取消；取消后的 Task
  不能提交迟到结果。
- **预算**：Provider 请求前先检查并预留 ModelCall。v4 正常需要四次调用；
  如果预算只允许三次，Editor 会在网络入口前以
  `model_call_limit_exceeded` 失败，不会偷偷发送第四次请求。

当前仍是单进程运行边界。跨多个服务进程的 Resume 需要 PostgreSQL 行锁、
CAS 或等价协调，留到部署强化阶段。

## 6. 自动化测试

本次验证结果：

```text
151 passed
Ruff lint passed
Ruff format check passed
Alembic upgrade/current/check passed
Fake M3.2 E2E passed
DeepSeek synthetic M3.2 E2E passed
```

完整测试：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest
ruff check src tests
ruff format --check src tests
alembic upgrade head
alembic current
alembic check
```

Editor 定向测试：

```bash
pytest tests/test_editor_core.py \
       tests/test_editor_workflow.py \
       tests/test_human_checkpoint_api.py \
       tests/test_checkpoint_e2e.py \
       tests/test_deepseek_provider.py \
       tests/test_deepseek_research_workflow.py -vv
```

这些测试覆盖：

- strict schema、topic 和引用白名单；
- Script 与 Show Notes 必须真正使用补充 Source；
- Prompt 输入大小上限和不可信数据边界；
- Markdown 转义、短引用、来源索引，以及已知/未知、原始/转义形式的内部
  ID 都不泄漏；
- v4 正常 `waiting -> Resume -> Editor -> succeeded`；
- v1 / v2 / v3 在途 Run 保留历史语义；
- Resume 幂等、冲突、404 / 409 / 422，以及无效 Editor 输入的原子拒绝；
- Editor Prompt 与公共 Provider 在干净 Python 进程中独立导入；
- Editor retry 只提交一个最终 Artifact；
- 第四次调用的预算拒绝发生在 Provider 前；
- 等待和 Editor 阶段的取消；
- 重启后继续领取 Editor；
- Podcast Draft / Show Notes 的 200 / 404 / 409；
- mocked DeepSeek 四调用集成；
- Fake E2E 的状态、计数、事件顺序、日志脱敏和最终 Markdown。

2026-07-29 又显式执行了同一合成 fixture 的真实 DeepSeek E2E：

```text
run_id = run_88d16bf3e03f45a98edfea2c164e383a
tasks = 5 succeeded
artifacts = 6
model_calls = 4 succeeded
events = 26 before Resume / 36 final
input_tokens = 16,667
output_tokens = 9,468
provider_duration_ms = 73,018
estimated_cost = CNY 0.035603
```

全部 guarded checks 为 true，包括三个 Markdown、补充来源使用、Resume
幂等、Scaffold 稳定和日志脱敏。费用来自本地配置价格表，不是厂商账单；
fixture 是合成材料，真实调用通过也不等于可以跳过人工内容审核。

## 7. 本地手动验证

### 7.1 最省事：运行自动 Fake E2E

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
python -m epiphany.checkpoint_e2e --provider fake --execute
```

这条命令使用 committed synthetic fixture，自动完成所有原本需要 Swagger
手点的步骤。默认证据文件：

```text
backend/data/editor-e2e.db
backend/artifacts/editor-e2e/runtime.jsonl
backend/artifacts/editor-e2e/report.json
backend/artifacts/editor-e2e/interview-scaffold.md
backend/artifacts/editor-e2e/podcast-draft.md
backend/artifacts/editor-e2e/show-notes.md
```

成功时 `report.json` 的 `passed` 为 `true`，最终计数为：

```text
workflow_version = v4
status = succeeded
current_step = complete
tasks = 5
artifacts = 6
model_calls = 4
```

等待点之后应该恰好新增十个持久 Event：

```text
run.resumed
workflow.user_material.accepted
task.queued
workflow.editor.queued
task.started
model.call.started
model.call.completed
task.succeeded
workflow.editor.completed
run.succeeded
```

E2E 还会验证：

- Scaffold 在 Resume 前后 hash 不变；
- Scaffold 不提前包含补充口述；
- Draft 与 Show Notes 都使用补充 Source；
- 相同 Resume 重放不新增对象；
- submission Artifact 不复制补充正文；
- 日志不包含 fixture 正文或密钥。

### 7.2 用 Swagger 看见人工边界

1. 启动 `uvicorn epiphany.main:app --reload`。
2. 打开 <http://127.0.0.1:8000/docs>。
3. 用 `POST /sources` 导入初始文字。
4. 用 `POST /runs` 创建 `episode-research` Run。
5. 轮询 `GET /runs/{run_id}`，直到
   `waiting_for_user / awaiting_interview_response`。
6. 下载 `GET /runs/{run_id}/exports/interview-scaffold.md`。
7. 再用 `POST /sources` 导入一段
   `source_type="voice_note_transcript"` 的文字。
8. 用补充 Source ID 调用 `POST /runs/{run_id}/resume`。
9. 第一次 Resume 返回时 Editor 可能仍是 `queued` 或 `running`，这是正常的；
   继续轮询 Run，直到 `succeeded / complete`。
10. 分别下载 Podcast Draft 与 Show Notes。
11. 人工核对事实、语气和来源，再决定如何修改或发布。

若本地 `.env` 使用 Fake：

```text
EPIPHANY_MODEL_PROVIDER=fake
```

整个流程不联网、不收费。修改 `.env` 后必须重启 Uvicorn；配置只在进程启动
时读取。

### 7.3 DeepSeek 只在明确需要时执行

安全 dry-run：

```bash
python -m epiphany.checkpoint_e2e --provider deepseek
```

它只打印 preflight，不发送请求。只有以下命令会读取本地 Key 并产生最多四次
真实 API 调用：

```bash
python -m epiphany.checkpoint_e2e --provider deepseek --execute
```

执行前应检查 preflight 的 provider、model、币种、调用上限、输入上限、
输出 Token 上限和文件路径。真实执行会产生费用；聊天订阅不能抵扣 API 费用。

## 8. 日志与排错

稳定的 M3.2 关键事件：

- 持久 Event：
  - `workflow.editor.queued`
  - `workflow.editor.completed`
  - 以及通用 `task.*`、`model.call.*`、`run.succeeded`
- stdout 操作日志：
  - `workflow.editor.queued`
  - `workflow.editor.completed`
  - `run.podcast_draft_markdown.exported`
  - `run.show_notes_markdown.exported`

排查顺序：

1. 从 HTTP 响应保存 `X-Request-ID` 和 `run_id`；
2. 查看 `GET /runs/{run_id}`，确认 workflow version、Run 状态和 Editor Task；
3. 查看 `GET /runs/{run_id}/events`，判断是未 Resume、未排队、Provider
   失败、validator 拒绝，还是导出未就绪；
4. 查看 Editor Task 的 `error_code`；
5. 查看对应 `ModelCall` 的 status、attempt、Token、费用和 `error_code`；
6. 用 `run_id` / `task_id` / `model_call_id` 搜索 JSON 日志；
7. 若 API 报 409，先确认 Run 是否真正成功，以及最终 Artifact 是否是
   `build_podcast_draft_result`；
8. 用最小定向 pytest 复现；
9. 必要时只读查看 SQLite，不要把正文或 Key 复制进 issue。

常见情况：

- `provider_input_too_large`：Editor 组合输入超过
  `EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS`；
- `podcast_draft_schema_invalid`：模型 JSON 结构不符合 strict schema；
- `invalid_podcast_draft_source_reference`：引用越过允许 SourceSegment；
- `podcast_draft_missing_supplemental_source_reference`：结果没有真正使用用户
  新补充的材料；
- `model_call_limit_exceeded`：单 Run 预算不足以进入第四次 Editor 调用；
- 最终导出 409：Editor 尚未成功，或最终 Artifact / 引用元数据无效。

日志只能记录 ID、计数、状态、耗时和错误码。不得记录 Source 正文、Prompt、
模型完整响应、API Key 或导出的节目正文。

## 9. 这一步学到了什么

1. **Human-in-the-loop 不是一句产品文案，而是可持久化状态。** 人离开多久，
   Run 都能从数据库知道自己在等什么。
2. **Resume 不应该等于同步跑完整个模型调用。** 它可靠保存提交并排队工作，
   Worker 异步完成 Editor，API 调用和长任务因此解耦。
3. **Agent 编排不是让多个模型自由聊天。** 这里的“编排”主要是代码决定
   fan-out、fan-in、暂停、Resume、预算和终态。
4. **结构化结果比直接让模型写 Markdown 更可靠。** Schema 可以先验证引用
   和字段，渲染器再控制面向用户的格式。
5. **引用可追踪不等于内容一定正确。** 工程约束能阻止很多越权和漏引用，
   但个人叙事仍需要本人最终审稿。
6. **可靠性必须覆盖最贵的最后一步。** retry、幂等、预算和重启不只适用于
   Researcher，也必须覆盖 Editor，否则最接近成品的环节反而最脆弱。

## 10. 限制与下一步

当前仍然没有：

- 正式 Web UI 和可视化 Run Trace；
- 浏览器录音、音频上传、STT、TTS 或语音克隆；
- 自动事实蕴含验证和写作质量评分；
- 多轮“审稿意见 -> 再编辑”检查点；
- 自动发布到播客平台；
- PostgreSQL / 多进程 Worker 的 Resume 协调；
- 使用本人隐私素材的 Editor 内容质量验收；

下一阶段可以进入 M4 可靠性与 Trace 强化，或按 roadmap 继续准备 M5 最小
Web UI。UI 不需要重新发明 workflow：它只需复用现有 Source、Run、Resume、
Events 和三个 Markdown 导出 API。

## 完成检查

- [x] 正常路径测试通过
- [x] 失败路径测试通过
- [x] Fake 本地 E2E 验证通过
- [x] 日志中无隐私内容
- [x] README / Roadmap / Devlog 已同步
- [x] 学习手册已同步
- [x] 合成素材真实 DeepSeek E2E
- [x] 已准备进入 focused commit
