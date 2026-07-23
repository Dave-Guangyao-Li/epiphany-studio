# MVP 路线图

更新时间：2026-07-23

路线图按可演示的纵向切片推进，不按“先把所有基础设施搭完”推进。

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

- [ ] Source/SourceSegment 导入
- [ ] OpenAI Provider
- [ ] Timeline Researcher schema/prompt
- [ ] Theme Researcher schema/prompt
- [ ] 并行 fan-out/fan-in
- [ ] Interview Scaffold schema/prompt
- [ ] 来源引用校验
- [ ] 模型调用、tokens、延迟记录

演示：从真实文字素材生成带来源引用的采访脚手架。

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
