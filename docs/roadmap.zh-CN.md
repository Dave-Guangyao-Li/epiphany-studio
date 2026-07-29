# MVP 路线图

更新时间：2026-07-29

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

### M3.4：Draft Quality Report 与用户反馈（下一步）

- [ ] 确定性时长、重复、引用与模板化表达检查
- [ ] 严格 Schema 的独立模型评价 Task
- [ ] 每项评价提供 Draft 位置和逐字证据
- [ ] 代码计算最终 decision，模型不能覆盖硬性 blocker
- [ ] 模型评价与真实用户反馈分开保存

模型自评属于 M3.4，不进入 M3.3 的素材充足度门槛。首版即使使用与 Editor
相同的模型，也必须明确标记为 self-review 和 advisory；它不能冒充人工评价，
也不能输出一个不可解释的“AI 概率”。自动 E2E 可以提交
`synthetic_test` 反馈验证接口，但产品指标不得把它计为真实用户认可。

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
