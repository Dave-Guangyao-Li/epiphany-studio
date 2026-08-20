# M5.2：采访脚手架的人工文本编辑（人工编辑版 Artifact）

## 基本信息

- 阶段：M5.2（Scaffold 编辑第一步）
- 日期：2026-08-20
- Commit：见分支 `cursor/interview-scaffold-editing-2888` / 对应 PR
- 状态：已完成（仅文本编辑 + 导出接入；Editor 接入与结构编辑推迟）

## 1. 为什么做这一步

采访脚手架（Interview Scaffold）此前只能查看和导出。用户经常想微调开场白、
小节标题、过渡语或某个问题的措辞，让它更像自己会说的话。没有编辑入口，用户
只能在导出的 Markdown 里手动改，改动无法回到系统、也无法被后续流程使用。

## 2. 生活化类比

像给一份 AI 起草的采访提纲“批注改写”：你不会撕掉原稿，而是另存一份“我的改写
版”。原稿留档随时可对照，别人看到的是你最新的改写版。这里的“另存一份”就是一
条新的不可变 Artifact；不同的是，系统会强制你只能改文字，不能改动引用来源、
增删小节，也不能改标题（标题与本期主题绑定）。

## 3. 完成了什么

- 在 Run Trace 页新增“编辑采访脚手架”面板：可修改本期意图、开场白、各小节
  标题与过渡语、每个问题的 prompt 与追问目的、收尾文字。
- 保存会生成一条新的 `interview_scaffold_human_edit` Artifact（带
  `base_artifact_id`、`editor_origin=human`、`submission_id`）。原始
  `build_interview_scaffold_result` 永不改动。
- 导出 `interview-scaffold.md` 自动使用“最新人工版”；没有人工版时回退原始 AI 版。
- 保存前强制校验：所有来源引用、结构形状和标题必须与原始版本一致，人工只能改
  文字。改动引用、结构或标题会被拒绝。
- 保存用 `submission_id` 幂等：同 ID 同内容为重放，同 ID 不同内容返回冲突。

## 4. 代码模块地图

| 文件或目录 | 作用 | 为什么放在这里 |
| --- | --- | --- |
| `docs/adr/0002-human-edited-scaffold-artifact.zh-CN.md` | 记录“人工编辑版 Artifact”取舍 | 引入新领域概念，按原则 8 需 ADR |
| `backend/src/epiphany/scaffold_edit_schemas.py` | 编辑请求/编辑版 schema + grounding 校验 | 与 schema 层其它契约并列 |
| `backend/src/epiphany/services.py` | `save_interview_scaffold_edit` + 生效脚手架解析 + 导出接入 | Run 级业务逻辑集中处 |
| `backend/src/epiphany/api.py` | `POST /runs/{id}/interview-scaffold-edits` | HTTP 边界 |
| `frontend/src/features/runs/ScaffoldEditor.tsx` | 可编辑脚手架面板 | Run Trace 功能组件 |
| `frontend/src/features/runs/RunTracePage.tsx` | 挂载编辑面板 + 保存后刷新 | 页面装配 |

## 5. 背后的技术点

- **不可变 + 候选不覆盖**（原则 2/5）：编辑写新 Artifact，不改原稿；“生效脚手架”
  = 最新人工版或回退原始版。复用 `artifacts` 表与 `idempotency_key`，无 migration。
- **Grounding 保持**（原则 4）：`scaffold_grounding_signature` 抽取标题 + 所有
  `source_refs`（有序）+ 结构形状；编辑版与原版签名必须相等，否则拒绝。这样人工
  编辑无法悄悄改引用、增删小节或改标题，导出仍能解析出 `[S]` 索引。
- **数据流**：前端从 `run.artifacts` 取原始脚手架与最新人工版，算出“生效”内容填表；
  保存时把完整脚手架（未暴露字段原样带上）+ `base_artifact_id` + `submission_id`
  发给后端；后端校验后落库并发 `workflow.interview_scaffold.human_edited` 事件。

## 6. 自动化测试

- 后端 `backend/tests/test_interview_scaffold_edit.py`：
  - 正常：保存创建人工版、原稿不变、导出反映编辑；
  - 失败：改引用、加引用、改标题被拒；`base_artifact_id` 不匹配被拒；
  - 幂等：同 ID 同内容重放、同 ID 不同内容冲突。
- 前端 `frontend/tests/scaffoldEditor.test.tsx`：渲染并回填、保存请求形状（保留
  引用与结构）、存在人工版时显示编辑版与徽标、无脚手架时不渲染。
- 运行：`ruff check`、`alembic check`（无 drift）、后端全套通过（1 个既有并发
  测试 `test_invalid_citation_fails_parent_and_fences_late_sibling` 偶发 flaky，
  与本改动无关，隔离重跑通过）；前端 `vitest` 46 通过、`npm run build` 通过。

## 7. 本地手动验证

1. 后端 `alembic upgrade head` 后 `uvicorn epiphany.main:app`；前端 `npm run dev`。
2. 打开一个已经生成脚手架的 Run（等待补充或已完成均可）。
3. 在“编辑采访脚手架”面板改开场白/某个问题，点“保存人工编辑版”。
4. 点“采访脚手架”导出，确认 Markdown 反映了你的改写；再看 Run，出现
   `interview_scaffold_human_edit` Artifact，且原始脚手架 Artifact 未变。

## 8. 日志与排错

- 事件：`workflow.interview_scaffold.human_edited`（含 `edit_artifact_id`、
  `base_artifact_id`、`submission_id`）。
- 导出日志 `run.interview_scaffold_markdown.exported` 增加 `scaffold_edited` 标记。
- 常见错误：改了引用/结构/标题 → 422 `interview_scaffold_edit_grounding_changed`；
  `base_artifact_id` 不匹配 → 422；同 ID 不同内容 → 409。日志只含 ID，不含正文。

## 9. 这一步学到了什么

- 用“签名比对”把“只能改文字、不能改 grounding/结构”变成一条可测试的硬约束，
  比逐字段白名单更简洁且不易漏。
- append-only + provenance + 生效解析（最新版或回退）是一种通用、可回放的
  “可编辑但不可覆盖”模式，可迁移到其它需要人工修订的产物。

## 10. 限制与下一步

- **仅文本编辑**：不支持增删小节/问题、拖拽排序、富文本；不编辑 `known_context`、
  `keywords`、`material_gaps`（保存时原样带上）。
- **仅接入导出**：Resume → Editor 仍使用原始脚手架。要让人工编辑影响生成的
  Draft，需同时更新 Improvement Plan / Material Readiness 里“Editor 输入必须与
  持久化脚手架一致”的校验，属更大改动，单独切片处理（见 ADR-0002）。
- Draft（口播稿）的段落级人工编辑是后续 Draft 编辑切片。

## 完成检查

- [x] 正常路径测试通过
- [x] 失败路径测试通过
- [x] 本地手动验证通过
- [x] 日志中无隐私内容
- [x] README / Roadmap / Devlog 已同步
- [x] 学习手册已同步
- [x] 已创建 focused commit
