# 轻量 Agent Runtime 架构

状态：Draft

日期：2026-07-31

## 1. 目标

第一版架构需要同时满足：

- 足够简单，可以个人开发和本机运行；
- 能真实练习 Agent 编排、后端状态和可靠性；
- 进程失败后不完全丢失工作；
- 不把产品状态藏进某个 Agent 框架的内部对象；
- 将来可以逐步替换数据库、队列或模型 Provider。

## 2. 系统边界

```text
Local Web Console (React + Vite)
  |
  | /api proxy: HTTP + SSE
  v
FastAPI
  |- Project Service
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

本地 Console 是现有后端契约的可视化入口，不保存第二份业务状态。Project、
Source、Run、Task、Artifact、Event 与 ModelCall 的真相仍在 SQLite；浏览器
刷新后会重新读取它们。Vite 开发服务器只把 `/api` 代理到本机 FastAPI，避免
为了本地开发额外引入网关或跨域配置。

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
  -> prepare factual Sources
  -> optional consented writing-style profile (deterministic, style-only)
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
  -> project + validate trusted deterministic facts
  -> review_podcast_draft (serial root Quality Reviewer)
  -> validate location + verbatim quote evidence
  -> synthesize code-owned Draft Quality Report + non-compensatory caps
  -> human final review
  -> complete

optional explicit revision
  -> build deterministic Improvement Plan
  -> inventory factual refs wholly absent from spoken units
  -> user selects actions and submits an idempotent request
  -> create podcast-revision child Run with parent_run_id
  -> when reusing material, attach exact spoken-length Recovery Plan
  -> revise_podcast_draft (serial root Revision Editor)
  -> metrics + Reviewer + code-owned quality report
  -> if still short, build trusted anchors from the latest spoken Draft
  -> plan_draft_supplemental_interview (serial root Planner)
  -> persist 3-6 anchored questions; completed Draft remains the Run output
  -> user imports answers as new factual Sources
  -> user explicitly creates the next answer-driven child Revision
  -> lazily persist parent/revision comparison
  -> stop at the duration floor or after two interview rounds
  -> human chooses; no automatic winner or hidden rewrite loop
```

其中 `prepare_sources`、`fan_in` 和状态更新是普通代码；
`timeline_research`、`theme_research`、`build_interview_scaffold`、
`build_podcast_draft`、`revise_podcast_draft` 和
`review_podcast_draft`、`plan_draft_supplemental_interview` 才调用模型。
`assess_material_readiness`、写作风格 profile、Improvement Plan 和新旧稿
comparison 都不调用模型。

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

M3.5 为新质量 Run 使用 workflow v7，不增加 Task、Artifact 类型或
migration。版本升级冻结了持久化语义：v6 继续使用 M3.4 的 Reviewer Task
v1、Prompt v1、`draft_quality_rules_v1` 和报告公式 v1；M3.5 最初使用带可信
确定性事实的 Task/Prompt v2、`draft_quality_rules_v2_chinese_calibration`
和非补偿报告公式 v2。M3.8 新生成的质量 Artifact 使用
`draft_quality_rules_v3_editorial_instruction`、
`zh_podcast_style_v2_enumeration_precision` 和
`deterministic_quality_facts_v2_editorial_instruction`；旧 v1/v2 Artifact
继续按各自版本恢复。变化发生在质量边界内部：

恢复时以持久 Reviewer Task 的合同为准，而不是只看 Run 的版本标签。这样既
能恢复纯 M3.4 v6，也能正确恢复 M3.5 预发布期间曾写出的
`workflow v6 + current deterministic facts` 过渡数据，后者不会丢失 v2
评分上限与模型/代码冲突记录。

1. `draft_metrics_report` 只统计 opening、section paragraph 与 closing 的
   `text`；标题、章节标题、引用对象、来源索引、Show Notes 和 Markdown
   渲染字符都不属于口播时长。
2. Orchestrator 从持久化的 metrics Artifact 投影一份有版本的
   `deterministic_quality_facts`，再与 Draft 一起写入 Reviewer Task 输入。
   Task schema 会重新计算关键事实并核对，防止 stale/tampered fact 进入模型。
3. Reviewer 只能解释这些可信事实，不能覆盖它们。`must_include` 字面 miss
   是 `info`；是否已用同义表达覆盖，由 Reviewer 结合 Draft 和来源判断。
4. 报告保存原始模型分和未封顶 60/40 加权分，再由普通代码应用 39 / 59 / 79
   非补偿上限。确定性 blocker 上限 39，时长覆盖低于 60% 上限 59，任一
   warning 上限 79；多个条件取最严格者。
5. `DraftQualityReport` 在写入和读回时重新推导模型分、未封顶分、cap、
   cap reasons、最终分和 decision，拒绝类型合法但彼此矛盾的 JSON。
6. 中文启发式按版本恢复：历史 `zh_podcast_style_v1` 保持原枚举语义，当前
   `zh_podcast_style_v2_enumeration_precision` 要求列举标记后有标点，并新增
   编辑指令泄漏检查。它们只观察可复现的表达模式，不是 AI 作者检测器。历史
   `template_phrases` / `not_but_pattern` 与新分类重叠时只保留 `info` 展示，
   避免重复扣分。

配置可让 Reviewer 独立路由到另一个受支持的 DeepSeek tier。Worker 仅对
可信 Task kind `review_podcast_draft` 选择 reviewer provider；其他 Task
继续使用 primary provider。ModelCall 与 Artifact `_execution` 保存实际
provider/model，报告将关系区分为 `same_model`、
`cross_tier_same_family` 或 `different_model`。所谓“独立”只是执行与记账
边界独立，跨 tier 仍不等于独立人工审核。

M3.6 为新质量 Run 使用 workflow v8，并增加一条显式的
`podcast-revision` 子 Run 路径。可选 `writing_style_reference` 最多选择
五个用户拥有的现有 Source，且必须同时确认归属和模型处理授权；被选 ID 在
同一个 Run 中不能也出现在事实 `source_ids`。`writing_sample` 只是可选的
导入/UI 标签，显式的 per-Run style-only 选择才是权威合同。普通代码按稳定
round-robin、最多 20 个 Segment /
12,000 个非空白字符建立 profile。持久 profile 只保存引用、hash、统计和
readiness，不复制样本正文；Event 与日志也只记录数量和状态。

Editor 只把样本当作不可信的 `style_only` 数据，优先级低于安全、事实、本轮
明确修订要求和 Creative Brief。样本不能提供本期事实、指令或引用，输出
校验还会拦截对样本独特长句的直接复制。达到 800 个非空白字符和五句话后
profile 才是 `ready`；只有这时 Reviewer v3 才增加
`personal_style_match` 第七维，并要求同时给出 Draft 和 style sample 的逐字
证据。无样本或样本有限时继续使用六维，也不能据此宣称“像本人”。

质量 Run 成功后，服务可以按需持久化 `draft_improvement_plan`。它根据已
验证 Draft/Report/Scaffold 和 Editor 的事实材料，区分可复用的未引用事实、
需要补充的新材料和可以降低的目标时长，并生成三至六个有 Scaffold 锚点的
追问。读取 Plan 不创建 Task 或模型调用。

只有用户显式提交稳定 `submission_id` 的 revision 请求，服务才在同一事务中
保存 `draft_revision_request` 并创建带 `parent_run_id` 的 v8
`podcast-revision`。子 Run 直接排队一次 `revise_podcast_draft`，不重跑
Researcher、Interviewer 或人工检查点；随后重新走 metrics、Reviewer 与质量
报告。父 Draft/Report/Feedback/output 不变，子 Run 有自己的 Task、
ModelCall 账本和预算。同 ID 同请求返回原子 Run，同 ID 改变请求返回冲突。

子 Run 成功后按需建立 `draft_revision_comparison`，只保存双方 Run/Artifact
ID、字符/时长/分数/decision 摘要及 delta，不保存两份正文，也不自动选出
winner。这个比较是帮助人工判断的证据，不是自动发布条件。

为使升级时已经在途的 Run 仍可恢复，v1 保留原有语义：fan-in 后以
`episode_research_bundle` 成功结束，不要求新增 `topic`，也不排队
Interviewer；v2 仍在采访脚手架完成后成功；v3 Resume 后仍按 M3.1 语义
确定性结束，不产生 Editor 调用；没有 Creative Brief 的请求仍走 v4；
显式关闭质量审阅的 Brief 请求走 v5；旧 v6/v7 继续按各自冻结合同恢复；
新建初始质量 Run 和 legacy Revision Request v1 继续走 v8；当前
`draft_revision_request_v2_supplemental_interview` 创建的 Revision 子 Run
走 v9。M3.6 通过 Alembic
`0004_run_lineage` 只给 `runs` 增加可空 `parent_run_id`、外键和索引。

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
60/40 实验性分数由普通代码计算，且无论结果如何都要求人工审稿。M3.5 又把
确定性事实放进受校验的 Reviewer Task 输入，并在 60/40 结果之后应用
代码所有的非补偿 cap；模型高分不能把 blocker 平均掉。

M3.6 的 Revision Editor 同样是一个串行根 Task。它只读取父 Draft、用户明确
选择的 feedback/gap/修订指令，以及允许的事实和 style-only 上下文。它必须
返回完整的新候选而不是原地 patch，并且不能把父 Draft 当作新增事实来源。
随后创建的 Reviewer Task 与 Revision Editor 属于同一个子 Run，因而预算、
重试、取消、恢复和 trace 都不会借用父 Run。

M3.8 没有增加新的 Agent、状态机循环、API 或数据库表。它收紧了
`reuse_unused_material` 进入 Revision Editor 前的确定性输入：

- 口播字符只来自 opening、Section Paragraph 和 closing；
- “已使用引用”也只来自这些口播单元，Section 元数据和 Show Notes 不计入；
- 完全未被口播引用的事实 Segment 保留为可审计 inventory；
- 去除完全重复和已经复制进口播的文本后，普通代码最多选 12 个候选；
- 候选按缺失 must-include、Scaffold gap、补充素材、数字/标点/长度等信号排序；
- 普通代码根据 Creative Brief 生成当前、85% 最低、目标、115% 最高和缺口；
- Revision 后沿用同一套 metrics、Reviewer、非补偿 cap 与 comparison。

该盘点是 ref-level，不是 claim-level。只要一个 Segment 在任一口播单元被引用，
系统就把整段视为 used，无法识别“已引用但展开不足”。未使用 Segment 的非空白
字符总量也只表示候选输入容量，不代表相关性、信息密度或可写成的正文长度。
因此 `existing_material_sufficient` 只允许用户尝试一次有界 Revision，不是
成功承诺。Improvement Plan v2 还会持久化
`prior_length_recovery_attempted`：一次恢复后仍短时，即使 inventory 里还有
片段，也不再推荐连续复用，而是转向补充具体材料或降低目标时长。系统不会自动
让 Reviewer 驱动 Editor 循环重写。

当前 Source Segment 没有结构化 `material_kind`，无法可靠区分事实、反思、
编辑指令和隐私边界。系统不会用脆弱的中文关键词过滤 Source；它会在成稿侧用
`style.editorial_instruction_leakage` 报告明显的元编辑语句。普通“最后一个”
不再被误判为列举，只有“最后，”等带列举标记的表达才计入该启发式。

M3.9 增加一个有边界的 Supplemental Interviewer，但没有增加动态 Agent
拓扑或数据库表。它只在 workflow-v9 的 Revision 子 Run 已完成 Editor、
Reviewer 和质量报告、且口播仍低于 85% 时长下限时排队：

```text
latest valid Draft + latest Quality Report
  -> deterministic spoken anchors (maximum 24)
  -> plan_draft_supplemental_interview
  -> validate exact anchor quote and question contract
  -> supplemental_interview_plan Artifact
  -> Run succeeded with the same Draft output
```

Anchor 只来自最新 Draft 的 opening、正文 Paragraph 和 closing，保存稳定 path、
逐字 excerpt 与该段已验证的 Source references。Planner 输出 3—6 个开放问题；
每个问题必须引用一个允许的 `anchor_id`，且 `anchor_quote` 必须是对应 excerpt
的逐字子串。模型不能自造 question ID；提交 Artifact 前由代码稳定注入
`q1`—`q6`。

用户回答仍通过普通 Source/SourceSegment 保存。下一次显式 Revision Request
把 Plan Artifact ID、回答的 question IDs 和新增 Source IDs 一起持久化，
`PodcastRevisionTaskInput.added_source_ids` 让 Editor 优先融合这些新事实并
保留父稿有效内容。服务端根据 lineage 推导 0/1/2 轮，客户端不能伪造轮次。
达到时长下限或完成两轮后都不会再创建 Planner。

Planner 是增益步骤：最终失败或输出不合法时，Task 保留失败状态和错误码，普通
代码从不同的最新 Draft Anchor 生成
`generation_mode=deterministic_fallback` 的问题计划。有效 Draft、质量报告和
Run 成功状态不受影响。整个阶段复用现有 `runs`、`tasks`、`artifacts`、
`model_calls`、`events` 和 Source 表，因此不需要新的 migration。

## 6. 持久化模型

### `runs`

- `id`
- `project_id`（可为空，Project 页面创建的 Run 必须绑定）
- `parent_run_id`（普通 Run 为空；Revision 子 Run 指向父 Run）
- `submission_id`（Project 内创建 Run 的调用方幂等键）
- `request_fingerprint`（同一幂等键是否仍是同一请求的摘要）
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

`(project_id, submission_id)` 有唯一约束。浏览器双击或网络重试提交相同 key 与
相同 payload 时，服务端回读原 Run，并返回
`X-Idempotent-Replay: true`；同 key 换 payload 返回 409。Revision 子 Run
继承父 Run 的 `project_id`，因此整条 lineage 仍出现在同一个 Project 历史里。

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

- `projects`
  - 本地创作工作区的标题、描述和时间戳
- `project_sources`
  - Project 与全局去重 Source 的多对多关联；复合主键防止重复关联
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
# optional: EPIPHANY_DEEPSEEK_REVIEWER_MODEL=deepseek-v4-pro
EPIPHANY_DEEPSEEK_MAX_QUALITY_BUNDLE_CHARS=80000
EPIPHANY_DEEPSEEK_QUALITY_REVIEW_MAX_TOKENS=6000
```

未设置 `EPIPHANY_DEEPSEEK_REVIEWER_MODEL` 时复用
`EPIPHANY_DEEPSEEK_MODEL`；设置为 `deepseek-v4-flash` 或
`deepseek-v4-pro` 时，只替换 Reviewer Task 的 Provider 实例。它不是一个
“质量保证开关”，费用、耗时和 strict schema 成功率仍须通过 ModelCall 与
实验记录观察。

Editor 把 Scaffold、topic、初始片段与补充片段都视为不可信数据，并只允许
原样复制输入白名单中的 SourceReference。Strict validator 要求 title 等于
topic，Podcast Script 同时使用初始与补充引用，Show Notes 也至少使用一条
补充引用。未知或越权引用、缺失补充证据和结构漂移都会在 Artifact 提交前
失败。合法引用仍不是语义蕴含证明，候选稿必须由用户最终审核。

Quality Reviewer 的输入由结构化 Draft、Creative Brief、质量 profile、
Draft 实际引用的 SourceSegment，以及代码生成的 bounded
`deterministic_quality_facts` 组成。Prompt 中的正文一律按不可信数据处理，
而 facts 由 Task schema 对当前 Draft 重新计算后才信任。Strict validator
固定六个 dimension，逐一验证 assessable 状态、1–5 分、location、
exact quote 和引用范围；模型不能返回最终 decision。本阶段不尝试判断文本
的作者身份，也不生成“AI 概率”。中文 pattern/count 只能说明哪些表达值得
人工复核。

正常 v4/v5 都需要四次 Provider 调用，正常 v6/v7/v8 父质量 Run 需要五次；
一次 v8 Revision 子 Run 只运行 Revision Editor 和 Reviewer，正常需要两次。
新的 v9 Revision 子 Run 在达到时长下限时仍是两次；仍短且满足追问条件时，
再运行一次 Supplemental Interviewer，因此正常上限为三次。读取已经持久化的
问题 Plan、导入回答 Source 和提交 Revision 请求本身都不调用模型。
将单 Run 预算设为三时，Editor 调用会在
进入 Provider 以前以 `model_call_limit_exceeded` 被拒绝。Editor retry、
timeout、lease、fencing、startup recovery 和 cancel 复用同一 Worker 机制；
每个重试 attempt 单独记账，但 Artifact 通过稳定 idempotency key 只提交一次。
若 v6/v7/v8 父质量 Run 的预算只够四次，Reviewer 会以
`model_call_limit_exceeded` 失败并触发
质量报告降级；已经生成的 Draft 不会因此变成失败产物。

## 11. API 和事件

最小 API：

```text
POST /projects
GET  /projects
GET  /projects/{id}
POST /projects/{id}/sources
POST /projects/{id}/runs
GET  /runs?project_id={id}
GET  /runs/{id}
GET  /runs/{id}/exports/interview-scaffold.md
GET  /runs/{id}/exports/podcast-draft.md
GET  /runs/{id}/exports/show-notes.md
GET  /runs/{id}/quality-report
GET  /runs/{id}/exports/quality-report.md
POST /runs/{id}/quality-feedback
GET  /runs/{id}/quality-feedback
GET  /runs/{id}/improvement-plan
POST /runs/{id}/revisions
GET  /runs/{child_id}/revision-comparison
GET  /runs/{id}/supplemental-interview-plan
POST /runs/{id}/resume
POST /runs/{id}/cancel
GET  /runs/{id}/events
GET  /runs/{id}/events/stream
```

SSE 用于低成本实时显示，但不是状态真相。每个 durable Event 在同一 Run 内有
单调递增的 `sequence`。连接建立后，服务端先返回 `sequence > cursor` 的历史
Event，再以短轮询读取 SQLite 中的新 Event；客户端可以用 `after` query 或
`Last-Event-ID` 恢复，服务端取两者较大值，避免重复倒退。浏览器按 sequence
排序和去重，因此 HTTP replay 与 SSE 同时看到同一 Event 也只展示一次。

空闲且非终态的 Run 每 15 秒发送 SSE 注释 heartbeat，维持代理连接但不伪造
领域 Event。Run 进入 `succeeded`、`failed` 或 `cancelled` 后，服务端在发送完
剩余 durable Event 后正常结束流。UI 还会定时执行 HTTP refresh；断流只改变
连接提示，不会改变 Run 状态，也不会触发模型调用。

### 本地 Console 的职责边界

- `/projects`：创建、列出并重新打开本地 Project；
- `/projects/{id}`：导入/查看 Source、配置 Run、查看不可变 Run 历史；
- `/runs/{id}`：展示 Event Timeline、Task、Artifact、ModelCall、错误与费用；
- 在人工检查点把文字保存为新 Source，再显式 Resume；
- 查看 Scaffold、Draft、Show Notes、质量报告，并显式提交反馈或 Revision。

Console 当前不是部署后的多用户产品：没有登录、权限、协作、可视化 Scaffold
编辑器、音频上传、STT，也不承担数据库备份。Docker 与单机部署仍属于未完成的
M5 切片。

### 输出与审稿 API

Scaffold 导出接受 `waiting_for_user` 或 `succeeded`，并从该 Run 已完成的
`build_interview_scaffold_result` Artifact 读取内容；它不依赖最终
`output_artifact_id`，因此 v4 Editor 成功后仍能导出同一份 Scaffold。
Podcast Draft 与 Show Notes 只接受最终成功且 `output_artifact_id` 指向合法
`build_podcast_draft_result` 或 `revise_podcast_draft_result` 的 Run。未
就绪、类型不符或内容无效时返回 409。

Readiness 首版不增加单独 endpoint；`GET /runs/{id}` 的 Artifact 列表会返回
所有 `material_readiness_report`，按创建时间可以看到初始判断和每轮补充后的
变化。未来 UI 直接消费这一结构，无需解析运行日志。

Draft Quality Report 有单独 JSON 与 Markdown endpoint。它不替换
`output_artifact_id`；Run 成功后，最终 output 仍是
`build_podcast_draft_result`。用户反馈只能提交给已经成功且确实输出 Podcast
Draft 的 Run。反馈采用稳定 `submission_id` 幂等追加，同 ID 不同内容返回
409；`human_signal_eligible` 由服务端依据 `feedback_origin` 计算，调用方
不能自行指定。

M3.5 的 Flash/Pro 比较器是离线实验工具，不是产品 API。它从一个成功 Run
加载唯一 Reviewer Task、精确 Draft 与 metrics Artifact，冻结输入 hash，
按固定顺序各调用一次。默认保留历史 deterministic snapshot；显式
`--recompute-current-rules` 则从同一 Draft 按当前规则重建 metrics/facts。
两种模式都不重新生成 Editor Draft，也不把实验调用写进正式 Run 的
ModelCall 账本；输出只含模型身份、schema 结果、分数、cap、token、耗时与
本地估算费用，不含正文、来源或密钥。

M3.6 的三个新 endpoint 都保持“读取不偷偷执行”的边界：

- Improvement Plan 是确定性、按需持久化的诊断；
- `POST revisions` 才是唯一创建子 Run 和模型工作的动作；
- comparison 只在子 Run 成功后按需持久化摘要，不自动选择或再触发修订。

M3.8 复用同一组 endpoint。读取 Improvement Plan 时可以计算未进入 spoken
units 的事实引用和候选字符总量，但不会创建 Task 或产生模型费用。只有显式
Revision 请求选择 `reuse_unused_material` 时，服务层才把匹配该父稿的
Recovery Plan 写进子 Run Task input；Schema 会重新验证目标时长、父稿实际
字符、引用范围和候选字符总量，防止旧 Plan、越权 Source 或客户端篡改数字。
子 Run 的 Improvement Plan 会记录已经发生过一次时长恢复；如果仍未达标，
`reuse_unused_material` 仍可供人工查看但不再推荐，默认下一步是补材料或降低
时长。读取这个 Plan 不会排队第二个 Revision。

M3.9 的 Supplemental Interview Plan 也保持读取与执行分离。
`GET /runs/{id}/supplemental-interview-plan` 只返回已提交、且仍绑定该 Run
最新 Draft 和 Quality Report 的 Plan；Run 不存在时返回 404，Run 尚未完成、
Plan 尚未就绪或 provenance 已过期时返回 409。重复 GET 不写 Artifact、
不排队 Task，也不增加 ModelCall。真正的新模型工作只能由用户带 Plan、
question IDs 和回答 Source IDs 显式提交下一次 Revision 后发生。

写作样本不是事实 Source 通道。即便被选片段为了可恢复执行而持久化在受限
Editor/Reviewer Task input 中，也不能进入 `allowed_source_refs`、最终引用
或 Plan 的未使用事实列表。

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

M3.5 没有新增持久 Event 类型。Reviewer 路由继续复用已有 Task/ModelCall
事件，并在 `_execution` 中记录实际 provider/model。独立比较器只输出
`quality_reviewer_compare.preflight/completed/blocked` 脱敏摘要，包含输入
hash、版本、调用计数、schema 状态、tokens、耗时和费用，不输出 Draft、
Prompt、模型 assessment 或 Source 文本。

M3.6 新增
`workflow.writing_style_profile.created`、
`workflow.draft_improvement.planned`、
`workflow.draft_revision.requested`、
`workflow.draft_revision.queued` 与
`workflow.draft_revision.compared`。它们只记录 profile readiness、数量、
Run/Task/Artifact ID、动作数量和 comparison 元数据，不记录写作样本、
feedback comment、修订指令或 Draft 正文。幂等 replay 只写脱敏操作日志，
不会重复创建持久 Artifact、Event 或子 Run。

M3.8 不新增事件名。`workflow.draft_revision.requested` 复用现有 Event，并在
有 Recovery Plan 时增加 `length_recovery_readiness`、
`length_recovery_missing_to_minimum` 和
`length_recovery_priority_source_count` 等状态与计数字段；不记录 Source
正文、Prompt 或 Draft 正文。

M3.9 新增持久事件
`workflow.draft_supplemental_interview.queued`、
`workflow.draft_supplemental_interview.completed`、
`workflow.draft_supplemental_interview.unavailable` 与
`workflow.draft_supplemental_interview.limit_reached`。只读 API 另写操作日志
`workflow.draft_supplemental_interview.plan_read`。这些记录只包含 Run、
Task、Draft/Report/Plan Artifact ID、轮次、时长缺口、问题数量和 generation
mode，不包含 Draft 原文、问题全文、回答正文或 Prompt。

## 13. 升级触发条件

只有出现以下证据时才升级：

- 多进程或多机器 Worker：SQLite -> PostgreSQL；
- 任务量要求独立 Broker：引入队列；
- Workflow 数量和分支难以维护：评估 LangGraph；
- 长任务跨部署恢复要求显著提升：评估 Temporal；
- 语义检索成为质量瓶颈：评估 embeddings/vector index；
- 执行不受信任的用户代码：引入 sandbox/container。
