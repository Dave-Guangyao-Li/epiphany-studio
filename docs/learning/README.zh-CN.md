# Epiphany Studio 学习实践手册

更新时间：2026-08-03

## 这套手册解决什么问题

Epiphany Studio 不只是一个等待 AI 帮忙完成的产品，也是一个用来亲手
理解全栈开发、Agent 编排和生产可靠性的学习项目。

代码回答“系统现在怎么运行”，这套手册回答：

- 这一步为什么要做；
- 它解决了什么真实问题；
- 新增了哪些模块，各自负责什么；
- 背后的技术概念是什么；
- 怎样自己运行、测试和观察；
- 出错时从哪里开始排查；
- 这一步完成后，项目真正具备了什么能力。

目标是即使几个月后重新回来，也能通过手册快速恢复上下文，而不是只看到
一堆陌生代码和 commit。

## 它和其他文档的区别

| 文档 | 主要回答的问题 | 目标读者 |
| --- | --- | --- |
| `product-spec` | 产品要解决什么问题 | 产品与设计 |
| `architecture` | 系统整体如何设计 | 工程设计 |
| `roadmap` | 下一步按什么顺序建设 | 项目规划 |
| `devlog` | 哪天完成了哪些事实 | 工程维护 |
| 本学习手册 | 为什么这样做，我怎样亲手验证 | 未来的自己和初学者 |

## 阅读顺序

第一次阅读建议按以下顺序：

1. [常见术语表](glossary.zh-CN.md)
2. [本地运行、测试与调试](local-development.zh-CN.md)
3. [M0：项目起点与架构选择](m0-foundation.zh-CN.md)
4. [M1：持久化 Agent Runtime](m1-durable-runtime.zh-CN.md)
5. [可观测性：如何知道系统发生了什么](observability.zh-CN.md)
6. [M2.1：Source 与来源引用](m2-1-source-contract.zh-CN.md)
7. [M2.2：双 Agent 并行编排](m2-2-parallel-agents.zh-CN.md)
8. [M2.3a：零费用模型调用 Trace](m2-3a-model-call-trace.zh-CN.md)
9. [M2.3b：DeepSeek Provider](m2-3b-deepseek-provider.zh-CN.md)
10. [M2.4：从研究结果生成采访脚手架](m2-4-interview-scaffold.zh-CN.md)
11. [M3.1：可持久化的人工暂停与恢复](m3-1-human-checkpoint.zh-CN.md)
12. [M3.1：后端 / API 全流程 E2E 验证](m3-1-backend-e2e.zh-CN.md)
13. [M3.1：一次接近真实用户的 DeepSeek 全流程验收](m3-1-realistic-e2e.zh-CN.md)
14. [M3.1 realistic E2E：运行证据与内容复核](m3-1-realistic-e2e-evidence.zh-CN.md)
15. [M3.2：Editor 与最终 Markdown](m3-2-editor-final-markdown.zh-CN.md)
16. [M3.3：Creative Brief、目标时长与素材充足度](m3-3-creative-brief-material-readiness.zh-CN.md)
17. [M3.4：Draft Quality Report、模型自评与用户反馈](m3-4-draft-quality-report.zh-CN.md)
18. [M3.5：中文口播质量校准与冻结稿 Reviewer 实验](m3-5-chinese-quality-calibration.zh-CN.md)
19. [M3.6：授权写作样本与显式 Revision 子 Run](m3-6-guided-revision-writing-style.zh-CN.md)
20. [M3.7a：写作样本 A/B 的冻结输入与零费用预检](m3-7a-writing-style-ab-preflight.zh-CN.md)
21. [M3.7b/c：受控写作样本 A/B 与匿名盲评](m3-7bc-controlled-writing-style-experiment.zh-CN.md)
22. [M3.7d：真实量级合成人设、写作样本 A/B 与匿名合成评审](../experiments/m3-7d-realistic-persona-e2e.zh-CN.md)
23. [M3.8：基于现有证据恢复口播时长](m3-8-grounded-length-recovery.zh-CN.md)
24. [M3.9：根据最新稿定向追问，再用回答继续修订](m3-9-draft-aware-supplemental-interview.zh-CN.md)
25. [M4/M5：Project 工作区与可重放 Run Trace](m4-m5-local-console.zh-CN.md)
26. [M5.1：AI 起步助手与可见的四步进度](m5-1-source-starter.zh-CN.md)
27. [M5.1b：真实浏览器全流程 E2E](../experiments/m5-1b-real-browser-e2e.zh-CN.md)
28. [SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)

## 当前进度

| 阶段 | 用普通话描述 | 状态 | 对应 commit |
| --- | --- | --- | --- |
| M0 | 建立独立项目，明确产品和技术边界 | 完成 | `a37e23c` |
| M1 | 后端可以持久化并执行三步假工作流 | 完成 | `65f6046` |
| 开发流程 | 确立小步、可测试、及时提交的规则 | 完成 | `b0b07e7` |
| 可观测性 | 请求、任务和错误可以通过 ID 与日志追踪 | 完成 | `0f5dd5a` |
| M2.1 | 可以导入、分段并稳定引用文字素材 | 完成 | `0f2b46b` |
| M2.2 | Manager 可以并行调度两个研究 Child Task | 完成 | `8e4306a` |
| M2.3a | 在真实调用前记录预算、尝试、耗时与费用 | 完成 | `4d48c90` |
| M2.3b-1 | DeepSeek 接口、Prompt、错误和费用可离线验证 | Mock 已验证 | `046358c` |
| M2.3b-2a | 受限真实调用命令、dry-run 与脱敏输出 | 离线已验证 | `fd232e0` |
| M2.3b-2b | 使用短合成素材执行两次真实调用 | 完成 | 本次 focused commit |
| M2.3b-3 | DeepSeek 费用可显式按 USD/CNY 估算与分组 | 完成 | 本次 focused commit |
| M2.4 | fan-in 后串行生成并安全导出采访脚手架 | 完成 | `81b150d`、`d30f68f` |
| M3.1 | 生成脚手架后持久化等待用户，并用补充 Source 幂等恢复 | 完成 | 本次 focused commit |
| M3.1 E2E | 用完整合成素材跑通 Source、Checkpoint、Resume、日志、数据库与 Markdown | Fake + DeepSeek 全流程通过 | 本次 focused commit |
| M3.2 | Resume 后运行 grounded Editor，导出口播稿和 Show Notes | Fake + DeepSeek E2E 已验证 | 本次 focused commit |
| M3.3 | 保存创作目标，素材明显不足时持久等待，补足后才运行 Editor | 178 tests + Fake E2E 已验证 | 本次 focused commit |
| M3.4 | 用可解释规则、证据化模型自评和独立用户反馈审阅 Draft | 205 tests + Fake/DeepSeek E2E 已验证 | 本次 focused commit |
| M3.5 | 只按口播正文估时、校准中文风格信号，并阻止模型高分掩盖硬性问题 | 已完成并合并 | `1fc84de` |
| M3.6 | 用授权写作样本约束风格，并由用户显式创建可追溯 Revision 子 Run | 292 tests + Fake E2E 已验证；真实 DeepSeek E2E 待执行 | `f418331`、`ab55b6b`、`53bb478` |
| M3.7a | 冻结同一份 v8 Editor 输入，证明 Sample A/B 只改变写作风格上下文 | 308 tests + Fake v8 手动预检通过；零模型调用 | 本次 focused commit |
| M3.7b/c | 有界生成两稿、同条件 Reviewer、匿名真人盲评与揭盲 | 310 tests + DeepSeek 4/4 调用 + 真人揭盲通过；因区分度不足结论为 inconclusive，M3 已冻结 | 本次 focused commit |
| M3.7d | 用完整虚构人设复跑持久化主流程和 Sample A/B | 315 tests + DeepSeek 主流程 5 次调用 + A/B 4/4；候选可区分并有方向性合成证据，M3 仍冻结 | `3fdf8c1`—`3b01f9b` |
| M3.8 | 初稿偏短时先复用口播正文完全未引用的事实；一次后转补材料或降时长 | Fake v8 workflow pass/content fail；DeepSeek 1,310→2,371，7 calls，¥0.201153 | 本次 focused commit |
| M3.9 | 既有素材修订后仍短时，围绕最新稿原句生成具体追问；回答作为新 Source 再显式修订，最多两轮 | 354 tests + Fake v9 完整闭环 2,509→3,073→3,637；失败回退、轮次和 provenance 已验证 | 本次 focused commit |
| M4 | timeout、retry、lease、fencing、恢复、取消与 Event Trace 可通过 replayable SSE 观察 | 363 backend tests；replay/heartbeat/disconnect/terminal 已验证 | 本次 focused commit |
| M5（本地 UI） | 在浏览器管理 Project/Source，并查看 Run Trace 与人工检查点 | 15 frontend tests + production build；Scaffold editor/部署仍未完成 | 本次 focused commit |
| M5.1 | 空 Project 可生成可编辑起步候选，持久等待确认；确认后原子导入 Source 并让 Run 成功 | backend 442 tests + Ruff；frontend 43 tests + build；Fake 与 Playwright/DeepSeek E2E 通过 | Completed；当前 PR 加固 |

## 当前系统已经能做什么

默认本地 Console、Swagger 和开发路径继续使用零费用 Fake Provider，可以完成：

```text
创建并重新打开 Project
  -> 在 Project 中导入、查看 Source 和 SourceSegment
  -> 空白时可创建独立 source-starter Run，查看四步进度
  -> 候选通过后 durable waiting；编辑确认时原子导入 AI-assisted Source
  -> 网络重试只 GET 同一 Run；刷新恢复 Artifact、mode 与 intent，但不假装恢复未保存的本地编辑
  -> 从页面配置事实素材、写作样本、受众、语气和目标时长
  -> 使用幂等 submission_id 创建 Project Run
  -> 可选从已有 Source 中明确选择并授权 style-only 写作样本
  -> 用 topic、source_ids 和 creative_brief 创建 episode-research v8 Run
  -> Manager 分发两个 Child Task
  -> Timeline / Theme Fake Researcher 并行执行
  -> 严格校验结构和来源引用
  -> 合并为 episode_research_bundle
  -> 串行 Interviewer 生成 build_interview_scaffold_result
  -> 严格校验脚手架内每一处来源引用
  -> 确定性生成 MaterialReadinessReport
  -> 素材不足时持久化停在 awaiting_more_material
  -> 通过 API 安全导出带引用的 Markdown
  -> 用户把补充口述文字导入为新 Source
  -> 用 Source ID 幂等 Resume 并累计多轮材料
  -> 达到目标时长下限后才自动排队 Editor Task
  -> 严格校验初始来源与补充来源引用
  -> 导出口播稿和 Show Notes Markdown
  -> 计算时长、引用、重复和模板表达指标
  -> 只用 opening / section paragraphs / closing 的正文估算口播时长
  -> Reviewer 按六维给出带逐字证据的建议
  -> ready 写作样本存在时才增加第七维 personal_style_match
  -> 代码把可信确定性事实传给 Reviewer，并验证不能被篡改
  -> 代码生成带非补偿式评分上限的 Draft Quality Report
  -> 用户最终审稿
  -> 用户单独提交声音匹配与可录性反馈
  -> 确定性生成 Improvement Plan，不调用模型、不偷偷创建 Run
  -> 用户明确选择动作后创建带 parent_run_id 的 Revision 子 Run
  -> 偏短时把 spoken-only 缺口和完全未引用事实传给一次显式 Revision
  -> 子 Run 用独立预算生成新候选并重新走质量检查
  -> 若仍短，围绕最新稿的具体原句生成 3—6 个补充采访问题
  -> 用户回答问题并导入新 Source，再显式创建下一版 Revision
  -> 最多两轮；达到 85% 时长下限后停止追问
  -> 按需保存新旧摘要与 delta；不自动选 winner
  -> 可从一个已完成 v8 Run 冻结 Editor 输入
  -> 派生“无 Sample / 有 Sample”两个实验输入，并用 hash 证明只有风格上下文不同
  -> 零网络预检后才允许后续切片执行付费 A/B
  -> 从 Run、Task、Artifact、Event 和日志中复盘全过程
  -> 在 Run Trace 页面实时观察，并在断线后按 sequence 重放 Event
```

M2.3a 让每次 Provider attempt 产生持久化调用记录，并在调用前执行单 Run
预算。M2.3b-1 又把 DeepSeek 的 HTTP、Prompt、JSON、错误和费用接到这条链路，
M2.3b-2a 提供默认不联网、必须显式 `--execute` 的两次调用命令。默认仍是
Fake；M2.3b-2b 已用短合成素材完成两次真实调用，并通过严格引用校验与
fan-in。真实 Trace 保存在独立 SQLite 中，查看方法见
[SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)。M2.3b-3 支持显式选择
USD 或 CNY 价格表；默认 USD 保持兼容，不自动猜测账户币种，也不改写历史
记录。M2.4 把研究结果继续变成可使用的采访脚手架：正常 Fake Run 最终有
4 个 Task、4 个 Artifact 和 3 个 ModelCall；Manager 只负责编排，不调用
模型。M2.4 当时的新建 `episode-research` 使用 v2，已在该阶段开始前运行的
v1 Run 仍按旧流程在 research bundle 处结束。M2.4 没有修改数据库结构，
因此没有新增 migration。M3.1 将当时的新建 Run 升级为 v3：脚手架完成后保留 4 个 Task、
4 个 Artifact 和 3 个 ModelCall，但状态停在 `waiting_for_user`，且等待
期间仍可导出脚手架。用户通过 `POST /sources` 导入一段已经转成文字的补充
口述，再调用 `POST /runs/{run_id}/resume`。首次 Resume 新增一个只保存
Source / SourceSegment 引用的 `user_material_submission` Artifact，随后
确定性结束 Run；Task 仍为 4 个，Artifact 变成 5 个，ModelCall 仍为 3 个。
相同 submission 可安全重放，重启和并发路径也有自动化测试。M3.1 同样没有
修改数据库结构；v1 与 v2 在途 Run 保持原有结束方式。

M3.2 将新建 Run 升级为 v4。人工等待点仍是 4 Tasks / 4 Artifacts /
3 ModelCalls；Resume 保存提交后自动排队一个 `build_podcast_draft` Editor
Task。Editor 同时使用初始证据、Interview Scaffold 和补充 Source，严格
结果必须在 Podcast Script 与 Show Notes 中真实引用补充材料。最终成功 Run
是 5 Tasks / 6 Artifacts / 4 ModelCalls，并可分别调用：

```text
GET /runs/{run_id}/exports/interview-scaffold.md
GET /runs/{run_id}/exports/podcast-draft.md
GET /runs/{run_id}/exports/show-notes.md
```

v1 / v2 / v3 在途 Run 继续保持各自历史语义。M3.2 也复用已有表，没有新增
migration。

M3.3 为带 `creative_brief` 的新请求使用 v5；不带 Brief 时继续使用 v4。
Brief 支持 10 / 15 / 30 分钟、可调字符速度、场景、听众、沟通目标、语气和
表达约束。Readiness 只读取 Scaffold 引用的初始片段，并用普通代码计算，
不花模型调用；初始不足或任一补充轮次
仍不足时都可靠停在 `awaiting_more_material`。达到门槛后，Editor 会收到
所有已接受补充材料与同一 Brief。正常一轮补充的 v5 最终为 5 Tasks /
8 Artifacts / 4 ModelCalls。

M3.4 为 Creative Brief 默认启用 Draft Quality，并把新 Run 升级为 v6。
Editor 后先用普通代码生成 `draft_metrics_report`，再排队一个串行
`review_podcast_draft` Task。正常一轮补充的 v6 最终为 6 Tasks /
11 Artifacts / 5 ModelCalls；最终 output 仍是 Editor Draft，不是质量报告。
显式 `draft_quality.enabled=false` 可继续走 v5。

Reviewer 固定检查 Brief 匹配、来源忠实、覆盖与具体性、结构、口播自然度和
非重复性。每个可评价维度必须带 Draft location 与逐字 quote，代码会验证
证据。默认同模型结果明确标记 self-review / advisory，最终 decision 由代码
合成，不能覆盖确定性 blocker。Reviewer 失败时，已有 blocker 仍保持
`blocked`，否则降级为 `automated_review_incomplete`；两种情况都不会隐藏
已经生成的 Draft。

M3.5 把新的质量流程升级为 workflow v7，因为持久化 Reviewer Task、Prompt、
确定性规则和报告语义已经变化；历史 v6 仍可从等待、队列或租约状态按旧合同
恢复。口播字符只统计 opening、各 section paragraph 和 closing 的 `text`，
标题、引用、来源索引和 Show Notes 不计入时长。新报告同时保留模型局部维度
平均、未封顶 60/40 分、代码上限和封顶分；blocker、低于 60% 的时长覆盖、
任一 warning 分别设置 39 / 59 / 79 的最高分，严格者优先。

M3.5 引入的中文规则 `zh_podcast_style_v1` 只报告可观察的风格风险，不判断
作者身份。M3.8 将当前新产物升级到
`zh_podcast_style_v2_enumeration_precision` 和
`draft_quality_rules_v3_editorial_instruction`，同时继续兼容读取旧版本。
`must_include` 的逐字未命中是 `info`，语义覆盖交给 Reviewer；旧模板与
`not_but` 指标保留显示但不重复扣分。Reviewer 可以单独选择 Flash 或 Pro，
同一家族不同档位只记为 `cross_tier_same_family`，不能包装成独立裁判。

M3.6 把新的质量流程升级为 workflow v8。用户可选引用一至五份自己拥有且
明确同意模型处理的现有 Source。`writing_sample` 只是可选的导入/UI 标签；
每个 Run 中显式的 style-only 选择才是权威合同，被选 ID 不能同时位于事实
`source_ids`。持久化 `writing_style_profile` 只包含引用、hash、统计和
readiness，不复制样本全文。Editor 的优先级固定为“安全与事实 > 本轮要求
和 Brief > 写作样本 > 默认写法”。样本只能帮助参考节奏、句长和口语感，
不能变成本期事实、命令或引用。

profile 至少有 800 个非空白字符和五句话才是 `ready`。只有 ready 时，
Reviewer 才增加第七维 `personal_style_match`，并同时引用 Draft 与样本
证据；样本不存在或有限时仍是六维，不能宣称已经判断“像本人”。真实的
`voice_match_rating` 仍要由用户提交。

质量 Run 成功后，`GET /runs/{run_id}/improvement-plan` 会用普通代码说明
时长差多少、是否还有未引用事实、应补什么材料、能否降低目标时长以及要处理
哪些质量 gap。读取它不会调用模型或生成子 Run。只有
`POST /runs/{run_id}/revisions` 会根据用户明确选择创建
`podcast-revision` 子 Run。稳定 `submission_id` 可安全重试；父 Draft、
Report、Feedback 和五次调用账本都不变。子 Run 使用独立预算；正常无重试
路径依次执行 Revision Editor 与 Reviewer，共两次模型调用。

子 Run 成功后，`GET /runs/{child_run_id}/revision-comparison` 会按需保存
一份不含正文的新旧摘要和 delta。它不会自动宣布哪稿更好，也不会再触发下一
轮改写。自动化 Fake workflow 已覆盖这条父子链路、样本隔离、幂等、复评与
comparison；M3.6 尚未做真实 DeepSeek E2E，因此这里不把 Fake 的可读输出
当作真实模型内容验收。

M3.7a 没有立即调用四次模型。它先从一个已成功完成的 workflow-v8 Run
读取原始 Editor Task 输入，校验写作样本确实经过授权、profile 已达到
`ready`、Editor 输出仍符合原合同，然后派生两个实验 Arm。两组共同输入的
canonical hash 必须相同；无 Sample 组只清空
`writing_style_profile / writing_style_segments`，有 Sample 组原样保留。
预检只输出 hash、数量、模型计划和隐私标记，不输出素材、Prompt、API Key，
也不修改原 Run。详细命令和设计原因见
[M3.7a 学习章节](m3-7a-writing-style-ab-preflight.zh-CN.md)。

M3.7b/c 在同一冻结输入上最多执行两次 Editor 和两次 Reviewer。执行顺序默认
随机化，输出写入独占、私有的本地实验目录；任一步失败立即停止。随后系统把
两份口播正文随机匿名成 Candidate A/B，隐藏 treatment 和 Reviewer 分数，
要求先保存真人的声音匹配、可录性与二选一判断，再允许揭盲。它是一次离线
个人实验，不修改原 Run，也不扩展生产 API 或数据库。完整操作与故障判断见
[M3.7b/c 学习章节](m3-7bc-controlled-writing-style-experiment.zh-CN.md)。

首个 pair 已完成真人评分和揭盲：有 Sample 的 A 获得 3/5 声音匹配，无
Sample 的 B 为 2/5，两稿可录性均为 3/5。但 10 个口播单元中 9 个逐字相同，
字符相似度 0.9638，因此结果只能是
`inconclusive_low_distinctness / directional_only`。blind v2 会在未来的
盲评中自动执行这项检查，避免从几乎相同的候选中制造“赢家”。

M3.7d 没有继续扩张生产功能，而是用一个更接近真实用户规模的固定合成人设
复验同一机制。新候选只有 18.75% 的口播单元逐字相同，合成盲评在揭盲前偏好
有 Sample 的候选；但它被明确标记为 `human_rating=false`，只能作为方向性
证据。更重要的发现是：4,679 个 grounded 字符已经足够支撑目标时长，Editor
却只写出约 6.4—6.7 分钟，因此下一步应先用现有未充分利用的素材创建 Revision，
而不是机械要求用户继续补充。完整复现命令、费用和失败分析见
[M3.7d 实验报告](../experiments/m3-7d-realistic-persona-e2e.zh-CN.md)。

M3.8 将这个发现收敛成一个有边界的产品修正。Improvement Plan 只把从未出现在
opening、正文 Paragraph 或 closing 引用中的事实 Segment 记为 `unused`；
Show Notes 和 Section 元数据不能假装正文已经使用素材。显式
`reuse_unused_material` Revision 会收到当前、85% 最低、目标、115% 最高和
优先事实引用，完成后重新经过 metrics 与 Reviewer。系统不自动循环扩写。

当前它不能识别“某 Segment 已被引用，但稿子只展开了其中一小部分”；候选未使用
字符数也只说明数量上可能够用，不保证相关性、信息密度或最终质量。最终 Fake
v8 从 456 增至 2,083，使用 12/12 个候选仍未越过 3,570 下限，并识别出进入
口播的编辑指令；它将下一步改为补材料。真实 DeepSeek v2 从 1,310 增至 2,371，
七次调用估算 ¥0.201153，工作流通过但 15 分钟内容验收未通过。当前规则还修正了
普通“最后”的列举误报，并只比较非时长 warning。详细原理和测试方法见
[M3.8 学习章节](m3-8-grounded-length-recovery.zh-CN.md)。

M3.9 把 M3.8 的“补充具体素材”变成一条可执行但仍由用户控制的闭环。只有
workflow v9 的 Revision 子 Run 在重新审稿后仍低于 85% 时长下限时，才会排队
一个 Supplemental Interviewer。可信代码先从最新 Draft 的 opening、正文
paragraph 与 closing 建立 Anchor；模型返回的问题必须逐字引用这些 Anchor，
再由代码注入稳定 question ID。用户回答后，答案以新 Source 保存，并通过
versioned Revision Request 记录 Plan、question 和 Source 的对应关系。

系统最多规划两轮，不会自动代答或偷偷创建下一版稿子。Fake E2E 从 2,509 字开始，
第一轮回答后达到 3,073 字，第二轮达到 3,637 字并越过 15 分钟目标的 85% 下限；
非法 question ID、绕过已存在 Plan、重复 GET 产生模型调用和第三轮 Planner 都被
回归测试拦截。Provider 失败时，已有 Draft 仍成功保存，并生成绑定最新原句的
确定性 fallback Plan。完整原理、API 与本地验证见
[M3.9 学习章节](m3-9-draft-aware-supplemental-interview.zh-CN.md)。

用户反馈与自动报告分开保存。自动 E2E 使用的 `synthetic_test` 永远是
`human_signal_eligible=false`。当前无鉴权 MVP 的 origin 是调用方自报标签，
不是已验证真人身份。详细原理、Swagger 和测试命令见
[M3.4 学习章节](m3-4-draft-quality-report.zh-CN.md)。

这里的 `voice_note_transcript` 是“已经转成文字的口述”这一 Source 分类，
不是麦克风或语音识别功能。当前可以在 Swagger 中直接输入或粘贴文字。

不打开 Swagger 也可以通过
`python -m epiphany.quality_contract_e2e --provider fake --execute`
一条命令复现整条 v5 链路，包括真正关闭 App 后从 SQLite 恢复。M3.3 的
Fake E2E 与当时的完整 178 项测试均通过。

M3.4 的 v6 链路使用：

```bash
python -m epiphany.draft_quality_e2e --provider fake --execute
```

2026-07-29 的受限真实 DeepSeek Run
`run_276a3bce22394eb8a56edd6af8760012` 完成 5/5 次调用和 6 个 Task，
提交 synthetic feedback 前后分别为 11 / 12 个 Artifact。合计 26,618 input
tokens、11,239 output tokens、61,669 ms 模型耗时，本地估算 CNY 0.049096。
三阶段重启、Reviewer 持久队列、补充引用、反馈幂等和 85 行无正文 JSON 日志
均通过。

报告没有为了展示而“报喜”：10 分钟目标的正文只有 1,429 个非空白字符，
估算 5.1 分钟，因此确定性 72 分、decision 为 `revision_recommended`。
引用覆盖 100%，来自 4 个 Source / 10 个 Segment，完全重复段落为 0，但有
1 次 filler 和 4 次“不是……而是……”。同模型 Reviewer 六维全部 5/5，
实验综合分 83.2；这恰好说明自评只能 advisory，应补充真实素材而不是灌水。

前一次真实尝试被 Editor 严格合同
`podcast_draft_missing_supplemental_source_reference` 拒绝，增强末尾引用自检
后本次通过。前一次约 CNY 0.039696 是单独的开发调试估算费用，不并入成功
Run。官方账单可能因计费口径、缓存和同步延迟与本地估算不同。

M3.2 的 2026-07-29 合成素材 live
Run 使用 16,667 input tokens、9,468 output tokens、73,018 ms Provider
耗时，本地估算 CNY 0.035603；估算不是厂商账单，内容仍需人工审核。
上一阶段的真实验收
使用三份完整初始素材和一份补充口述，成功走到
`waiting_for_user -> Resume -> succeeded`；第一次容量失败、修复过程、Token、
费用、日志与内容质量复核记录在
[真实用户路径验收章节](m3-1-realistic-e2e.zh-CN.md)。

## 当前还不能做什么

- DeepSeek 真实 API 已用完整合成素材评价脚手架质量，但尚未使用个人隐私素材；
- 已能生成带引用的播客候选稿，但仍需本人审核事实、语气和取舍；
- 已能生成 Draft Quality Report，但它仍是辅助审稿，不代表真实录制反馈；
- 已加入证据化模型自评，但默认可能是同模型 self-review，只能作为 advisory；
- 中文启发式不能输出“AI 概率”，阈值也尚未用真实用户录音校准；
- `must_include` 仍不能由普通代码可靠识别同义改写；
- Flash 与 Pro 属于同一 DeepSeek 家族，不等于跨家族独立裁判；
- 已用完整合成 persona、Writing Sample 与 DeepSeek 跑通 Revision；仍未用本人
  私有 Writing Sample 做“像不像我、愿不愿意录”的真人复核；
- M3.7b/c 的首个真人盲评 pair 因区分度不足不能证明 Sample 有效；M3.7d 的
  真实量级合成人设已产生可区分候选和方向性正信号，但合成评审仍不能代表
  真实用户私有 Sample 的“像不像我、愿不愿意录”；
- comparison 只给出差异证据，不会替用户选择最终稿；
- M3.8 只能识别口播正文完全未引用的 Segment，尚不能识别已引用但展开不足；
  真实 DeepSeek 时长恢复已完成但仍短，真人可录性验收尚未完成；
- M3.9 已用真实 DeepSeek 围绕最新稿生成问题，并由 Playwright 提交四段合成回答
  跑通 child Revision；问题与回答仍是合成验收，当前真实口述仍需先转成文字；
- Source Segment 还没有结构化 `material_kind`，当前只能在成稿侧检测明显的
  editorial instruction 泄漏；
- M5.1 起步候选仍需用户逐句核对；有界 repair 与 `server_line_grounding` 可以
  保留安全主题线索，但不提供带网页引用的领域研究，也不能把模型猜测直接当事实
  Source。`writing_sample` 和 `voice_note_transcript` 不允许使用这条生成通道；
- 已能在本地 Console 查看采访脚手架和播客稿，但尚未提供可视化编辑器；
- M3.2 的 Editor 已通过合成素材真实调用，但尚未使用个人隐私素材验收；
- 尚未提供麦克风录音、音频上传、STT 或语音克隆；
- 已提供本地 Project/Source 与 Run Trace Console，但还不是带登录、权限和
  多人协作的线上 Web 产品；
- SSE Trace 已能重放、实时追踪、heartbeat 并在终态关闭；断线后仍以 SQLite
  和 HTTP replay 为真相；
- 尚未部署到线上。

当前用于人工验证的主要界面是本地 React Console；FastAPI Swagger 继续作为
精确 API 调试入口。两者都不等于线上部署已经完成。

## 每个后续步骤如何记录

从下一步开始，每个开发切片必须更新对应章节，或按
[学习记录模板](entry-template.zh-CN.md) 新建章节。每一条记录至少包含：

1. 问题与目标；
2. 非技术类比；
3. 完成的功能；
4. 代码模块地图；
5. 技术原理；
6. 自动化测试；
7. 本地手动验证；
8. 日志与排错入口；
9. 取舍、限制和未完成项；
10. commit 与下一步。

学习记录应与实现处于同一个 commit，避免文档永远落后于代码。
