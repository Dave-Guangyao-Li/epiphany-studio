# MVP 路线图

更新时间：2026-07-30

路线图按可演示的纵向切片推进，不按“先把所有基础设施搭完”推进。

## 迭代规则

每个小步必须同时包含：

- 一个清楚、可运行的行为；
- 对应 migration 与自动化测试；
- 正常与失败路径的结构化日志和关联 ID；
- 必要的手工演示；
- 同一次 commit 内更新 README、路线图、开发日志以及受影响的 Spec/ADR；
- 测试通过后立即 commit，再开始下一步。

优先完成既定 MVP，不在 Milestone 之间插入未规划的平台化、框架化或界面
支线。开发期先通过 API 和自动化测试验证；面向用户的最小界面在 M5
集中完成。

## M0：项目基线

- [x] 建立独立仓库
- [x] 产品 Spec
- [x] 轻量架构
- [x] ADR-0001
- [x] 创建 GitHub 远端
- [x] 建立分章节学习实践手册
- [ ] 建立 Issues / Milestones

完成标准：任何新会话都能从仓库文档理解产品目标、边界和第一步。

## M1：持久化 Run

- [x] FastAPI 项目骨架
- [x] SQLite migration
- [x] `runs`、`tasks`、`events`、`artifacts`
- [x] Run/Task 状态机
- [x] Fake Provider
- [x] Worker 启动、领取、完成任务
- [x] 状态转换、重试、取消、恢复测试
- [x] migration、Ruff 与完整测试套件通过

演示：创建 Run，Worker 完成三步 Fake Workflow，重启后仍能查询完整事件。

## M2：第一个真实 Agent Workflow

### M2.1：来源契约

- [x] `Source` / `SourceSegment` migration
- [x] 纯文字素材导入 API
- [x] 稳定分段与顺序
- [x] 来源引用数据结构与导入测试

演示：导入一段播客素材，重启后仍能按顺序查询原文分段。

### M2.2：Fake 双 Agent 编排

- [x] Timeline Researcher 严格输出 schema
- [x] Theme Researcher 严格输出 schema
- [x] 两个同级 Child Task 并行 fan-out
- [x] 等待全部 Child 完成后 fan-in
- [x] Fake Provider fixtures
- [x] 来源引用校验与失败测试

演示：不调用模型，从已导入素材生成两份带合法引用的结构化候选 Artifact。

验收：并发探针确认两个 Child Provider 调用发生重叠；HTTP 集成测试从
Source 导入走到三份 Artifact；非法引用故障注入会失败父任务、取消兄弟
任务并拒绝迟到写入。M2.2 不改变数据库结构，因此没有新增 migration；
`alembic check` 必须继续无差异。

### M2.3：真实模型 Provider

#### M2.3a：零费用调用基础设施

- [x] 每次 Provider attempt 的持久化 `ModelCall`
- [x] provider、model、tokens、延迟、估算费用与币种记录
- [x] 真正调用 Provider 前执行单 Run 调用上限
- [x] retry 与 timeout 分别记账
- [x] Fake/Mock 正常、失败、超时与超限测试

演示：继续使用 Fake Provider 完成双 Researcher，在 Run JSON 和 Events 中
查看两条零 Token、零费用的调用记录；将上限设为 1 后，第二次调用会在进入
Provider 以前失败。

#### M2.3b：首个真实托管模型

- [x] DeepSeek OpenAI-compatible Provider
- [x] Timeline Researcher prompt
- [x] Theme Researcher prompt
- [x] HTTP/auth/rate-limit/overload 错误映射
- [x] 受限 live smoke 命令与零联网安全测试
- [x] 使用合成素材完成小额 live smoke test
- [x] 显式选择 USD/CNY 结算币种，默认 USD 保持兼容
- [x] live summary 按币种分组，历史记录不换算或覆盖
- [x] 对 OpenAI-compatible Mock 返回继续执行结构化输出与引用校验

离线验收：默认仍为 Fake；DeepSeek Provider 通过 MockTransport 验证请求、
JSON、usage、费用、错误分类和日志脱敏。完整双 Researcher Run 能成功
fan-in；429 只由 Worker 重试，401 不重试，timeout 记为 `timed_out`；
付费但截断的响应仍保存 Token 和费用。独立 smoke 命令默认 dry-run；
M2.3b 首次联网验收的旧 v1 边界只有显式 `--execute` 才允许两次短合成素材
请求。DeepSeek 响应不提供账户结算币种，因此通过
`EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY|USD` 明确选择，不根据地区、Key 或
余额猜测。默认 `USD` 保持旧行为；当前 CNY 账户在本地 `.env` 设为 `CNY`。

真实联网验收：`deepseek-v4-flash` 的 Timeline 与 Theme 调用均在 attempt 1
成功，严格 Schema、来源引用、逐字 quote 与 fan-in 全部通过。合计 1,092
input tokens、1,209 output tokens、15,435 ms Provider 耗时，预估费用
0.000491 USD；无 retry、timeout 或错误码。

币种正确性验收：新的调用按所选官方价格表保存 `cost_currency`，摘要分别
汇总 USD 与 CNY。现有 USD smoke 记录保持不变；复用现有表结构，不新增
migration。

演示：使用短合成素材完成两个 Researcher，Trace 中可见调用与分币种成本数据。

### M2.4：采访脚手架

- [x] Interview Scaffold schema/prompt
- [x] 合并 Timeline 与 Theme Artifact
- [x] 引用完整性校验
- [x] Markdown 导出

验收：新的 `episode-research` payload 必须同时包含非空 `topic` 与
`source_ids`，并记录为 workflow v2。Timeline 与 Theme 仍并行执行；Manager
先确定性写入 research bundle，随后才串行运行 Interviewer。Interview
Scaffold 使用严格 schema/prompt，拒绝多余字段、空文本、标题偏离 topic，
以及 research bundle 之外的引用。完整成功 Run 包含 4 个 Tasks、4 个
Artifacts 和 3 个 ModelCalls。

导出：`GET /runs/{run_id}/exports/interview-scaffold.md` 从最终 Artifact
确定性生成安全 Markdown，保留来源引用，并转义模型文本中的 Markdown
控制语法和原始 HTML。旧 workflow v1 的 in-flight Run 保持原有语义，在
research bundle 完成，不会被追加 Interviewer。M2.4 复用现有表结构，不新增
migration；默认 Fake Provider 仍为零 Token、零费用。

成本边界：当前 live smoke harness 最多 3 次模型调用，可覆盖两个
Researchers 与串行 Interviewer；本阶段只完成 dry-run，没有新增付费实测。
历史 live smoke 仍是 2 次调用、2,301 tokens、0.000491 USD，未被改写。

演示：使用已导入文字素材和默认 Fake Provider 生成带来源引用的采访脚手架，
再通过导出 API 获取 Markdown。完整测试套件共 99 个测试，全部通过。

M2 完成标准：以上四个小步全部通过测试和演示后，才进入 M3。

## M3：Human-in-the-loop

状态：**完成并冻结（2026-07-31）**。M3.8 是针对 M3.7d 暴露的长度缺口所做
的一次有界修正；除该修正外，后续产品想法进入新 Milestone，不再继续扩张 M3。

- [x] `waiting_for_user`
- [x] Resume API
- [x] 用户新增素材
- [x] Editor
- [x] Markdown/Show Notes 导出

### M3.1：持久化人工检查点

新的 `episode-research` Run 使用 workflow v3。两个 Researcher 和串行
Interviewer 完成后，Run 不再立即成功，而是停在
`waiting_for_user / awaiting_interview_response`。此时四个 Task 已全部结束，
采访脚手架已经可以导出，Worker 没有待领任务，也不会继续调用模型。

用户先通过现有 `POST /sources` 把**已经转成文字**的补充口述导入为新
Source，再调用 `POST /runs/{run_id}/resume` 提交 Source ID。M3.1 会保存一条
只含 Source/Segment 引用的 `user_material_submission` Artifact，并确定性完成
当前检查点。Resume 不复制口述正文、不创建新 Task、不调用 Provider，所以
ModelCall 仍为三次、不会产生额外 Token 或费用；最终
`output_artifact_id` 暂时仍指向采访脚手架。

验收：等待状态可导出、重启后仍然等待；相同 submission 重发只保存一次，
同 ID 不同素材返回 409；缺失 Source 返回 404 且继续等待；等待时可以取消，
取消后不能 Resume；同进程内并发 Resume、并发 Resume/Cancel 均只有一个有效
状态转换。v1 仍停在研究 Bundle，v2 仍在脚手架后成功。复用既有表结构，无
新 migration；Fake 全流程 E2E、受限 DeepSeek 真实 E2E 与 M3.1 阶段的 130 项测试
通过，Alembic 无 schema drift。真实验收使用三份完整初始素材和一份补充
口述，成功走到 `waiting_for_user -> Resume -> succeeded`：4 个 Source、
21 个 Segment、4 个成功 Task、3 次模型调用、最终 5 个 Artifact 和 29 个
Event，本地估算 CNY 0.023386。第一次完整素材 Run 暴露了合并 Bundle 错误
复用 8,000 字原始素材上限的问题；现在分别使用 8,000 / 24,000 字边界。
失败记录与内容质量复核均保留在学习章节。

边界：本步的“口述”只指文本，不申请麦克风权限，也不处理录音、实时语音
转文字、TTS 或语音克隆。多进程 Resume 的数据库级 CAS/冲突重读属于部署
阶段；当前保证的是文档约束下的本机、单进程、单 `RunService` 语义。

演示：Workflow 暂停，用户补充口述文字后可靠 Resume；当前导出物仍是采访
脚手架。

### M3.2：Editor 与最终 Markdown

- [x] Resume 后排队 Editor Task
- [x] 读取 Interview Scaffold 与补充 Source
- [x] 生成可审阅的播客口播稿 Markdown
- [x] 生成 Show Notes
- [x] 保留来源引用、模型调用账本与失败恢复
- [x] 将现有 E2E 延伸到最终 Markdown

演示：同一份合成 fixture 从初始 Source 一路生成包含补充材料的可录口播稿和
Show Notes。正式 Web UI 仍不阻塞本步；M5 再复用同一 API E2E 做页面操作测试。

验收：新建 `episode-research` Run 使用 workflow v4。人工等待点仍为
4 Tasks / 4 Artifacts / 3 ModelCalls；第一次合法 Resume 创建一条只含引用的
`user_material_submission`，并排队一个串行根 `build_podcast_draft` Editor
Task。Editor 的 strict schema 要求 Podcast Script 同时引用初始与补充素材，
Show Notes 也必须引用补充素材。成功后最终为 5 Tasks / 6 Artifacts /
4 ModelCalls，`output_artifact_id` 指向 `build_podcast_draft_result`；原
Scaffold Artifact 继续保留。

导出：

```text
GET /runs/{run_id}/exports/interview-scaffold.md
GET /runs/{run_id}/exports/podcast-draft.md
GET /runs/{run_id}/exports/show-notes.md
```

Podcast Draft 与 Show Notes 由严格 JSON 确定性渲染为安全 Markdown，正文
使用 `[S1]` 短引用并在文末列来源索引。内部 Source/Segment ID 仍保留在
SQLite 与 Artifact。v1 / v2 / v3 在途 Run 保持原有完成语义；本步复用现有
表结构，不新增 migration。

可靠性验收覆盖 Resume 重放、Editor retry、重启恢复、等待或运行时取消、
第四次调用前的预算拒绝、最终导出未就绪和引用越权。完整 151 项测试、Ruff、
Alembic 和 Fake E2E 通过。2026-07-29 还使用同一合成 fixture 显式完成一次
`deepseek-v4-flash` 真实 E2E：4 次调用全部成功，合计 16,667 input tokens、
9,468 output tokens、73,018 ms Provider 耗时，本地估算 CNY 0.035603；
估算值不是厂商账单，最终内容仍需人工审阅。

### M3.3：Creative Brief、目标时长与素材充足度

- [x] Run 创建时保存 Creative Brief
- [x] 支持 10 / 15 / 30 分钟目标时长与可调口播速度
- [x] Editor 前生成确定性 `MaterialReadinessReport`
- [x] 素材明显不足时进入第二个持久检查点
- [x] 多轮补充 Source、幂等 Resume 与重启恢复
- [x] 合成 Source / 补充材料的自动 Fake E2E

首版 Readiness 只计算去重后的非空白素材字符、初始与补充材料是否存在、
来源多样性，以及相对于目标时长的保守字符下限。默认按每分钟 280 个字符、
上下 15% 容差估算；这些值可以随 Creative Brief 调整，不代表真实录音速度。

素材明显不足时，系统不会要求 Editor 用重复段落凑够时长，而是持久化停在
`waiting_for_user / awaiting_more_material`。没有新的有效 Resume，就不创建
Editor Task、不增加该次 ModelCall 或费用。补充材料达到门槛后，才继续沿用
M3.2 的 grounded Editor 和 Markdown 导出。

验收使用隐私安全的合成初始 Source 与补充口述自动完成，不要求开发者每次
真人口述。三份初始 Source 原文共有 2,106 个非空白字符；按 Scaffold 引用
最小披露后，Readiness 实际使用 488 个。补充 2,215 个后合计 2,703 个，
越过 2,380 门槛并进入 Editor。暂停时为 4 Tasks / 5 Artifacts /
3 ModelCalls，最终为 5 / 8 / 4。E2E 完全关闭并重启 App，确认等待状态、
事件、调用与费用没有变化；Resume 重放也没有重复排队。Fake 全流程零
Token、零费用。Synthetic fixture 只证明工程流程和合同有效，不能算作真实
用户体验或个人声音验证。

M3.3 还恢复了初始素材最小披露，原子拒绝重复 Source 和累计第 501 个补充
Segment，移除了隐藏的 20 轮死锁，并把 Editor 默认输出 ceiling 调整为
20,000 tokens，使 30 分钟 Brief 不再与旧的 4,000/6,000 限制冲突。

Readiness 同时按稳定 Segment 引用和规范化正文去重；把同一段文字复制到
另一个 Source，既不会增加可用字符，也不会伪造来源多样性。

M3.3 完成时 178 项测试、Ruff、Alembic、diff check 和独立 Fake E2E
全部通过；没有重复进行一次付费 DeepSeek E2E，真实生成与模型自评将在
M3.4 合并后用同一 fixture 一次性验证。

详细学习与本地排查步骤见
`docs/learning/m3-3-creative-brief-material-readiness.zh-CN.md`。

### M3.4：Draft Quality Report 与用户反馈

- [x] 确定性时长、重复、引用与模板化表达检查
- [x] 严格 Schema 的独立模型评价 Task
- [x] 每项评价提供 Draft 位置和逐字证据
- [x] 代码计算最终 decision，模型不能覆盖硬性 blocker
- [x] 模型评价与真实用户反馈分开保存
- [x] Reviewer 失败时保留 Draft 并生成降级报告
- [x] JSON / Markdown 报告与 append-only feedback API
- [x] 完成一次受限 DeepSeek 生成 + 自评 E2E 并记录最终数字

带 Creative Brief 的新 Run 默认进入 workflow v6；显式
`draft_quality.enabled=false` 时保留 v5。Editor 后先由普通代码保存
`draft_metrics_report`，检查目标时长、引用覆盖、来源多样性、重复、Brief
约束、filler 和模板表达，再排队一个串行 Quality Reviewer。

模型自评不进入 M3.3 的素材充足度门槛。首版即使使用与 Editor 相同的模型，
也明确标记为 self-review 和 advisory；它不能冒充人工评价，也不输出一个
不可解释的“AI 概率”。每个可评价维度都必须提供 Draft location 和逐字
quote，引用范围由代码验证，最终 decision 由代码计算。

Reviewer 在 retry 后仍失败或模型预算不足时，系统保留已通过来源合同的
Draft、确定性指标和错误码并完成 Run。已有确定性 blocker 时仍为
`blocked`，否则为 `automated_review_incomplete`。自动 E2E 可以提交
`synthetic_test` 反馈验证接口，但产品指标不得把它计为真实用户认可；当前
无鉴权 API 的 origin 只是调用方声明。
M3.4 继续复用现有表，无 migration。2026-07-29 验证快照为 Ruff、71 文件
format check、205/205 pytest、Alembic upgrade/check、M3.3 Fake 回归 E2E 和
M3.4 Fake E2E 全部通过；205 是阶段快照，不是永久测试总数。

2026-07-29 的真实 DeepSeek v6 Run
`run_276a3bce22394eb8a56edd6af8760012` 完成 5/5 次调用、6 个成功 Task、
11 个流程 Artifact，提交一份幂等 `synthetic_test` 反馈后为 12 个
Artifact。合计 26,618 input tokens、11,239 output tokens、61,669 ms 模型
耗时，本地估算 CNY 0.049096。三阶段 App 重启、持久 Reviewer 队列、补充
来源引用、反馈重放和 85 行无正文 JSON 日志均通过。

质量 decision 为 `revision_recommended`：确定性 72 分发现 10 分钟目标只有
1,429 个非空白字符、估算 5.1 分钟；引用覆盖 100%，使用 4 个 Source /
10 个 Segment，没有完全重复段落，但有 1 次 filler 和 4 次“不是……而是……”。
同一个 DeepSeek 模型的六维自评却全部为 5/5，实验综合分 83.2。这种差异正好
说明 same-model review 只能 advisory；正确动作是补充素材，不是灌水凑时长。

前一次付费尝试在 Editor 被严格合同
`podcast_draft_missing_supplemental_source_reference` 拒绝。增强输出末尾
引用自检后，本次成功。前一次本地估算约 CNY 0.039696，属于单独的开发调试
费用，不能并入成功 Run 的 CNY 0.049096。两者都是本地价格表估算；官方账单
可能因计费口径、缓存处理或用量同步延迟而不同。

### M3.5：中文质量校准与 Reviewer 对照实验

- [x] 新质量 Run 升为 workflow v7，并保留持久化 v6 的旧合同恢复能力
- [x] 口播字数只统计 opening / section paragraphs / closing 正文
- [x] Reviewer 接收并校验代码所有的 deterministic facts
- [x] 39 / 59 / 79 非补偿分数上限与模型/代码冲突记录
- [x] Report 读写时校验分数、cap、reasons 与 decision 的跨字段一致性
- [x] 版本化中文表达启发式，不输出“AI 写作概率”
- [x] legacy 重叠规则与 must-include 字面 miss 改为 info
- [x] Quality Reviewer 独立模型路由与实际 provider/model 记账
- [x] 同一冻结 Draft 的 Flash / Pro 比较与 current-rules 重算模式
- [x] 完整 Fake E2E、真实 DeepSeek Run、离线 harness 重验和实验记录

时长计数边界现在是一个可以逐字段解释的合同：只数用户真正会说出的文字。
标题、章节名、结构化 SourceReference、`[S1]`、来源索引、Show Notes 和
最终 Markdown 的排版字符都排除。Reviewer Task 同时携带应用代码生成的
目标/估算时长、字符数、覆盖率、引用覆盖、finding 数量和中文模式计数；
validator 会从当前 Draft 重新计算关键事实，不一致就不进入模型。

综合分保留模型原始六维分和未封顶 60/40 结果，再套用最严格的 code-owned
cap：任一 blocker 最多 39，时长覆盖低于 60% 最多 59，任一 warning 最多
79。这样“其他维度很好”不能补偿一个明确硬伤。中文启发式只用于定位可能
需要改写的表达，不能判断作者是否为 AI。旧的 template / not-but 规则仅作
兼容展示，不和新分类重复扣分；must-include 字面没出现只记 `info`，是否
已经同义覆盖交给有 Draft/Source 证据的 Reviewer。

验收记录：

- 正式 v7 Fake Run `run_f41eac8520cd4b47b97cc1181acb3d63` 全流程通过，
  并通过自动化重启回归验证升级前排队的纯 legacy v6 Reviewer Task 仍可完成，
  以及预发布 `v6 + current facts` Task 仍保留 v2 cap 与 conflict；
- DeepSeek Run `run_0af27a7596474a92ba79e298e912e35e` 是 M3.5 预发布
  workflow-v6 开发快照；workflow 成功，
  2,055 个口播字符、估算 7.34 / 15 分钟，五次调用本地估算
  CNY 0.089433；
- 该 Run 的旧报告出现两项 harness false negative：旧检查器仍假设 Editor /
  Reviewer 同模型并只接受旧关系值。修复后对同一数据库离线重验，两项都为
  true，没有为修 checker 再付费调用模型；
- 当前规则重算 deterministic 62，包含 1 blocker、1 warning、2 info，
  最严格 cap 为 39；
- 第一轮历史快照冻结比较为 Flash 76.67、Pro 80，最终均为 39；
- `--recompute-current-rules` 比较中 Flash strict schema 失败，Pro raw 70、
  final 39；两次调用本地估算 CNY 0.013950 / 0.044008。

这只是一个主题、一份合成材料和一份冻结 Draft。它能证明合同、路由、上限和
错误呈现有效，不能证明 Pro 永远优于 Flash。模型选择需要继续积累不同主题、
长度和真实用户评分。

### M3.6：写作样本与显式反馈驱动的 Revision Run

- [x] 新质量 Run 升级为 workflow v8；新增 `runs.parent_run_id` migration
- [x] 可选接收一至五份经用户确认归属与模型处理授权的写作样本
- [x] 写作样本只建立有界 `writing_style_profile`，不提供事实、指令或引用
- [x] Editor 按“安全/事实 > 本轮要求与 Brief > 写作样本 > 默认写法”处理
- [x] 样本达到 800 字符和五句后，Reviewer 才增加第七维个人风格匹配
- [x] 确定性 Improvement Plan 区分未使用事实、需补素材和可降低目标时长
- [x] 用户显式选择 feedback、gap、补充 Source、目标时长与修订指令
- [x] 新增受限 `revise_podcast_draft` Task，不静默覆盖旧稿
- [x] 子 Run 独立记账与预算，并重新执行 metrics、Reviewer 和非补偿 cap
- [x] `submission_id` 幂等重放；同 ID 不同请求冲突，不重复创建子 Run
- [x] 懒加载持久化旧稿/新稿对比，不自动选 winner，仍要求人工审核

M3.6 的人机边界已经落地：读取 Improvement Plan 不调用模型，也不创建子
Run；只有用户明确提交 `POST /runs/{run_id}/revisions` 才会开始一次新的
`podcast-revision`。父 Run 的 Draft、Report、Feedback、output 与历史调用
账本保持不变，子 Run 通过 `parent_run_id` 追溯来源并使用独立预算。

自动化 Fake workflow 测试已经覆盖完整父子链路、样本隔离、幂等、质量复评
和 comparison。M3.6 尚未进行真实 DeepSeek E2E；在得到真实模型与人工内容
复核证据前，不把 Fake 结果表述为真实生成质量验收。

### M3.7：写作样本受控 A/B 验证

#### M3.7a：冻结输入与零费用预检

- [x] 从一个已完成的 v8 Run 读取唯一 Editor 输入
- [x] 要求写作样本经过明确授权且 `writing_style_profile` 为 `ready`
- [x] 构造“不提供 Sample / 提供 Sample”两个 Editor 输入
- [x] 用规范化 hash 证明除写作样本外，其余输入完全相同
- [x] 冻结质量配置、模型与调用上限，并证明 Sample 确实进入 Editor Prompt
- [x] SQLite 使用 `mode=ro + query_only`，拒绝修改源 Run
- [x] 默认只输出脱敏 dry-run，不联网、不写实验产物、不修改 Run、不调用模型
- [x] 使用真实持久化 Fake v8 Run 完成手工预检

M3.7a 不生成候选稿，也不声称写作样本已经有效。它只把实验前提变成代码
可以验证的合同，避免把不同素材、不同 Brief 或不同采访结果误当成风格提升。

#### M3.7b：有界生成与同条件 Reviewer

- [x] 同一 Flash Editor 分别生成两份候选稿
- [x] 两稿分别执行严格来源校验、Sample 泄漏检查和确定性质量分析
- [x] 同一 Pro Reviewer 在相同 ready Sample 下评价两稿
- [x] 把真实调用严格限制为 2 次 Editor + 2 次 Reviewer
- [x] 付费调用前重新计算并核对预检合同 hash，阻止预检后的输入/配置漂移
- [x] 用 scripted Provider 覆盖失败、记账、顺序与日志脱敏

#### M3.7c：匿名候选与真人揭盲

- [x] 随机映射 Candidate A / B，并把私有映射与候选正文分开保存
- [x] 用户在揭盲前提交 `voice_match_rating`、可录性与 forced choice
- [x] 以真人声音匹配为主证据，模型第七维只作辅助
- [x] 完成一次真实 DeepSeek 单 pair 实验并记录 tokens、费用与限制
- [x] 保存一次真人盲评并在评分后揭盲
- [x] 检测候选区分度不足，并阻止从高度相似的两稿中宣布 winner

揭盲结果为 A=`with_sample`、B=`without_sample`。真人声音匹配为 3/5 与
2/5，可录性均为 3/5；用户低置信度偏好 A。但 10 个口播单元中有 9 个逐字
相同，逐字重合率 0.90，规范化字符相似度 0.9638，因此最终结论是
`inconclusive_low_distinctness`，不是“Sample 获胜”。这次合成 Sample 的
单 pair 不能证明真实个人写作样本有效。

#### M3.7d：收口后的真实量级合成人设复验

- [x] 冻结三份事实 Source、一份完整补充口述和四篇独立写作 Sample
- [x] 跑通真实 checkpoint、进程重启、Resume 与 Editor；Reviewer 调用返回后
  因证据合同失败而按设计降级
- [x] 发现并修复实验驱动器 32k 与产品配置 48k 的限制漂移
- [x] 用同一冻结输入完成四调用 DeepSeek A/B 与匿名合成评审

复验得到的是方向性证据：Sample arm 已产生可辨识差异，匿名合成评审也偏好
它的声音与可录性，但这仍不是真人证明。两稿都只有约 6.4—6.7 分钟，远低于
15 分钟目标，最终分被硬性封顶为 39；一次 Reviewer 输出也因
`exact_quote` 不是 Draft 逐字子串而被严格降级。这些状态应在 M4/M5 的 Trace
界面中被看见，而不是继续用 Prompt 隐藏。完整记录见
[M3.7d 实验报告](experiments/m3-7d-realistic-persona-e2e.zh-CN.md)。

M3.7d 后已达到写作样本实验的硬性收口点。不继续建设通用 benchmark、自动
winner、多用户统计或盲评 UI。它同时暴露了一个可以用小切片修复的产品缺口：
素材总量足够时，初稿仍可能只使用一小部分，所以在转入 M4 前用 M3.8 修复
一次有来源的时长恢复，不重新打开 M3 的实验范围。

### M3.8：基于现有证据恢复口播时长

- [x] 口播时长只统计 opening、正文 Paragraph 与 closing
- [x] Show Notes 和 Section 元数据中的引用不再把事实片段标记为正文已使用
- [x] 确定性计算当前、85% 最低、目标、115% 最高字符数和长度缺口
- [x] 盘点口播正文完全未引用的事实 Segment，并生成优先引用列表
- [x] 仅在用户显式选择 `reuse_unused_material` 时把 Recovery Plan 交给
  Revision Editor
- [x] Revision 后重新执行引用、时长、重复、filler、中文风格规则和 Reviewer
- [x] 保持父 Run 不可变、子 Run 独立预算、请求幂等和无自动修订循环
- [x] 一次恢复后持久化历史，不再推荐连续复用，转补材料或降低时长
- [x] 检测明显 editorial instruction 泄漏，并修正普通“最后”的列举误报
- [x] Fake v8 验证 456 → 2,083：workflow pass、content fail、停止规则生效
- [x] 固定真实量级合成素材完成 DeepSeek v2：1,310 → 2,371，7 calls，
  本地估算 ¥0.201153，15 分钟内容验收未通过
- [ ] 由真人复核 voice match、可录性和真实录音时长

当前 `unused` 是引用级近似：只识别“整个 Segment 从未被口播单元引用”，不能
识别“已经引用但展开不足”。`available_unused_character_count` 只是候选
字符容量，不评价相关性、重复、隐私风险或最终文稿质量。即使数量看起来充足，
模型也可能合理地保持短稿。Source 还没有结构化 `material_kind`，当前只能在
成稿侧报告明显编辑指令泄漏。一次恢复后，产品应提示补充具体素材或降低目标
时长，而不是继续推荐同一批素材或自动循环扩写。

### M3.9：根据最新稿定向追问，再用回答继续修订

- [x] 只在显式 Revision 重新审稿后仍低于 85% 时长下限时规划补充采访
- [x] 从最新 Draft 的 opening、正文 Paragraph 与 closing 建立可信 Anchor
- [x] 每个问题必须绑定 Anchor，并逐字引用最新稿中的具体行文
- [x] 把问题计划持久化为只读 Artifact；重复读取不调用模型
- [x] 用户回答以新 Source 保存，并在 versioned Revision Request 中绑定
  Plan、question ID 与 Source ID
- [x] 回答 Revision 优先融合新事实，同时保留父稿已有的有效内容
- [x] 服务端推导轮次并限制最多两轮；达到下限后不再创建 Planner
- [x] Planner 失败时保留有效 Draft，并生成可追踪的确定性 fallback Plan
- [x] workflow v9 只用于新的 v2 Revision 子 Run；既有 v8 语义继续可恢复
- [x] Fake E2E 验证 2,509 → 3,073 → 3,637，并覆盖绕过、幂等、失败回退和
  第三轮停止
- [ ] 用受限 DeepSeek 与真人回答验证问题是否真的触发具体记忆

M3.9 不会自动替用户回答，也不会自动创建下一版稿子。每一轮新事实都来自用户
显式导入的新 Source，每一版稿子都来自显式 Revision 请求。问题 Planner 只负责
把最新稿的具体缺口变成可回答问题；Editor 和 Reviewer 继续各守自己的职责。
两轮后仍短时，系统停止规划，交由用户选择继续主动补充或降低目标时长。

至此 M3 的产品闭环冻结。下一阶段不再继续优化 Prompt，而是把已有 Run、Task、
ModelCall、Draft、质量报告、问题计划和 Revision lineage 放进可回放 Trace 与
最小 UI。详细实现与验证见
[M3.9 学习章节](learning/m3-9-draft-aware-supplemental-interview.zh-CN.md)。

## M4：可靠性与 Trace

- [ ] timeout / bounded retry
- [ ] idempotency key
- [ ] lease / fencing
- [ ] startup recovery
- [ ] cancel propagation
- [ ] SSE replay + live stream
- [ ] 故障注入测试

演示：运行中杀掉 Worker，重启后恢复；取消父 Run 后迟到结果无法提交。

## M5：最小 Web UI 与部署

- [ ] Project/Source 页面
- [ ] Run trace 页面
- [ ] Scaffold 编辑与恢复
- [ ] Dockerfile
- [ ] 单机部署
- [ ] 健康检查、结构化日志、备份说明

演示：从浏览器完整走通一次创作流程。

## Later

- 本地音频转录；
- 录音时间戳引用；
- 候选长期记忆与确认；
- 可替换本地模型 Provider；
- 语音合成和本人授权的 voice clone；
- PostgreSQL / 多 Worker；
- 评估 LangGraph 或 Temporal。
