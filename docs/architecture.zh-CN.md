# 轻量 Agent Runtime 架构

状态：Draft

日期：2026-07-29

## 1. 目标

第一版架构需要同时满足：

- 足够简单，可以个人开发和本机运行；
- 能真实练习 Agent 编排、后端状态和可靠性；
- 进程失败后不完全丢失工作；
- 不把产品状态藏进某个 Agent 框架的内部对象；
- 将来可以逐步替换数据库、队列或模型 Provider。

## 2. 系统边界

```text
Web UI
  |
  | HTTP + SSE
  v
FastAPI
  |- Run Service
  |- Source Service
  |- Event Service
  |- Orchestrator
  |
  +--> SQLite (source of truth)
  +--> Local Artifact Store
  +--> Durable Worker Loop
          |
          +--> Hosted Model Provider
          +--> Fake Provider
```

## 3. 编排与调度的边界

### 模型可以决定

- 哪些已有素材可能相关；
- 应从哪个受允许的研究角色获取帮助；
- 证据中还缺少什么；
- 如何组织问题和草稿。

### 代码必须决定

- 允许调用哪些 Agent；
- 最大深度和并发数；
- Task 的依赖、状态和停止条件；
- timeout、retry 和预算；
- 取消传播；
- 哪些写操作需要用户确认；
- 最终状态是否有效。

MVP 使用固定 Workflow。模型不能发明任意 Agent 名称，也不能递归创建
Subagent。

## 4. 第一个 Workflow

```text
create_run
  -> prepare_sources
  -> fan_out
       |- timeline_research
       `- theme_research
  -> fan_in (deterministic)
  -> build_interview_scaffold (serial root Interviewer)
  -> assess_material_readiness (deterministic)
  -> wait_for_user / awaiting_more_material
  -> import supplemental Source + idempotent Resume
  -> reassess accumulated material (deterministic)
  -> build_podcast_draft (serial root Editor)
  -> validate strict structure and source scope
  -> render Podcast Draft / Show Notes
  -> evaluate deterministic Draft metrics
  -> review_podcast_draft (serial root Quality Reviewer)
  -> validate location + verbatim quote evidence
  -> synthesize code-owned Draft Quality Report
  -> human final review
  -> complete
```

其中 `prepare_sources`、`fan_in` 和状态更新是普通代码；
`timeline_research`、`theme_research`、`build_interview_scaffold`、
`build_podcast_draft` 和 `review_podcast_draft` 才调用模型。
`assess_material_readiness` 不调用模型。

M2.4 将可执行边界推进到 `build_interview_scaffold`：两个 Researcher
仍以同级 Child Task 并行调用模型，确定性 fan-in 持久化研究 Bundle 后，才
排队一个 `parent_task_id=None` 的串行根 Interviewer Task。它不是第三个
并行 Child，也不会与研究调用重叠。一次成功的 v2 Run 因而固定产生四个
Task（Manager、两个 Researcher、Interviewer）、四个 Artifact（两个研究
结果、一个研究 Bundle、一个采访脚手架）和三次 ModelCall。

M3.1 将当前边界推进到人工检查点。新的 `episode-research` Run 使用
workflow v3：Interviewer 成功后保存脚手架，但 Run 进入
`waiting_for_user / awaiting_interview_response`，而不是立刻成功。用户把
已经转成文字的补充口述作为新 Source 导入，再用 Source ID 调用 Resume。
Resume 保存一份只含引用的 `user_material_submission` Artifact，并在同一
事务中完成 `waiting -> running -> succeeded`。此步没有新增 Task 或
ModelCall，最终 output 暂时仍是采访脚手架。

M3.2 将新建 Run 升级为 workflow v4。人工检查点以前的执行形状不变；
第一次合法 Resume 保存 submission 后，将 Run 保持为 `running`，并排队
一个 `parent_task_id=None` 的串行根 `build_podcast_draft` Editor Task。
Editor 输入由已验证 Scaffold、Scaffold 实际引用的初始 SourceSegment 和本轮
补充 SourceSegment 组成。Worker 对结构、topic、引用范围及补充材料使用情况
做严格校验，成功后持久化 `build_podcast_draft_result` 并将其设为 Run 的
最终 output。一条完整 v4 Run 因而有五个 Task、六个 Artifact 和四次
ModelCall。

M3.3 为带 `creative_brief` 的新 Run 使用 workflow v5。Interviewer 完成后，
普通代码只读取 Scaffold 实际引用到的初始 SourceSegment，并持久化
`material_readiness_report`。Run 停在
`waiting_for_user / awaiting_more_material`；Resume 会把所有已经接受的补充
Source 合并后重新计算。仍不足时再次进入同一检查点，达到门槛时才排队一个
Editor。正常一轮补充后的 v5 Run 在等待时为四个 Task、五个 Artifact 和
三次 ModelCall；成功后为五个 Task、八个 Artifact 和四次 ModelCall。
多出的 Artifact 是两份 Readiness Report 与一份用户材料提交。

初始原文的最小披露规则与 v4 一致：Readiness 可以计算且 Editor 可以读取的
初始片段集合，严格等于已验证 Scaffold 的引用集合；它不会因为 Source 曾被
选入 Run 就把整份私人原文继续发送给最后一个模型。补充材料按已接受轮次累计，
重复提交初始或历史 Source 会在持久化前拒绝，累计补充上限为 500 个
SourceSegment。Editor 输入另受 Provider 的 48,000 字符上限保护。

M3.4 为带 Creative Brief 且没有显式关闭 `draft_quality` 的新 Run 使用
workflow v6。Editor 成功后，普通代码先保存一份
`draft_metrics_report`，计算目标/估算时长、段落引用覆盖、来源多样性、重复、
Brief 文字约束、固定 filler 与模板表达。随后排队一个
`parent_task_id=None` 的串行根 `review_podcast_draft` Task。它固定评价六个
维度，每个可评价维度必须提供 Draft 字段路径和逐字 quote；代码再验证 quote
确实存在、引用未越权，并合成 `draft_quality_report`。

正常一轮补充的 v6 Run 完成时为六个 Task、十一个 Artifact 和五次
ModelCall。三个新增质量 Artifact 分别是确定性指标、Reviewer 严格结果和最终
质量报告。Run 的 `output_artifact_id` 仍指向 Editor Draft，而不是报告。
显式提交 `draft_quality.enabled=false` 会保持 v5 的四次调用路径。

Reviewer 只是 advisory。若它与 Editor 使用相同 Provider/model，报告标记
`reviewer_relation=same_model`；它不能自称独立人工评价，也不能覆盖确定性
blocker。Reviewer 最终失败或预算不足时，系统保留失败原因：已有确定性
blocker 时 decision 仍为 `blocked`，否则为
`automated_review_incomplete`。Run 正常完成，使已经通过来源合同的 Draft
仍可导出。用户反馈通过独立 append-only Artifact 保存；当前 origin 是
调用方自报的分类，E2E 的 `synthetic_test` 会明确标记为非真人信号。

为使升级时已经在途的 Run 仍可恢复，v1 保留原有语义：fan-in 后以
`episode_research_bundle` 成功结束，不要求新增 `topic`，也不排队
Interviewer；v2 仍在采访脚手架完成后成功；v3 Resume 后仍按 M3.1 语义
确定性结束，不产生 Editor 调用；没有 Creative Brief 的请求仍走 v4；
显式关闭质量审阅的 Brief 请求走 v5。六个版本分支都复用现有表和字段，
M3.4 不需要数据库 migration。

## 5. Subagent 定义

Subagent 是一个受约束的 Child Task，而不是独立微服务：

```json
{
  "task_id": "task_...",
  "run_id": "run_...",
  "parent_task_id": "task_manager",
  "agent_type": "timeline_researcher",
  "objective": "从允许的素材中提取人生时间节点",
  "source_segment_ids": ["src_01:12", "src_02:04"],
  "output_schema": "TimelineEvent[]",
  "tool_policy": ["read_source_segments"],
  "deadline_seconds": 120,
  "model_call_budget": 1
}
```

子任务只返回结构化候选 Artifact。它不能直接更改已确认的记忆。

M2.4 的 Interviewer 同样受 Task/Provider/ModelCall 契约约束，但它是 fan-in
之后的根 Task，只读取已经校验并合并的研究结果，不扩大一层父子拓扑。

M3.2 的 Editor 也是串行根 Task。它不是动态派生的新 Child 层级，也不会与
人工输入并发；只有持久化 Resume 成功后才能排队。模型提交候选 Podcast
Script 和 Show Notes，最终状态、导出和发布权限仍由代码与用户控制。

M3.3 的 Readiness 不是 Agent 或 Task，而是确定性业务规则。它不占用模型
调用预算，输入正文只在内存中用于去重和计数，持久报告仅保留阈值、聚合计数、
gap code、限制说明和带 SourceReference 的追问。
计数先按稳定 SourceSegment 引用去重，再按移除空白后的正文内容去重；来源
多样性只统计真正贡献了新内容的 Source，不能靠复制同一段文字跨过门槛。

M3.4 的 Quality Reviewer 是一个独立串行 Task，但在默认配置下可能复用
Editor 的同一个模型。它只读取 Draft 实际引用到的 SourceSegment，并通过
strict schema 返回六张证据卡。`assessable=true` 时必须带 1–5 分、稳定
location 和存在于该 block 中的 exact quote；无法可靠评价时必须使用
`assessable=false` 并说明 limitation，不能编造证据。最终 decision 和
60/40 实验性分数由普通代码计算，且无论结果如何都要求人工审稿。

## 6. 持久化模型

### `runs`

- `id`
- `workflow_type`
- `workflow_version`
- `status`
- `current_step`
- `input_ref`
- `output_ref`
- `model_call_count`
- `cancel_requested_at`
- `created_at`
- `updated_at`

### `tasks`

- `id`
- `run_id`
- `parent_task_id`
- `agent_type`
- `status`
- `attempt`
- `input_ref`
- `output_ref`
- `idempotency_key`
- `lease_token`
- `lease_expires_at`
- `error_code`
- `created_at`
- `updated_at`

### `events`

- `id`
- `run_id`
- `task_id`
- `sequence`
- `type`
- `payload`
- `created_at`

### `model_calls`

- `id`
- `run_id`
- `task_id`
- `attempt`
- `provider`
- `model`
- `status`
- `input_tokens`
- `output_tokens`
- `duration_ms`
- `estimated_cost_micros`
- `cost_currency`
- `error_code`
- `started_at`
- `completed_at`

`estimated_cost_micros` 与 `cost_currency` 共同构成一次调用的费用估算，不是
厂商账单。不同币种必须分别汇总；任何展示币种换算都属于带汇率来源和时间的
派生数据，不能覆盖原始调用记录。

### 领域对象

- `projects`（M3 前补充）
- `sources`
  - 规范化全文、SHA-256、类型、metadata、字符数
- `source_segments`
  - 稳定片段 ID、顺序、原文、字符区间、SHA-256
- `artifacts`
- `memory_candidates`

M2.1 使用规范化正文 hash 生成稳定 Source ID，使用 Source hash、片段顺序
和片段 hash 生成稳定 Segment ID。唯一约束保证重复或并发重试不会生成
第二份 Source。列表 API 只返回摘要，详情 API 返回有序片段，不直接返回
整篇 `content_text`。

## 7. 状态机

### Run

```text
queued
  -> running
  -> waiting_for_user
  -> running
  -> succeeded | failed | cancelled
```

### Task

```text
queued
  -> running
  -> succeeded | failed | cancelled
```

终态不可回退。重试创建新的 `attempt`，但保留稳定的逻辑 `task_id`。

## 8. Worker

MVP 只有一个 Worker 进程，但任务存在 SQLite 中，不只存在内存中。

Worker 循环：

1. 依次在短事务中领取最多两个 `queued` Task。
2. 为每个 Task 写入独立的 `lease_token` 和过期时间。
3. 使用 `asyncio.gather` 并行运行模型调用或确定性 Handler。
4. 严格校验结构化输出和本次 Task 允许的 Source 引用。
5. 持久化幂等 Artifact。
6. 使用当前 lease/fencing token 提交终态并追加 Event。
7. 触发 Orchestrator 判断是否仍需等待，或执行确定性 fan-in。

同一进程内并发上限固定为二。M2.2 的单 Worker 在一个短的 finalization
临界区中串行提交 Child 终态，避免两个同时完成的 Child 都看见过期的
兄弟状态；耗时的 Provider 调用仍然真实并发。未来多 Worker 需要借助
PostgreSQL 行锁或等价的数据库协调后再解除这个单进程约束。

M3 的 `RunService` 在单进程内用同一个 mutation lock 串行 Resume 与
Cancel，防止两个请求同时从 `waiting_for_user` 穿过状态边界。相同 Resume
由 Artifact idempotency key 防止重复落库。这个边界不等于多进程
exactly-once：两个独立 `RunService` 同时写入时，SQLite 唯一约束能阻止
重复数据，但 loser 还不会被转换成友好的 replay/409。多进程部署前应加入
数据库 CAS/行锁，或捕获唯一约束后回读已有提交。

进程启动时将已过期的 `running` Task 重新排队。后续如果需要多 Worker，
再迁移 PostgreSQL，不在 SQLite 上模拟分布式队列。

Alembic 是数据库 schema 的唯一变更入口。正常应用启动不得调用
`metadata.create_all()` 自动补表，否则会出现“表已经存在但 migration
版本未前进”的 schema drift。`create_all()` 只用于隔离的临时测试库。

## 9. 可靠性基线

MVP 必须实现：

- 稳定的 Run/Task/Event ID；
- append-only 事件；
- 每个副作用的 idempotency key；
- 有界 retry，默认只重试瞬时读取或模型网络错误；
- parent cancel 标记；
- Child 提交结果时验证 cancel 状态和 lease token；
- 已完成只读 Artifact 可保留；
- 模型输出经过 Pydantic 严格校验；
- 单 Run 调用和并发上限；
- 启动恢复测试。

不承诺 exactly-once。采用 at-least-once execution + idempotent commit。

## 10. 模型 Provider

定义最小接口：

```python
class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, invocation: TaskInvocation) -> ProviderResult: ...
```

首批实现：

- `FakeProvider`：测试状态机、恢复和调用记账，不联网、不产生 API 费用；
- `DeepSeekProvider`（M2.3b）：首个真实托管模型适配器；
- 其他厂商以后保持在同一契约后面，不让 Workflow 绑定某个 SDK。

M2.3a 在调用 Provider 以前先写入一条 `ModelCall(status=started)`，同时写入
Provider、model 与配置的费用币种，并原子地增加 Run 调用数。这样即使请求在
返回 usage 前遇到认证、限流、网络错误或 timeout，失败记录仍有正确币种。
完成后更新 tokens、耗时、估算费用和错误码。唯一约束
`(task_id, attempt)` 防止同一次尝试重复记账；retry 是新 attempt，因此单独
记账。单进程 Worker 使用短锁保护“检查预算 + 预留调用”，避免两个并发 Child
同时越过上限。

真实模型的 Key、模型名、API 地址和数据保留选项通过配置传入。本地数据库
仍是产品状态来源；不得在日志或 Event 中保存 prompt、响应正文或密钥。

M2.3b 的 DeepSeek 适配器直接使用 `httpx`，自身不执行 retry。一次
`generate()` 最多发送一个 HTTP 请求；429、500、503、网络和 timeout 交回
Worker，以新的 Task attempt 和 `ModelCall` 重试。JSON Output 仍需通过
Pydantic、引用范围和逐字 Quote 校验。

M2.4 为 Interviewer 增加独立的 strict 输入、Prompt 与输出契约。两个
Researcher 同时接收 `topic` 与 SourceSegment，并把二者都视为不可信数据；
topic 只帮助筛选相关证据，不能改变系统规则。Interviewer Prompt 只序列化
已校验 Timeline/Theme 结果和从中收集的 `allowed_source_refs`，研究文字仍被
视为不可信数据。Provider 和应用配置为原始 Researcher 输入、已校验的合并
研究 Bundle 提供两个独立字符上限，避免聚合结果错误复用单份素材限制；默认
都为 24,000 以保持兼容，realistic E2E 则显式使用 8,000 / 24,000。输出禁止额外字段，
标题必须逐字等于 Run 的 `topic`；episode intent、开场、收束、section、
known context、transition、question 和 material gap 都必须带引用，且引用
只能来自研究 Bundle。Worker 在 Artifact 提交前统一调度这套验证，未知 Agent
若没有注册 validator 会直接失败。

Interviewer 还必须保留素材中的事实状态：计划、草稿、愿望、准备和尝试不能
改写成已经完成或发布。当前这是一条 Prompt 约束，不是形式化语义证明；
引用白名单只验证可追踪性，正式内容仍需要人工确认或未来的 claim-level
verifier。

调用预算仍在进入 Provider 前原子预留。将单 Run 预算设为二时，两个并行
Researcher 可以完成，第三个 Interviewer 调用会以
`model_call_limit_exceeded` 在 Provider 入口前被拒绝；Run 失败，但两个研究
结果与确定性 fan-in Bundle 继续保留，便于诊断或后续恢复。

首版只允许官方 `https://api.deepseek.com`，默认模型为
`deepseek-v4-flash`，thinking 关闭。单 Task 还有素材字符数和输出 Token
上限。即使 HTTP 200 的内容被截断或 JSON 不可用，只要响应带有可信 usage，
失败的 `ModelCall` 也必须保存 Token 和预估费用。

M3.2 为 Editor 增加第三种独立输入边界和单独输出上限。默认：

```text
EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS=48000
EPIPHANY_DEEPSEEK_EDITOR_MAX_TOKENS=20000
EPIPHANY_DEEPSEEK_MAX_QUALITY_BUNDLE_CHARS=80000
EPIPHANY_DEEPSEEK_QUALITY_REVIEW_MAX_TOKENS=6000
```

Editor 把 Scaffold、topic、初始片段与补充片段都视为不可信数据，并只允许
原样复制输入白名单中的 SourceReference。Strict validator 要求 title 等于
topic，Podcast Script 同时使用初始与补充引用，Show Notes 也至少使用一条
补充引用。未知或越权引用、缺失补充证据和结构漂移都会在 Artifact 提交前
失败。合法引用仍不是语义蕴含证明，候选稿必须由用户最终审核。

Quality Reviewer 的输入由结构化 Draft、Creative Brief、质量 profile 和
Draft 实际引用的 SourceSegment 组成。Prompt 中的正文一律按不可信数据
处理。Strict validator 固定六个 dimension，逐一验证 assessable 状态、
1–5 分、location、exact quote 和引用范围；模型不能返回最终 decision。
本阶段不尝试判断文本的作者身份，也不生成“AI 概率”。

正常 v4/v5 都需要四次 Provider 调用，正常 v6 需要五次。将单 Run 预算设为三时，Editor 调用会在
进入 Provider 以前以 `model_call_limit_exceeded` 被拒绝。Editor retry、
timeout、lease、fencing、startup recovery 和 cancel 复用同一 Worker 机制；
每个重试 attempt 单独记账，但 Artifact 通过稳定 idempotency key 只提交一次。
若 v6 预算只够四次，Reviewer 会以 `model_call_limit_exceeded` 失败并触发
质量报告降级；已经生成的 Draft 不会因此变成失败产物。

## 11. API 和事件

最小 API：

```text
POST /projects
POST /projects/{id}/sources
POST /projects/{id}/episode-runs
GET  /runs/{id}
GET  /runs/{id}/exports/interview-scaffold.md
GET  /runs/{id}/exports/podcast-draft.md
GET  /runs/{id}/exports/show-notes.md
GET  /runs/{id}/quality-report
GET  /runs/{id}/exports/quality-report.md
POST /runs/{id}/quality-feedback
GET  /runs/{id}/quality-feedback
POST /runs/{id}/resume
POST /runs/{id}/cancel
GET  /runs/{id}/events
GET  /runs/{id}/events/stream
```

SSE 用于低成本实时显示。客户端断线后先从数据库按 `sequence` 补事件，
再连接实时流。SSE 不是状态真相。

Scaffold 导出接受 `waiting_for_user` 或 `succeeded`，并从该 Run 已完成的
`build_interview_scaffold_result` Artifact 读取内容；它不依赖最终
`output_artifact_id`，因此 v4 Editor 成功后仍能导出同一份 Scaffold。
Podcast Draft 与 Show Notes 只接受最终成功且 `output_artifact_id` 指向合法
`build_podcast_draft_result` 的 Run。未就绪、类型不符或内容无效时返回
409。

Readiness 首版不增加单独 endpoint；`GET /runs/{id}` 的 Artifact 列表会返回
所有 `material_readiness_report`，按创建时间可以看到初始判断和每轮补充后的
变化。未来 UI 直接消费这一结构，无需解析运行日志。

Draft Quality Report 有单独 JSON 与 Markdown endpoint。它不替换
`output_artifact_id`；Run 成功后，最终 output 仍是
`build_podcast_draft_result`。用户反馈只能提交给已经成功且确实输出 Podcast
Draft 的 Run。反馈采用稳定 `submission_id` 幂等追加，同 ID 不同内容返回
409；`human_signal_eligible` 由服务端依据 `feedback_origin` 计算，调用方
不能自行指定。

Markdown 由已验证 JSON 确定性渲染。正文把原始 Source/Segment ID 显示为
短标签 `[S1]`，文末通过数据库中的 Source 标题与 Segment 位置生成来源索引；
结构化 Artifact 与数据库仍保留原始 ID，因此追踪能力没有丢失。任何引用
无法解析到对应 Source/Segment 元数据时，导出返回 409，不会猜测来源。所有
模型文本会转义 Markdown 控制字符和原始 HTML，避免改变文档结构或注入链接、
标签。运行追踪用的 `_execution` metadata 不会进入导出。

M3 的 Resume 契约是：

```text
POST /sources
  -> source_type = voice_note_transcript
  -> text = 已经转成文字的补充口述

POST /runs/{id}/resume
  -> checkpoint = interview_scaffold（v3/v4）或 material_readiness（v5）
  -> submission_id = 调用方稳定重试键
  -> source_ids = 新 Source ID 列表
```

Resume 不接受原始正文。正文只存于 `sources` / `source_segments`；
`user_material_submission` Artifact 和 Events 只保存检查点、Artifact ID、
Source ID、Segment ID 与计数。相同 submission 和相同 Source 列表重放返回
已有结果；同一 submission 对应不同 Source 返回 409。v4 第一次提交会
确定性创建一个 Editor Task；v5 会先把历史与本轮 Source 累计后重新判断，
达到门槛才创建 Editor。相同请求重放不会再次计算、排队或调用模型。

## 12. 可观测性与调试

系统区分两种 Trace：

- 数据库中的 append-only Event 是持久化产品执行轨迹，用于回答某个 Run
  经过了哪些 Task、状态和 Artifact；
- stdout JSON 日志是运行诊断轨迹，用于回答请求耗时、Worker 领取、重试、
  失败和恢复发生在何时。

HTTP 接受并返回 `X-Request-ID`。同一请求内的服务日志继承该 ID；异步
Worker 日志使用 `run_id`、`task_id` 和 `attempt` 关联。日志只记录标识、
状态、错误代码和耗时，不记录素材正文、prompt、模型输出、密钥或录音。

未来 Web UI 必须保留后端返回的 request ID，在错误界面展示它，并通过
Run/Event API 呈现可回放状态。浏览器控制台不能成为唯一调试来源。

每个纵向切片的完成条件都包括：

- 正常与失败路径测试；
- 稳定的日志 event 名称；
- 可从 API 或持久化 Event 复现问题；
- 必要的手工演示和文档同步。

M2.2 的稳定事件包括 `workflow.fan_out.started`、
`workflow.fan_in.waiting` 和 `workflow.fan_in.completed`。Child 失败时
Event 还会记录 Manager 失败及兄弟 Task 的 `sibling_failed` 取消原因；
stdout 对应 `worker.task.failed` 和迟到结果的
`worker.task.stale_result`，均不包含素材正文或模型输出。

M2.3b 增加 `provider.deepseek.request.started/completed/failed`。它们只记录
Run、Task、attempt、provider、model、Token、费用和错误码，不记录 HTTP
请求体、响应正文、素材或密钥。

M2.4 增加采访脚手架排队、完成与 Markdown 导出的稳定事件/日志。字段只包含
Run、Task、Artifact、ModelCall 等 ID，以及 section、question、引用片段和
Markdown 字符数等计数；不记录研究内容、Prompt、模型输出或导出的正文。

M3.1 新增持久事件
`workflow.user_input.requested`、`run.waiting_for_user`、`run.resumed` 和
`workflow.user_material.accepted`，以及操作日志
`run.resume.accepted`、`run.resume.idempotent_replay` 和
`run.resume.rejected`。它们只记录关联 ID、checkpoint、Source/Segment
数量、状态和错误码；不记录 submission label、补充口述正文或 SourceSegment
文本。

M3.2 新增持久事件 `workflow.editor.queued` 和
`workflow.editor.completed`。最终两个导出产生操作日志
`run.podcast_draft_markdown.exported` 与
`run.show_notes_markdown.exported`。字段只包含 Run、Task、Artifact ID、
引用数量和 Markdown 字符数；不记录节目正文。等待点后的正常 v4 事件顺序
是 Resume 接收、Editor 排队、Task/ModelCall 执行、Editor 完成和
`run.succeeded`。

M3.3 新增 `workflow.material_readiness.evaluated`，只记录报告 Artifact ID、
状态、目标分钟、素材/片段计数和缺少字符数，不记录原文或追问全文。正常 v5
顺序是 Interviewer 完成、Readiness 不足、持久等待、Resume 接收、Readiness
就绪、Editor 排队和最终成功。App 重启时不会自动跨过等待点。

M3.4 新增 `workflow.draft_metrics.evaluated`、
`workflow.draft_self_review.queued`、
`workflow.draft_self_review.completed`、
`workflow.draft_self_review.unavailable`、
`workflow.draft_quality.completed` 与
`workflow.draft_quality.feedback_recorded`。事件只记录 Artifact ID、分数、
decision、blocker/warning 数量、错误码和反馈摘要，不记录 Draft、Source、
模型 assessment 或用户 comment 正文。反馈网络重放只写操作日志，不重复写
持久 Event。

## 13. 升级触发条件

只有出现以下证据时才升级：

- 多进程或多机器 Worker：SQLite -> PostgreSQL；
- 任务量要求独立 Broker：引入队列；
- Workflow 数量和分支难以维护：评估 LangGraph；
- 长任务跨部署恢复要求显著提升：评估 Temporal；
- 语义检索成为质量瓶颈：评估 embeddings/vector index；
- 执行不受信任的用户代码：引入 sandbox/container。
