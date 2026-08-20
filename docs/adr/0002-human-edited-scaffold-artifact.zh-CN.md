# ADR-0002：采访脚手架的人工编辑存为不可变的“人工编辑版” Artifact

日期：2026-08-20

状态：Accepted

## 背景

Interview Scaffold（采访脚手架）目前只能查看和导出，用户无法在 Console 里
修改。M5 计划把它做成可编辑。但仓库有两条核心工程原则会被“直接可编辑”破坏：

- 原则 2：数据库为准；Alembic 管 schema；应用不静默改写。
- 原则 5：Agent 只提交候选，不静默覆盖已批准内容。

Scaffold 是模型生成的、带来源引用（SourceReference）的结构化 Artifact，
而现有 Artifact 表本身是 append-only、带 `idempotency_key` 的不可变记录。
直接就地修改 Scaffold Artifact 会：破坏可回放性、丢失“模型原始产物 vs 人工
修改”的区分、并可能让人工编辑意外改动来源引用从而破坏 grounding
（原则 4：每条派生事实必须保留来源片段引用）。

## 决策

采用“人工编辑版 Artifact”方案：

- 用户的每次保存写入一条**新的、不可变的** Artifact，`kind =
  interview_scaffold_human_edit`，内容包含被编辑后的完整 Scaffold、指向原始
  AI Scaffold 的 `base_artifact_id`、`editor_origin = "human"` 与
  `submission_id`。
- 原始 `build_interview_scaffold_result` Artifact **永不改动**。
- 同一 Run 的“生效脚手架”定义为：若存在人工编辑版，取**最新一条**；否则取
  原始 AI 版本。
- 导出 `interview-scaffold.md` 使用生效脚手架。
- 保存前做**来源引用与结构保持**校验：编辑版的所有 `source_refs`、章节/问题/
  背景/缺口的数量与位置，以及标题（必须仍等于 topic），都必须与原始 AI 版本
  逐位一致。人工只能修改面向人的**文本字段**（开场/意图/收尾/小节标题/过渡/
  问题 prompt 与 purpose/关键词/背景与缺口文本），不能增删结构，也不能改动
  任何引用。
- 保存用 `submission_id` 做幂等：同 ID 同内容为重放，同 ID 不同内容为冲突，
  与既有反馈接口一致。

## 原因

- 满足不可变与“候选不覆盖”原则：模型原始产物与人工修改都可审计、可回放。
- 保护 grounding：引用与结构不可被人工编辑破坏，导出仍能解析出 `[S]` 索引。
- 复用现有 `artifacts` 表与 `idempotency_key`，**无需新增 migration**。
- 与既有 `draft_user_feedback` 的 append-only + provenance 模式一致，降低认知
  与实现成本。

## 本切片范围（第一步）

- 只做 **Interview Scaffold** 编辑；Draft 编辑作为后续。
- 只做 **文本编辑**：不支持增删章节/问题、拖拽排序、富文本或改动引用。
- 生效脚手架用于**导出**。

## 明确推迟（后续切片）

- 把生效（人工版）脚手架接入 Resume → Editor 输入。当前 Editor 输入用的是
  原始 Scaffold，而 Improvement Plan、Material Readiness 等会校验
  “Editor 输入的 scaffold 必须与持久化 Scaffold 一致”。要把人工版接入 Editor，
  必须同时更新这些一致性校验，属于更大改动，单独切片处理，以免本步引入回归。
- Draft（口播稿）的段落级人工编辑。

## 代价

- 生效脚手架需要一个解析步骤（取最新人工版或回退原始版），导出与未来 Editor
  接入都要走它。
- 暂时出现“导出用人工版、但 Editor 仍用原始版”的差异；已在上面显式记录为推迟
  项，并会在 UI/文档中说明，避免被误解为 bug。

## 复审条件

- 需要让人工编辑影响生成的 Draft（届时做 Editor 接入切片）。
- 需要支持结构性编辑（增删章节/问题）或段落级 Draft 编辑。
- 人工编辑版数量增多，需要显式的版本列表/回滚 UI。
