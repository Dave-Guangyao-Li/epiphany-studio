# MVP 路线图

更新时间：2026-07-23

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

- [ ] OpenAI Provider
- [ ] Timeline Researcher prompt
- [ ] Theme Researcher prompt
- [ ] 模型调用、tokens、延迟记录
- [ ] 调用上限与结构化输出校验

演示：使用少量真实素材完成两个 Researcher，Trace 中可见调用与成本数据。

### M2.4：采访脚手架

- [ ] Interview Scaffold schema/prompt
- [ ] 合并 Timeline 与 Theme Artifact
- [ ] 引用完整性校验
- [ ] Markdown 导出

演示：从真实文字素材生成带来源引用的采访脚手架。

M2 完成标准：以上四个小步全部通过测试和演示后，才进入 M3。

## M3：Human-in-the-loop

- [ ] `waiting_for_user`
- [ ] Resume API
- [ ] 用户新增素材
- [ ] Editor
- [ ] Markdown/Show Notes 导出

演示：Workflow 暂停，用户补充口述文字后继续生成可录初稿。

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
