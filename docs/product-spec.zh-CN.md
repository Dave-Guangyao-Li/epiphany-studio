# Epiphany Studio 产品规格（MVP）

状态：Draft

日期：2026-07-29

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
4. 用户选择主题、目标时长、听众和语气，并启动 `EpisodeRun`。
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
11. 用户审核、提交独立质量反馈并导出 Markdown。

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

- 按 Creative Brief 保存的字符速度估算实际文字可支持的分钟数；
- 检查每个口播段落是否带来源引用，并报告 Source/Segment 多样性；
- 检查规范化后完全重复的段落和重复八字符窗口；
- 检查 must-include、avoid pattern、固定 filler、模板短语和重复句式；
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

## 8. 衡量指标

早期不追求 DAU。记录：

- 一期内容从素材到可录初稿所需时间；
- 被用户采用的素材卡片比例；
- 没有来源或来源错误的结论数量；
- 每 Run 模型调用数、tokens、延迟和估算费用；
- 失败恢复次数；
- 用户对脚手架“是否帮助想起新内容”的主观评分。
- Draft 目标时长偏差、引用覆盖、重复和模板模式；
- 用户对 voice match、recordability、usefulness 与 tone fit 的独立评分；
- `synthetic_test` 与 human feedback 必须分开统计。
