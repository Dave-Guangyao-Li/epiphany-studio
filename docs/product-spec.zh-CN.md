# Epiphany Studio 产品规格（MVP）

状态：Draft

日期：2026-07-30

## 1. 产品命题

Epiphany Studio 是一个面向个人创作者的 AI 素材与表达工作台。

它帮助用户把散落的日记、随想、旧文章、录音和转录内容，逐步转化为：

- 有来源依据的人生素材库；
- 能引导继续口述的半开放采访脚手架；
- 保留本人语言和经历的播客稿或文章；
- 经用户确认后才长期保存的个人记忆。

第一位用户是项目作者本人。第一类内容是 `Epiphany Moments` 播客及
“成年十年”系列。

## 2. 要解决的问题

用户拥有很多真实素材，但创作过程存在四个阻力：

1. 素材分散，跨越不同时间、格式和主题。
2. 直接面对空白页面时很难完整回忆和组织表达。
3. 通用模型容易生成漂亮但空泛、缺少个人证据的总结。
4. 长对话中的有效素材、来源和决策容易丢失。

产品不试图替用户决定“你是谁”。它提供一个可追溯的思维脚手架，
帮助用户重新看见自己的材料并继续表达。

## 3. 核心产品原则

### 3.1 Source-grounded

事实、原话、事件和记忆候选必须包含 `source_segment_id`。用户可以从
生成结果回到原文或录音时间戳。

MVP 文字来源契约：

- `Source` 保存本地规范化全文、内容 hash、类型和非敏感 metadata；
- `SourceSegment` 保存稳定 ID、顺序、原文、字符起止位置和片段 hash；
- `SourceReference` 只包含 `source_id` 和 `source_segment_id`，不允许
  Agent 把无来源文本混入引用对象；
- 换行统一为 `\n`，Unicode 统一为 NFC，去掉全文首尾空白；
- 优先按段落切分，超长段落在标点或空格边界继续切分；
- 完全相同的规范化正文视为同一 Source，重复或并发导入不得产生副本；
- 原始全文和片段只存本地数据库，不写入运行日志。

### 3.2 Human-led

系统生成候选素材、问题和草稿；用户负责确认、修正、补充和最终采用。
M3 中的“补充口述”特指用户已经转成文字后导入的 Source。浏览器不申请
麦克风权限，后端不接收音频，也不执行实时语音转文字。

### 3.3 Semi-scripted

系统不是只给空白问题，也不是直接替用户写完。它生成：

- 已知背景；
- 可直接说的过渡；
- 关键词；
- 有针对性的追问；
- 留给临场表达的空间。

### 3.4 Local-first for MVP

数据库、原始素材和产物默认保存在本机。上传到模型服务的内容应最小化，
并明确记录调用范围。真实 Provider 必须显式开启，默认仍使用 Fake；首版还
限制单次发送的素材字符数和输出 Token。

### 3.5 Observable agents

用户能看到主任务、子任务、状态、来源和产物，但无需直接与每个
Subagent 对话。

## 4. MVP 用户旅程

1. 用户创建一个 Episode Project。
2. 导入 Markdown 或纯文本素材。
3. 系统切分内容并保留来源信息。
4. 用户选择主题、目标时长、听众和语气；还可主动选择本人旧文章或口述转录
   作为仅用于表达风格的写作样本，然后启动 `EpisodeRun`。
5. 两个只读 Subagent 并行工作：
   - Timeline Researcher：事件、阶段、时间线；
   - Theme Researcher：主题、认知变化、原话和细节。
6. Manager 确定性合并研究结果，随后由一个串行根 Interviewer 生成素材缺口
   和采访脚手架。
7. 系统用确定性规则估算现有素材能否支撑目标时长，Workflow 进入
   `waiting_for_user`。
8. 用户输入新的口述转录或手动补充；明显不足时可以继续多轮补充。
9. 素材达到门槛后，Editor 按同一份 Creative Brief 生成口播稿和
   Show Notes。
10. 系统生成可解释的 Draft Quality Report，区分确定性指标与模型建议。
11. 用户审核、提交独立质量反馈，并查看系统根据现有证据生成的改进计划。
12. 用户可以直接导出 Markdown，也可以明确选择改进动作，创建一个不覆盖
    旧稿的 Revision 子 Run，再人工比较两个候选。

## 5. MVP 范围

### 包含

- 本地单用户；
- Markdown/TXT 导入；
- 来源切分与引用；
- 固定的一层父子 Agent 编排；
- 两个并行只读 Subagent；
- 结构化输出；
- 等待用户与恢复；
- 运行事件和错误展示；
- Markdown 导出；
- Draft Quality Report 与独立用户反馈；
- 经明确授权、与事实素材分离的可选写作样本；
- 确定性 Improvement Plan 与用户显式触发的 Revision 子 Run；
- 可替换的托管模型 Provider（首个为 DeepSeek）与测试用 Fake Provider。

### 暂不包含

- 多用户账户与协作；
- 手机 App；
- 自动发布播客；
- 动态无限派生 Agent；
- 向量数据库；
- 云端文件存储；
- 自动长期记忆写入；
- 音频转录；
- 语音克隆与 TTS；
- 支付和商业化。

音频、语音和部署属于后续里程碑。先证明文字素材到创作产物的闭环。

## 6. Agent 角色

### Manager

- 用户唯一交互入口；
- 创建受限子任务；
- 合并结构化结果；
- 不得绕过用户确认发布或改写长期记忆。

### Timeline Researcher

- 只读允许的 Source Segments；
- 输出 `TimelineEvent[]`；
- 每条结果包含证据引用和置信度。

### Theme Researcher

- 只读允许的 Source Segments；
- 输出 `ThemeFinding[]` 和 `QuoteCandidate[]`；
- 区分用户原话与模型推断。

### M2.2 结构化输出契约

- Timeline 的每个事件候选至少有一条 `SourceReference`、置信度、标签和
  描述；
- Theme 的每个主题候选至少有一条 `SourceReference`，原话候选包含一条
  精确引用；
- 输出对象和嵌套引用都采用 strict schema，不允许额外字段；
- 引用的 `(source_id, source_segment_id)` 必须属于该 Child Task 获准读取
  的片段；
- Quote 文本必须逐字存在于引用片段，不能把模型改写伪装成用户原话；
- 任一 Child 输出无效时，Manager 失败、仍在运行的同级 Child 被取消，
  lease 被清除，迟到结果不能写入 Artifact。

M2.2 使用确定性 Fake Provider 验证以上契约，不评价研究内容质量。M2.3b
让 DeepSeek 格式的 HTTP 返回继续经过同一套校验；真实模型内容质量从小额
live smoke 开始验证。

### Interviewer

MVP 中作为确定性 Workflow 排队的一次模型调用，而不是独立并发 Agent。
它根据已合并的证据和素材缺口生成脚手架。两个 Researcher fan-in 完成后，
系统才创建 `parent_task_id=None` 的 Interviewer 根 Task；它不会成为第三个
Child，也不会与研究调用并发。

### M2.4 采访脚手架契约与导出

- M2.4 新建的 `episode-research` Run 使用 workflow v2；一次成功运行固定包含四个
  Task、四个 Artifact 和三次 ModelCall；
- v1 在途 Run 保持 M2.2 语义，在确定性 fan-in 后以研究 Bundle 成功结束，
  无需 `topic` 或 Interviewer；
- Researcher 输入中的主题和素材、Interviewer 输入中的研究文字都被视为
  不可信数据；主题只用于筛选相关证据；
- Interviewer 输入只包含已校验的 Timeline/Theme 研究结果、主题、研究
  Bundle ID，以及由这些结果收集的允许引用；
- Prompt 要求模型只能复制
  `allowed_source_refs` 中的引用；
- Prompt 要求保留事实状态：计划、草稿、愿望、准备和尝试不得改写成已完成
  或已发布。该规则降低语义夸大的概率，但合法引用不等于自动证明陈述被原文
  蕴含，发布前仍需要人工确认；
- 输出采用禁止额外字段的 strict schema；标题必须逐字匹配主题，episode
  intent、开场、收束、章节、已知背景、过渡、问题和素材缺口都必须
  source-grounded；
- 任一引用超出研究 Bundle、结构不完整或没有对应 validator 时，Worker
  在持久化采访脚手架前拒绝输出；
- 若 Interviewer 失败，已经成功的两个研究 Artifact 和确定性研究 Bundle
  继续保留，失败的 Run 不会伪装成可导出的成功结果；
- 单 Run 调用预算设为二时，第三次 Interviewer 调用在进入 Provider 前以
  `model_call_limit_exceeded` 被拒绝，不产生第三条 ModelCall；
- `GET /runs/{run_id}/exports/interview-scaffold.md` 导出已经就绪的采访
  脚手架。M2.4 v2 成功态以及 M3.1 v3 / M3.2 v4 等待态都允许导出；
  Markdown 由结构化
  Artifact 确定性生成，正文使用 `[S1]` 等短引用，文末按 Source 标题和
  Segment 位置列出来源索引；原始 Source/Segment ID 仍保留在 Artifact 与
  数据库。引用元数据无法解析时返回 409。模型文本中的 Markdown 控制字符和
  原始 HTML 会被转义，运行 metadata 不进入正文。

M2.4 复用现有 Run、Task、Artifact、Event 和 ModelCall 表，不需要数据库
migration。结构化运行日志只记录关联 ID、状态和 section、question、引用、
字符等计数，不记录素材、Prompt、模型响应或导出正文。

### M3.1 人工检查点与 Resume 契约

- 新建 `episode-research` Run 使用 workflow v3；v1 和 v2 在途 Run 保持各自
  原来的完成边界；
- Interviewer 成功后，v3 Run 保存脚手架并进入
  `waiting_for_user / awaiting_interview_response`；
- 等待时四个 Task 都已终结、四个 Artifact 和三次 ModelCall 已持久化，
  Worker 没有可领取任务，也不会后台继续花费模型费用；
- 用户通过 `POST /sources` 导入补充文字，建议
  `source_type="voice_note_transcript"`，再调用
  `POST /runs/{run_id}/resume`，只提交 checkpoint、稳定 submission ID 和
  Source ID 列表；
- Resume 创建一份 `user_material_submission` Artifact。它只引用采访脚手架、
  Source 与 SourceSegment，不复制补充正文，不改写 Run 的原始 input；
- 第一次合法 Resume 返回 `resumed=true`；完全相同的网络重试返回
  `idempotent_replay=true`，不新增 Artifact 或 Event；相同 submission ID
  对应不同 Source 返回 409；
- Source 不存在返回 404，Run 继续等待；无效 body 返回 422；非等待态、
  已取消或已失败的 Run 返回 409；
- Resume 与 Cancel 在当前单进程服务内共用 mutation lock，只有一个终态能
  成功；多进程协调不在 M3.1 支持范围；
- M3.1 的 Resume 只验证人工检查点闭环，在同一事务内确定性完成，不新增
  Task、Provider 调用、Token 或费用，`output_artifact_id` 仍指向脚手架；
- 这是 v3 的历史 Resume 语义；新建的 v4 Run 会由 M3.2 Editor 读取新增
  Source，生成口播稿与 Show Notes。

M3.1 同样复用现有表，无 migration。操作日志和 Events 不保存补充口述
正文、Prompt、模型输出、密钥或语音内容。

### Editor

M3.2 把 Editor 实现为 Resume 后排队的串行根 Task，而不是一个与
Researchers 并发的 Child：

- 新建 `episode-research` Run 使用 workflow v4；v1 / v2 / v3 在途 Run
  保持各自历史完成语义；
- v4 到人工等待点时仍为四个成功 Task、四个 Artifact 和三次 ModelCall；
- 第一次合法 Resume 创建 `user_material_submission`，把 Run 恢复为
  `running`，并排队一个 `parent_task_id=None` 的
  `build_podcast_draft` Editor Task；
- Editor 输入只包含已验证 Scaffold、Scaffold 实际引用到的初始
  SourceSegment、用户本轮补充的 SourceSegment，以及相关 Artifact ID；
- Scaffold、topic 与 Source 文本都作为不可信数据；输入 JSON 有单独的字符
  上限，Editor 有独立输出 Token 上限；
- 输出采用禁止额外字段的 strict schema，包含 Podcast Script 与 Show Notes；
- title 必须逐字等于 topic，每个口播段落、section、Show Notes summary 和
  key point 都必须包含允许范围内的 SourceReference；
- Podcast Script 必须同时使用至少一条初始引用和一条补充引用，Show Notes
  也必须使用补充引用，避免模型在格式正确的情况下忽略用户的新回答；
- 引用越权、缺少补充引用、结构不完整或 topic 漂移时，Worker 在 Artifact
  提交前拒绝结果；
- 成功 Run 最终为五个 Task、六个 Artifact 和四次 ModelCall，
  `output_artifact_id` 指向 `build_podcast_draft_result`；
- `GET /runs/{run_id}/exports/podcast-draft.md` 与
  `GET /runs/{run_id}/exports/show-notes.md` 从结构化结果确定性渲染安全
  Markdown。内部 Source/Segment ID 转为 `[S1]` 短引用和来源索引；
- 原 Interview Scaffold Artifact 保留，原导出 endpoint 在 Editor 成功后
  仍可读取同一份 Scaffold；
- Editor 是候选内容生产者，不自动发布。合法引用只证明可追踪，不构成语义
  蕴含证明，最终事实和表达仍由用户审核。

Resume 重放不能重复排队 Editor 或产生额外费用。Editor 共享既有 Worker 的
lease、fencing、retry、timeout、cancel、startup recovery 和模型调用预算；
预算不足时第四次调用会在进入 Provider 前失败。M3.2 复用现有表，不需要
migration。操作日志和 Events 仍不得保存素材正文、Prompt、模型完整输出或
API Key。

### Creative Brief 与素材充足度

M3.3 为选择提供 Creative Brief 的新 Run 使用 workflow v5；没有 Brief 的
请求仍创建 v4，以保留既有 API 与持久 Run 语义。

- Brief 严格保存 `target_duration_minutes=10|15|30`、可调口播字符速度、
  场景、目标听众、沟通目标、最多三个语气词、必须涵盖内容和避免模式；
- 默认以每分钟 280 个非空白中文字符和上下 15% 作为可解释的首版估算，
  这不是实际录音测速；
- Interviewer 完成后，确定性代码只读取 Scaffold 实际引用的初始
  SourceSegment，写入一份不复制原文的 `material_readiness_report`；
- `ready` 要求存在初始与补充素材、至少两个独立 Source，且去重后的素材
  字符达到目标区间下限；
- 去重同时检查稳定 Segment 引用和去除空白后的正文内容；复制同一段文字到
  新 Source 不能增加字符量或伪造来源多样性；
- 未达到门槛时，Run 持久停在
  `waiting_for_user / awaiting_more_material`，不创建 Editor Task，也不
  产生那次 ModelCall 或费用；
- `checkpoint=material_readiness` 的每次合法 Resume 都把补充 Source 引用
  幂等保存。仍不足时再次等待；达到门槛后，把所有已接受轮次一起交给唯一
  Editor；
- 初始 Source 或历史补充 Source 不能换一个 submission ID 重复计数；累计
  补充材料最多 500 个 Segment，超限请求整批拒绝且不留下部分 Artifact/Event；
- Editor Prompt 使用同一 Brief 约束目标时长、听众和语气，但来源事实仍有
  更高优先级。素材不足时宁可短，不得通过重复、空话或虚构凑长度。默认
  Editor 输出 ceiling 为 20,000 tokens，以容纳 30 分钟目标；实际费用仍按
  返回 Token 计算，模型是否达到目标由 M3.4 的 Draft Quality Report 检查。

Readiness 只判断“是否明显短缺”，不评价叙事、具体性、自然口语或个人声音。
这些属于 M3.4 的 Draft Quality Report。模型自评必须标记为 advisory，
synthetic fixture 或 Mock 用户反馈不能冒充真实用户认可。

M3.3 继续复用 Run input 与 Artifact，不新增数据库表。自动合成 E2E 会关闭
并重启 App，证明等待状态不会丢失或偷偷继续；合成材料只用于工程验收。

### Draft Quality Report 与用户反馈

M3.4 为带 Creative Brief 且默认启用 Draft Quality 的新 Run 使用 workflow
v6。调用方可以显式提交 `draft_quality.enabled=false` 保持 v5 的较低费用
路径；历史 v1–v5 Run 不会被升级后偷偷改变。

Editor 成功后先由普通代码生成 `draft_metrics_report`：

- 只统计真正会被说出的 opening、section paragraph 和 closing 正文，再按
  Creative Brief 保存的字符速度估算文字可支持的分钟数；标题、章节标题、
  SourceReference、`[S1]`、来源索引、Show Notes 和渲染后的 Markdown 均不
  进入口播字数；
- 检查每个口播段落是否带来源引用，并报告 Source/Segment 多样性；
- 检查规范化后完全重复的段落和重复八字符窗口；
- 观察 must-include 字面文本、avoid pattern、固定 filler、模板短语和重复
  句式；must-include 的字面 miss 只记为 `info`，不能证明同义表达没有覆盖，
  语义判断交给 Reviewer；
- 所有 finding 都包含明确 code、status、location、observed 和 threshold。

10 / 15 / 30 分钟是文字阶段估算，不是实际录音时长。系统默认按每分钟 280
个非空白字符计算；录制后的真实分钟数只能由用户另行反馈。时长严重不足时，
产品建议继续补充具体事件、动作、对话和感受，而不是要求 Editor 用同义反复
或空泛表达凑满目标。

随后排队一个串行根 Quality Reviewer Task。模型只能按固定六维返回建议：
Creative Brief 匹配、来源忠实、覆盖与具体性、结构连贯、口播自然与声音匹配、
精炼与非重复。每个 `assessable=true` 的维度必须包含 1–5 分、Draft
location 和逐字 exact quote；代码验证 quote 确实存在于该位置，并验证引用
没有越过 Draft 实际引用的 SourceSegment。无法可靠判断的维度必须使用
`assessable=false` 和 limitation，不得编造证据。

模型不能返回最终 decision。应用代码按以下边界合成报告：

- 确定性 blocker -> `blocked`；
- Reviewer 不可用或评价不完整 ->
  `automated_review_incomplete`；
- 确定性 warning 或低分维度 -> `revision_recommended`；
- 其余情况 -> `candidate_ready_for_human_review`。

实验性综合分使用有版本号的 60% 确定性指标 + 40% 六维模型平均分，只用于
同一 profile 下比较候选稿。任何维度不可评价时不生成综合分。报告始终要求
人工审核。

首版默认可能由与 Editor 相同的模型担任 Reviewer，因此必须明确标记
`same_model`、self-review 和 advisory。产品不输出“AI 生成概率”。可观察的
重复、模板、filler 等信号可以报告，但不能据此断言作者身份。合法引用也只
证明可追踪，不等于已经证明语义完全正确。

Reviewer 是辅助能力。它在 retry 后仍失败、鉴权不可用或预算只够前四次调用
时，系统保留 Draft、确定性指标和失败原因并让 Run 正常完成。若确定性规则
已有 blocker，decision 仍为 `blocked`；否则使用
`automated_review_incomplete`。Run 的最终 output 始终仍是 Editor Draft。

用户评价通过独立 append-only `draft_user_feedback` Artifact 保存，包含
overall、voice match、recordability、usefulness、tone fit、是否愿意直接录、
可选实际时长和评论。`feedback_origin=human` 才会被标为
`human_signal_eligible=true`；自动化 E2E 必须使用 `synthetic_test`，服务端
强制将其标记为不具备真人信号资格。当前本地 MVP 没有身份认证，所以 origin
仍是调用方自报的分类，不是“真人身份已验证”。反馈评论不复制到 Event 或
运行日志，但仍保存在 Artifact 中并可由本地数据接口读取。

M3.4 复用 Run、Task、Artifact、Event 与 ModelCall 表，不新增数据库
migration。确定性指标、模型原始六维结果、代码合成报告和用户反馈分别保存，
避免把工程验收、模型自评和真实用户认可混成一个分数。

### M3.5 中文质量校准、可信事实与非补偿上限

M3.5 将新质量 Run 升级为 workflow v7，但不改变 Task 数量、公开 API 或
数据库 schema。升级是必要的，因为持久化 Reviewer Task 输入、Prompt、
确定性规则和报告语义都发生了变化。已经停在 checkpoint、排队或租约恢复中的
v6 Run 继续按 M3.4 的旧规则、旧 Prompt 与 v1 报告合同执行，不会被偷偷改成
v7。若预发布数据表现为 `workflow v6`、但持久 Reviewer Task 已经携带
current deterministic facts，则恢复时以 Task 合同为准继续 v2 报告语义，
避免丢失 score cap 与 conflict。新版本解决“模型给高分，却把明显时长缺口
平均掉”的问题。

Reviewer Task 新增一份由应用代码生成的
`deterministic_quality_facts_v1`。它只包含当前持久 Draft 的目标分钟数、
口播正文字符数、估算分钟数、时长覆盖率/状态、引用覆盖、blocker/warning
数量和版本化中文风格计数。Task 输入 validator 会从同一份结构化 Draft
重新计算字数、时长与引用覆盖并逐项核对；事实不一致时，在调用模型前拒绝。
因此 Reviewer 可以解释事实的编辑意义，却不能自己重新数一遍后否认或覆盖。

综合分仍保留原始六维模型均分和 60/40 的未封顶加权值，随后由代码应用最
严格的**非补偿上限**：

- 任一确定性 blocker：最终实验分最多 39；
- 估算时长不足目标的 60%：最多 59；
- 任一确定性 warning：最多 79。

上限不把原始模型卡改写掉。报告同时保存 raw model score、uncapped score、
cap、cap reason、capped score 和显式冲突，便于看清“模型意见”和“代码事实”
为何不同。Decision 仍按 blocker、review 不可用、warning/低维度等规则由
应用代码计算，始终需要用户终审。

报告在任何反序列化入口还必须重新验证跨字段一致性：六维卡片与模型换算分、
确定性分与未封顶 60/40 分、findings 与 cap/reasons、未封顶分与封顶后分数，
以及 evidence/status 与 decision 必须能够互相推出。像“cap 为 39，但最终分
为 80”这样的 JSON 即使字段类型全部合法，也必须被 API、导出和 E2E 拒绝。
历史 v1 Artifact 继续按旧合同读取。

v6 的确定性规则冻结为 `draft_quality_rules_v1`；v7 使用
`draft_quality_rules_v2_chinese_calibration`。中文启发式规则使用版本
`zh_podcast_style_v1`，观察成组反差、层层递进、
枚举、通用转场/顿悟/收束和过度礼貌，以及句长/段长变异。这些只是可重复的
**表达风险信号**，不是作者身份判断，也不产生“AI 写作概率”。规则使用保守
重复阈值；一次自然出现不应直接扣分。为兼容历史报告，
`style.template_phrases` 与 `style.not_but_pattern` 仍可显示，但与新分类
重叠时仅为 `info`，由版本化中文分类统一承担一次分数影响。

Reviewer 可以独立路由。未设置 `EPIPHANY_DEEPSEEK_REVIEWER_MODEL` 时复用
Editor 模型；设为受支持的 Flash 或 Pro 时，只把
`review_podcast_draft` Task 送到该模型。ModelCall 和 Artifact
`_execution` 记录真正的 provider/model，报告用 `same_model`、
`cross_tier_same_family` 或 `different_model` 说明关系。跨 tier 仍只是同一
模型家族的参考评价，不等于独立人工审核。

冻结稿比较工具对同一份 Draft、Prompt 和 strict schema 依次调用 Flash 与
Pro，不重新运行 Editor。`--recompute-current-rules` 先用当前代码重建
deterministic result/facts，再让两个 Reviewer 看见相同输入，避免把历史规则
差异错当成模型差异。工具输出经过脱敏，只比较分数、decision、schema
成功与否、tokens、耗时和本地估算费用；它不把实验调用伪装成正式 Run 的
ModelCall 账本。

当前合成案例的工程事实是：

- 正式 v7 Fake 全流程 Run `run_f41eac8520cd4b47b97cc1181acb3d63`
  全部检查通过；
- DeepSeek Run `run_0af27a7596474a92ba79e298e912e35e` 是 M3.5
  预发布 workflow-v6 开发快照，其 workflow 成功，
  口播正文为 2,055 字符，估算 7.34 / 15 分钟，五次调用本地估算
  CNY 0.089433；
- 真实 Run 初次报告中的两项失败来自旧 E2E harness：它错误假设 Editor 与
  Reviewer 使用同一模型，并只接受旧报告关系值；修复后的离线重验两项均为
  true，未再次付费生成 Draft；
- 当前规则重算为 deterministic 62、1 blocker、1 warning、2 info，最严格
  cap 仍为 39；
- 历史快照的同稿比较为 Flash 76.67、Pro 80，封顶后都为 39；
- 当前规则比较中 Flash 未通过 strict schema，Pro raw 70、封顶 39；两次
  调用本地估算分别为 CNY 0.013950 与 CNY 0.044008。

这只是一个合成样本，不能证明 Pro 普遍更好，也不能把一次 schema 失败推断
为 Flash 普遍不可用。产品选择模型时还要累计多主题、多长度和真实用户反馈。

### M3.6 写作样本、改进计划与显式 Revision

M3.6 为新质量 Run 使用 workflow v8，并把“更像用户”和“根据反馈改稿”拆成
三个有边界的产品能力。

第一，用户可以在创建 Run 时可选提交 `writing_style_reference`。它最多引用
五个用户拥有的现有 Source，并要求用户明确确认内容归属和模型处理授权。
`writing_sample` 可以作为导入或 UI 分类标签，但不是能否使用的依据；每个
Run 中显式的 style-only 选择才是权威合同。被选样本不能同时出现在该 Run 的
事实 `source_ids` 中。系统只持久化有界
`writing_style_profile`：被选片段的引用、hash、字符/句子/段落统计和
readiness，不在 profile、Event 或日志中复制全文。样本正文仍保存在原
Source；被选片段会进入可恢复的 Editor/Reviewer Task input，因此本地数据库
仍属于敏感数据。

写作样本的优先级低于安全、来源事实、本轮明确修订要求和 Creative Brief；
高于系统默认写法。它只能帮助参考句长、节奏、直接程度、转折和口语感，
不能提供本期事实、可执行指令或 `source_refs`。样本至少达到 800 个非空白
字符和五句话才是 `ready`；否则只是 `limited` 弱提示。只有 `ready` 时，
Reviewer 才增加第七维 `personal_style_match`，而且证据必须同时指向 Draft
和样本。没有足够样本时仍维持六维，系统不能声称文稿“像本人”。最终
`voice_match_rating` 仍由用户提供。

第二，成功质量 Run 可以读取
`GET /runs/{run_id}/improvement-plan`。普通代码从 Draft、质量报告、采访
脚手架和 Editor 已有事实材料生成一个可追踪计划，说明：

- 目标时长还差多少正文；
- 是否有尚未引用、可以先复用的事实片段；
- 何时需要补充材料，并给出基于脚手架的具体追问；
- 用户是否可以选择更低的 10/15 分钟预设；
- 哪些确定性 finding 或模型低分维度需要处理。

读取计划不调用模型，也不会创建新 Run。

第三，只有用户显式提交 `POST /runs/{run_id}/revisions` 才创建一次
`podcast-revision` 子 Run。请求使用稳定 `submission_id`，并明确列出复用
材料、补充 Source、降低目标时长和应用反馈等动作；同一 ID 同一请求安全
重放，同一 ID 改变选择返回冲突。子 Run 保存 `parent_run_id`，使用独立调用
预算，先运行一个受限 `revise_podcast_draft`，再重新执行确定性 metrics、
Reviewer 与非补偿 cap。父 Draft、Report、Feedback、output 和调用账本都
不会被改写。

子 Run 成功后可读取
`GET /runs/{child_run_id}/revision-comparison`。系统会懒加载并持久化一份
不含正文的父/子摘要及字符、时长和分数变化，但
`automatic_winner_selected=false`，最终采用哪一稿仍由用户决定。自动化
Fake workflow 已覆盖上述链路；M3.6 的真实 DeepSeek E2E 尚未执行。

### M3.7 写作样本效果验证

写作样本已经接入并不等于它真的让稿子更像用户。验证时必须冻结同一份
topic、Creative Brief、采访脚手架、初始素材与补充口述，只改变 Editor
是否收到 ready 写作样本。重新运行 Researcher 或 Interviewer 会引入额外
差异，不能算受控比较。

M3.7a 先提供只读预检：从已完成的 v8 Run 读取原始 Editor 输入，构造
`without_sample` 与 `with_sample` 两臂，并对删除风格字段后的公共输入计算
同一个 hash。更宽的实验合同还冻结质量配置、模型档位、temperature、
token/bundle 上限和 Reviewer 共用的 style context；Sample 必须真的改变
Editor Prompt，否则预检阻断。命令默认不联网、不写实验产物，通过 SQLite
只读模式保证不修改 Run 业务记录，不调用模型，也不输出素材、Sample、Prompt
或 Key。SQLite 自身仍可能维护 WAL/SHM 连接辅助文件。

后续执行阶段固定使用同一个 Flash Editor 生成两稿，再让同一个 Pro Reviewer
在同一份 ready Sample 下评价两稿。即使 Control 生成时没有见过 Sample，
Reviewer 也必须看见 Sample，否则两边的 `personal_style_match` 不可比。
真实结论以用户不知道分组时提交的 `voice_match_rating` 和 forced choice
为主；模型评分只是辅助证据。单个 pair 只能形成方向性案例，不能代表普遍
效果。

M3.7b/c 将这条验证实现为一次本地、受限实验，而不是新的生产工作流。Editor
与 Reviewer 的 arm 顺序默认随机化；最多四次调用且不自动 retry。实验目录
独占创建，每次请求前后原子更新私有 manifest。进程崩溃后若最后一条调用仍为
`started`，表示是否计费未知，必须先核对厂商 Dashboard，不能自动重跑。

两稿随后随机映射为 Candidate A/B。公开候选只含经过转义的口播正文，不含
treatment、Reviewer 分数或内部 Source ID。私有映射使用 salt 和 commitment
与候选 hash 绑定；候选被修改、映射被修改或评分不匹配时均不能揭盲。用户必须
先提交两稿的声音匹配、可录性和 forced choice，之后才能看到 treatment 与模型
辅助结果。系统不自动选择 winner。

系统还必须先判断两稿是否真的形成了可比较的 treatment difference。blind v2
只比较 opening、section paragraphs 与 closing：如果对齐口播单元逐字重合率
达到 70%，或规范化字符相似度达到 90%，结果标记为
`inconclusive_low_distinctness`，真人二选一只能作为 `directional_only`
反馈，不能被解释成写作样本有效。

M3.7 实验产物不写回原 Run，不进入生产 `model_calls` 表，也不新增 API、数据库
表或 workflow 版本。它们只保存在 `.gitignore` 覆盖的本地私有目录。完成一个
首个真实单 pair 的揭盲结果为用户低置信度偏好有 Sample 的 A，但 10 个口播
单元中 9 个逐字相同，字符相似度为 0.9638，因此结论是不确定而非 Sample
获胜。这也完成了 M3 的实验退出条件。M3 停止扩展，转入可靠性 Trace 与最小
Web UI。

## 7. 成功标准

完成 MVP 时，应能演示：

1. 从至少 5 份真实素材生成一期采访脚手架。
2. 每个事实性结论均可返回来源片段。
3. 两个子任务可以并行，且有独立状态和结果。
4. 进程中断后，重启能够恢复未完成 Run。
5. 取消父 Run 后，不再接受子任务的迟到写入。
6. 一次完整运行不超过配置的模型调用上限。
7. 用户认为生成的脚手架比空白提问更容易触发表达。
8. 用户补充材料后，系统能生成同时引用初始与补充证据的口播候选稿，并
   单独导出 Show Notes，最终采用仍由用户决定。
9. 用户能看到有证据的质量建议，并能单独记录“是否像我、是否可录”的真人
   评价；合成反馈不能被计为真实用户信号。
10. 用户选择写作样本时，样本不能成为本期事实引用；没有足够样本时，系统不
    冒充已经判断“像本人”。
11. 用户能够从质量证据创建一个可追溯的 Revision 子 Run，比较新旧候选，
    同时确认父 Draft/Report 没有被覆盖。

## 8. 衡量指标

早期不追求 DAU。记录：

- 一期内容从素材到可录初稿所需时间；
- 被用户采用的素材卡片比例；
- 没有来源或来源错误的结论数量；
- 每 Run 模型调用数、tokens、延迟和估算费用；
- 失败恢复次数；
- 用户对脚手架“是否帮助想起新内容”的主观评分。
- Draft 目标时长偏差、引用覆盖、重复和模板模式；
- 原始模型分、未封顶分、代码 cap、封顶后分数和 Reviewer schema 成功率；
- 用户对 voice match、recordability、usefulness 与 tone fit 的独立评分；
- 写作样本 readiness、是否启用第七维，以及样本泄漏/错误引用数量；
- Revision 的字符、时长、确定性分数与实验分数变化，以及用户最终选择；
- `synthetic_test` 与 human feedback 必须分开统计。
