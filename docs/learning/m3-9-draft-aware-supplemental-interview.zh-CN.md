# M3.9：根据最新稿定向追问，再用回答继续修订

状态：完成（2026-07-31）

## 1. 为什么需要这一步

M3.8 已经解决了第一层问题：稿子偏短时，先检查并利用已有但尚未进入口播正文的
事实，不要立刻让用户重复讲一遍。

但一次有来源的 Revision 以后，仍可能出现：

- 稿子从 4.7 分钟增长到 8.5 分钟；
- 已有高价值素材已经被合理使用；
- 15 分钟目标的可接受下限仍是 12.75 分钟；
- 继续只用原素材扩写会产生重复、套话或虚构。

这时系统不应该只显示“请补充更多素材”。它已经看过最新稿，应该指出：

> 哪一句写得太快？缺的是现场、动作、原话、感受，还是前后变化？

然后围绕这句具体行文提问，让用户更容易想起新内容。

## 2. M3.9 完成后的产品闭环

```text
父 Draft 低于目标时长下限
  -> 用户显式选择 reuse_unused_material
  -> Revision Editor 使用仍有价值的既有事实
  -> Reviewer + 确定性质量规则重新检查
  -> 若仍低于 85% 下限
  -> Supplemental Interviewer 阅读最新口播正文
  -> 持久化 3—6 个绑定具体原句的问题
  -> Run 成功结束，父稿和本轮候选稿都不再改变
  -> 用户回答问题，把回答文字导入为新 Source
  -> 用户显式创建下一版 Revision
  -> Editor 优先融合本轮新增 Source，并保留父稿有效内容
  -> 再次 Reviewer + 质量检查
  -> 达标则停止；仍短则最多再追问一轮
```

这里没有隐藏的自动改稿循环。每一次新增人生事实都必须来自用户新提交的 Source；
每一次新稿也都必须来自一次显式 Revision 请求。

## 3. 为什么 Planner 不等于 Reviewer

三个 Agent 的职责不同：

| 角色 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Editor | 根据允许的 Source 生成完整候选稿 | 判断用户是否应该再提供事实 |
| Reviewer | 评价稿子的质量，并给出证据化建议 | 发明新事实或自动触发 Revision |
| Supplemental Interviewer | 把最新稿中的具体缺口变成可回答的问题 | 直接改稿、替用户回答 |

把追问塞进 Reviewer 会混淆“判断”和“采集材料”。独立 Planner Task 可以单独
记录模型调用、重试、失败回退、问题产物和轮次。

## 4. “根据具体行文提问”怎样由代码保证

只在 Prompt 里写“请具体提问”不够。模型可能仍返回泛泛的：

> 能再展开讲讲吗？

M3.9 先由可信代码从**最新 Draft 的口播正文**建立最多 24 个 Anchor：

- `podcast_script.opening`
- 每个 section 的 paragraph
- `podcast_script.closing`

Show Notes 和 Section 标题不进入 Anchor。每个 Anchor 保存：

- 最新 Draft Artifact ID；
- 稳定 paragraph path；
- section 标题；
- 从该段逐字截取的 excerpt；
- 该段已经验证过的 Source references。

模型每个问题必须返回：

- 一个允许的 `anchor_id`；
- 从对应 excerpt 逐字复制的 `anchor_quote`；
- 开放式 `prompt`；
- 追问目的和 detail type；
- 2—4 个回答抓手；
- 预计可增加的字符数。

Worker 校验 `anchor_quote` 确实来自这份最新稿。模型不能伪造旧稿路径，也不能把
Show Notes 当成口播正文。持久化时，代码再注入 `q1`—`q6`，模型不能自造
question ID。

## 5. 如何避免问题预设不存在的事实

问题必须允许用户回答：

- “没有”；
- “不记得”；
- “当时还没有想清楚”。

例如稿子只写“我重新点开了录音”，系统不能直接问：

> 你为什么哭了？

它可以问：

> 重新点开录音时，你有没有明显的身体或情绪反应？如果没有，也可以直接说没有。

Prompt 负责语义约束；Anchor、逐字 quote、严格 JSON Schema 和人工复核共同提供
工程边界。代码不能完全理解所有中文事实预设，因此这仍不是百分之百的语义证明。

## 6. 为什么 Run 成功，而不是再次进入 waiting_for_user

本轮 Revision 已经生成了一份有效 Draft 和 Quality Report。问题计划只是下一步建议，
不应把已经完成的候选稿改回“运行中”。

因此 M3.9 的状态是：

```text
Revision Run = succeeded
output_artifact_id = 最新 Draft
SupplementalInterviewPlan.status = awaiting_user
```

用户回答以后创建的是新的 child Revision Run。这样每一版稿子、评分和问题都不可变，
父子 lineage 也能完整回放。

## 7. 数据保存在哪里

M3.9 不新增数据库表，也不需要 Alembic migration。

| 数据 | 保存位置 | 用途 |
| --- | --- | --- |
| 最新候选稿 | `artifacts` | 下一轮问题只能绑定它 |
| 质量报告 | `artifacts` | 提供时长缺口和质量 focus |
| Planner 工作 | `tasks` | 重试、失败、租约和状态 |
| 模型调用 | `model_calls` | Provider、Token、延迟、费用 |
| 问题计划 | `artifacts` | 3—6 个问题及可信 Anchor |
| 用户回答 | `sources` / `source_segments` | 新的人生事实通道 |
| 回答与问题关系 | `draft_revision_request` Artifact | Plan ID、question IDs、Source IDs |
| 轮次 | child Run input | 服务端推导的 0 / 1 / 2 |
| 执行轨迹 | `events` + JSON logs | 调试与未来 Trace UI |

不能只把 question ID 写进 Source metadata。相同正文可能因为内容哈希去重而复用旧
Source，metadata 不能可靠表达每一次 Revision 的选择。真正的关联保存在 versioned
Revision Request Artifact。

## 8. API 怎样操作

### 8.1 读取已生成的问题

```http
GET /runs/{run_id}/supplemental-interview-plan
```

这个 GET 只读取已提交 Artifact，不排队 Task、不调用模型、不产生费用。没有问题计划、
Run 尚未完成或计划不属于最新 Draft 时返回 `409`。

### 8.2 把回答导入为 Source

```http
POST /sources
Content-Type: application/json

{
  "title": "第一轮定向补充口述",
  "source_type": "voice_note_transcript",
  "text": "我看到旧录音文件时正坐在……"
}
```

当前仍是“已经转成文字的口述”。麦克风权限、录音上传和 STT 属于后续 UI/语音阶段。

### 8.3 用回答创建下一版 Revision

```http
POST /runs/{run_id}/revisions
Content-Type: application/json

{
  "version": "draft_revision_request_v2_supplemental_interview",
  "submission_id": "answer-round-1",
  "selected_actions": ["add_supplemental_material"],
  "selected_feedback_artifact_ids": [],
  "selected_gap_codes": [],
  "source_ids": ["src_回答SourceID"],
  "supplemental_interview_plan_artifact_id": "art_问题计划ID",
  "answered_question_ids": ["q1", "q2", "q3"],
  "target_duration_minutes": null,
  "revision_instruction": "保留原稿有效内容，优先融合本轮新场景。"
}
```

后端会重新验证：

- Plan 属于这个 parent Run；
- Plan 指向 parent 的最新 Draft 和质量报告；
- question ID 确实存在；
- Source 是新事实材料；
- submission ID 重放时内容完全相同；
- 轮次没有超过 2。

客户端不能直接设置轮次。

## 9. 停止条件

系统不会无限追问。满足任意一项就停止自动规划：

1. 最新稿达到目标时长的 85% 下限；
2. 当前不是显式 Revision；
3. 本轮不是“复用旧素材”或“加入新补充素材”；
4. 已完成两轮 Supplemental Interview；
5. 最新 Draft 或 Quality Report provenance 无效。

到达两轮上限后，用户仍可主动添加一般素材或降低目标时长，但系统不会继续声称
“再回答一轮就一定够”。

## 10. Provider 失败时怎样处理

问题 Planner 是增益能力，不应该让已经完成的 Draft 丢失。

如果 DeepSeek 超时、重试耗尽或返回不合格 JSON：

- Planner Task 保留 `failed` 和错误码；
- 代码从三个不同正文 Anchor 生成保守问题；
- Plan 标记 `generation_mode = deterministic_fallback`；
- Run 仍以已验证 Draft 成功结束；
- API 允许读取这份有明确 provenance 的 fallback Plan。

Fallback 仍绑定最新原句，并明确允许用户回答“不记得/没有”。

## 11. Fake E2E 验证了什么

核心回归测试：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest \
  tests/test_revision_workflow.py::test_v9_draft_aware_questions_drive_two_bounded_answer_revisions \
  tests/test_revision_workflow.py::test_v9_planner_validation_failure_preserves_draft_with_fallback_plan \
  -vv
```

固定 Fake 场景得到：

```text
已有素材恢复后：2,509 字，仍低于 3,570 字下限
  -> Round 1 Plan：3 个最新稿锚定问题
第一轮回答 Revision：3,073 字，仍短
  -> Round 2 Plan：3 个不同角度问题
第二轮回答 Revision：3,637 字
  -> 达到 15 分钟目标的 85% 下限
  -> 不再创建 Planner Task
```

这条测试还验证：

- 重复 GET Plan 不增加 ModelCall；
- 没带 Plan 不能绕过已经生成的问题；
- 不存在的 question ID 被拒绝；
- 新 Source 真正增加口播正文，而不是让稿子重新变短；
- round 由 0 → 1 → 2 服务端推进；
- Planner 输出校验失败时 fallback Plan 仍可读取；
- 第二轮 fallback 会改问对白、感官和前后变化，不重复第一轮模板；
- 已经存在 Plan 时，不能再用 `reuse_unused_material` 绕过回答路径；
- 历史 v1 Revision 请求省略版本号时仍可幂等重放；
- 是否达到下限直接比较精确正文字符，不用四舍五入后的分钟数；
- 父 Run 和旧 Draft 没有被覆盖。

Fake 的 3,637 字只是确定性测试数据，不代表真实节目内容质量。真实 DeepSeek 仍需要
单独小额验收，真人仍需要判断“问题是否能触发回忆”和“最终稿是否愿意录”。

最终本地验证：

```text
354 backend tests passed
Ruff format check passed
Ruff lint passed
git diff --check passed
```

## 12. 关键模块

| 文件 | 作用 |
| --- | --- |
| `supplemental_interview_schemas.py` | Anchor、问题、Plan、严格校验和 fallback |
| `runtime/supplemental_interview_prompts.py` | 最新稿定向追问 Prompt |
| `runtime/orchestrator.py` | v9 Planner 排队、完成、失败降级和两轮停止 |
| `runtime/providers/fake.py` | 零费用问题与回答 Revision 行为 |
| `runtime/providers/deepseek.py` | 真实 Planner 模型调用 |
| `services.py` | Plan 读取、回答 provenance、轮次和 child Revision |
| `api.py` | Plan GET 与 Revision API |
| `revision_schemas.py` | v2 Revision Request、回答 Source 和新增材料契约 |

## 13. 本阶段没有做什么

- 没有麦克风录音和语音转文字；
- 没有问题填写 UI；
- 没有自动创建 child Revision；
- 没有无限 Agent loop；
- 没有 Claim-level “已引用但展开不足”的完整语义分析；
- 没有新的数据库或大框架；
- 没有在本次实现中发起新的付费 DeepSeek E2E。

下一步回到 M4：先做可回放 Trace/SSE，再用最小 Web UI 把 Draft、质量报告、问题、
Source 回答和 child Revision 的关系真正展示出来。
