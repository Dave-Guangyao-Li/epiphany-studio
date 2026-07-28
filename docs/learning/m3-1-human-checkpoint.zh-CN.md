# M3.1：可持久化的人工暂停与恢复

## 基本信息

- 阶段：M3.1 Durable Human Checkpoint
- 日期：2026-07-28
- Commit：本章节与实现处于同一个 focused commit
- 状态：Fake 默认零费用、暂停/恢复、幂等、重启恢复和自动化测试已完成

## 一句话理解

系统生成采访脚手架后不再假装“整期节目已经完成”，而是把 Run 安全地停在
`waiting_for_user`。用户可以阅读并导出脚手架，再把补充口述的**文字**导入
为新 Source，最后用这些 Source ID 恢复 Run。

这里的“口述文字”是已经存在的一段文字：

- 可以直接在 Swagger 文本框里输入；
- 可以从备忘录或聊天记录复制粘贴；
- 也可以由项目外的录音转文字工具先转好，再粘贴进来。

本阶段**不会**申请麦克风权限，也没有实时录音、音频上传、语音识别
（STT / ASR）或语音克隆。`source_type="voice_note_transcript"` 只是说明
“这段文字原本来自口述”的分类标签，不会触发任何音频功能。

## 1. 为什么做这一步

M2.4 已经能生成采访脚手架，但旧流程生成后马上把 Run 标为 `succeeded`。
这会造成一种错误印象：好像 Agent 已经独立完成工作，用户不需要再参与。

真实的播客创作不是这样。脚手架的作用是帮助本人回忆和表达，例如：

> 你第一次重新听见五年前的声音时，具体是什么感觉？

用户看见这个问题以后，可能会补充一段新的经历。系统需要做到：

1. 明确知道当前正在等人，而不是等 Worker；
2. 即使服务重启，等待状态和脚手架也不能丢；
3. 补充材料继续作为可追溯 Source 保存；
4. 网络重试不能重复接收同一份材料；
5. 运行历史要能解释“何时暂停、提交了什么引用、何时恢复”；
6. Event 和日志不能复制用户的口述正文。

M3.1 先把这个可靠的人工边界做实，再在 M3.2 接真正的 Editor Agent。

## 2. 生活化类比

可以把当前流程想成纪录片团队准备采访。

研究员和主持人先完成问题清单，然后把文件放到桌上，状态改成“等待受访者
补充”。这时没有工作人员在后台继续忙，也没有定时器偷偷运行。

受访者后来交来一份补充材料。前台先登记：

- 这是哪一次提交；
- 它属于哪个等待点；
- 对应哪几份材料；
- 材料被分成了哪些段落。

登记完成后，流程才从“等待”恢复。

这个类比和当前实现有一个重要差别：M3.1 的“恢复”只证明材料被可靠接收，
随后便确定性结束 Run。它还不会把补充材料交给新的编辑人员生成播客稿；
那是 M3.2 的工作。

## 3. 完成了什么

### 3.1 新 Run 会停在真正的人工检查点

新建的 `episode-research` 使用 `workflow_version="v3"`：

```text
导入初始 Source
  -> 创建 episode-research v3 Run
  -> Timeline / Theme 两个 Researcher 并行
  -> fan-in 合并 Research Bundle
  -> Interviewer 串行生成 Interview Scaffold
  -> Run status = waiting_for_user
  -> current_step = awaiting_interview_response
```

此时可以观察到：

| 项目 | 等待时数量或状态 |
| --- | --- |
| Run | `waiting_for_user` |
| current_step | `awaiting_interview_response` |
| Task | 4 个，而且都不再是 queued / running |
| Artifact | 4 个 |
| ModelCall | 3 个 |
| output_artifact_id | 指向 `build_interview_scaffold_result` |

等待不是进程内的一次 `sleep`。状态、脚手架和历史已经写入 SQLite，所以关掉
服务再启动，Run 仍然处于同一个检查点。Worker 此时没有可领取的 Task。

### 3.2 等待期间仍可阅读和导出脚手架

下面的导出 API 同时接受 `waiting_for_user` 和 `succeeded` Run：

```text
GET /runs/{run_id}/exports/interview-scaffold.md
```

用户不必先 Resume，便可以下载问题清单、离线回答或把它放在录音时旁边。

### 3.3 补充口述继续使用 Source 契约

补充内容先通过现有 API 导入：

```text
POST /sources
```

例如：

```json
{
  "title": "EP0 第一轮口述补充",
  "source_type": "voice_note_transcript",
  "text": "重新听见五年前的声音时，我意识到声音保存的不只是内容，还有当时的语气。",
  "metadata": {
    "round": 1
  }
}
```

`text` 才是用户补充的正文。系统会像处理日记和旧稿一样保存 Source，并切成
可稳定引用的 SourceSegment。Resume API 不接受一大段裸文本，只接受这些
已经入库的 Source ID。

这样做的好处是：

- 原始材料只有一个正式入口；
- 后续 Agent 可以引用到具体段落；
- Artifact 和 Event 不需要反复复制正文；
- 将来换成上传音频加 STT 时，也可以在转写完成后复用同一个 Source 契约。

### 3.4 Resume API 接收引用并结束 M3.1

恢复 API：

```text
POST /runs/{run_id}/resume
```

请求示例：

```json
{
  "checkpoint": "interview_scaffold",
  "submission_id": "ep0-round-1",
  "source_ids": [
    "src_替换成补充Source的ID"
  ]
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `checkpoint` | 当前只允许固定值 `interview_scaffold` |
| `submission_id` | 调用方为这次提交选择的稳定 ID，网络重试时保持不变 |
| `source_ids` | 1 至 20 个已经存在且不重复的 Source ID |

第一次成功响应的关键字段：

```json
{
  "resumed": true,
  "idempotent_replay": false,
  "submission_artifact_id": "art_...",
  "run": {
    "status": "succeeded",
    "current_step": "complete",
    "model_call_count": 3
  }
}
```

数据库新增一个 `user_material_submission` Artifact。它保存：

- checkpoint；
- submission ID；
- 原脚手架 Artifact ID；
- Source ID；
- SourceSegment 引用。

它**不复制补充口述正文**。正文仍只从 Source / SourceSegment 获取。

M3.1 的状态变化是：

```text
waiting_for_user
  -> running / accepting_user_material
  -> succeeded / complete
```

这两个后续变化发生在同一次 Resume 数据库事务内。因此正常 HTTP 调用完成后
看到的是 `succeeded`；需要通过 Events 才能回放中间的 `run.resumed`。

Resume 后：

| 项目 | 结果 |
| --- | --- |
| Task | 仍是 4 个 |
| Artifact | 从 4 个变成 5 个 |
| ModelCall | 仍是 3 个 |
| output_artifact_id | 仍指向采访脚手架 |

也就是说，这一步没有排队新 Task、没有调用新模型、没有新增 Token 或费用，
也没有把补充内容变成播客成稿。

## 4. 状态、Artifact 和 Event 怎样配合

三种数据分别回答不同问题：

| 数据 | 回答的问题 |
| --- | --- |
| Run 状态 | 现在整个流程停在哪里 |
| Artifact | 这一步稳定地产出了什么 |
| Event | 这些变化按什么顺序发生 |

生成脚手架后的关键 Event 顺序：

```text
workflow.interview_scaffold.completed
workflow.user_input.requested
run.waiting_for_user
```

此时不应出现 `run.succeeded`。

接受补充材料后的 Event 顺序：

```text
run.resumed
workflow.user_material.accepted
run.succeeded
```

`workflow.user_material.accepted` 只记录 Artifact ID、Source 数量和 Segment
数量，不记录补充口述正文。

等待中的 Run 也可以通过 `POST /runs/{run_id}/cancel` 取消。取消后状态为
`cancelled`，再次 Resume 会得到 409，不能让已经终止的 Run 复活。

## 5. 幂等为什么重要

用户点击提交后，浏览器可能因为网络超时没有收到响应，于是自动重试。如果
每次请求都创建新 Artifact，系统可能把同一份回答当成两轮采访。

M3.1 使用以下三项组成一次稳定提交：

```text
run_id + checkpoint + submission_id
```

系统据此生成 Artifact 的 idempotency key。

### 相同请求重放

使用完全相同的 `submission_id` 和 `source_ids` 再发一次：

```json
{
  "resumed": false,
  "idempotent_replay": true,
  "submission_artifact_id": "与第一次相同"
}
```

它返回 200，但不会新增 Artifact、Event 或模型调用。

### 相同 ID 对应不同材料

如果 `submission_id` 相同，却换成另一组 `source_ids`，系统返回 409：

```text
submission_id was already used with different material
```

这比静默覆盖安全，因为调用方必须明确选择新的 submission ID。

当前 MVP 是单进程运行。一个短暂的 `asyncio.Lock` 让两个同时到达的 Resume
请求按顺序处理；Artifact 唯一幂等键是写入 SQLite 的持久化重复保护。
将来升级为多进程或多机器时，不能只依赖进程内 Lock：唯一约束仍能阻止
重复数据，但当前 loser 会暴露数据库 `IntegrityError`，还不能得到友好的
replay / 409。部署前需要使用数据库事务锁、compare-and-set，或捕获冲突后
回读已有提交。

## 6. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `backend/src/epiphany/state_machine.py` | 允许 Run 在 running、waiting 和终止状态之间进行合法转换 |
| `backend/src/epiphany/runtime/orchestrator.py` | v3 生成脚手架后写入等待状态和等待 Event |
| `backend/src/epiphany/human_input_schemas.py` | 定义严格的 Resume 请求；拒绝空值、重复 ID、未知字段和未知 checkpoint |
| `backend/src/epiphany/services.py` | 校验检查点、读取 SourceSegment、创建 submission Artifact、处理幂等与恢复 |
| `backend/src/epiphany/api.py` | 提供 `POST /runs/{run_id}/resume`，把错误映射为 404 / 409 / 422 |
| `backend/src/epiphany/schemas.py` | 定义包含 Run 全貌的 Resume 响应 |
| `backend/src/epiphany/observability.py` | 允许记录安全的 checkpoint、数量和幂等元数据 |
| `backend/tests/test_human_checkpoint_api.py` | 测试完整路径、重启、并发、隐私、取消和失败响应 |
| `backend/tests/test_human_input_schemas.py` | 测试 Resume 输入边界 |

M3.1 复用 Run、Artifact、Event、Source 和 SourceSegment 表，没有新增数据库
字段，因此 Alembic 仍是 `0003_model_call_trace (head)`，不需要新 migration。

## 7. 自动化测试

在 `backend` 目录运行：

```bash
source .venv/bin/activate
pytest tests/test_human_input_schemas.py \
       tests/test_human_checkpoint_api.py \
       tests/test_research_workflow.py \
       tests/test_interview_export_api.py -vv
```

覆盖的关键行为包括：

- v3 在脚手架完成后进入 `waiting_for_user`；
- 等待时没有 queued / running Task；
- 等待时仍可导出 Markdown；
- 补充 Source 被接收并只以引用进入 submission Artifact；
- Run 恢复后 4 Tasks、5 Artifacts、3 ModelCalls；
- 相同提交重放只应用一次；
- 同一 submission ID 对应不同材料返回 409；
- Run 或 Source 不存在返回 404；
- 空列表、空 ID、重复 ID、未知字段或 checkpoint 返回 422；
- 服务重启后仍然等待，之后可以 Resume；
- 两个并发相同请求只创建一个 submission Artifact；
- 等待中的 Run 可以取消，取消后不能 Resume；
- Resume 与 Cancel 并发时只有一个终态能成功；
- 日志中找不到补充口述正文。

完整验证：

```bash
pytest -q
ruff check src tests
ruff format --check src tests
alembic current
alembic check
```

当前全量基线是：

```text
120 passed
```

这些测试默认使用 Fake Provider，不联网、不读取 DeepSeek Key，也不产生模型
API 费用。M3.1 的 Resume 本身无论使用哪种 Provider 都不会新增模型调用。

除逐步 Swagger 验证外，项目还提供一套带合成素材的自动 E2E 命令，能够
自动走完 Source 导入、Run、等待、Markdown 导出、补充 Source、Resume 和
幂等重放，并保存数据库、JSONL 日志与机器可读报告。操作与真实 DeepSeek
尝试结果见
[M3.1 后端 / API 全流程 E2E](m3-1-backend-e2e.zh-CN.md)。

## 8. Swagger 手动验证

### 第一步：启动服务

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
alembic upgrade head
uvicorn epiphany.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>。

### 第二步：导入初始素材并创建 Run

用 `POST /sources` 导入一段不含隐私的合成文字，复制返回的 `source.id`。
再用 `POST /runs` 创建 `episode-research` Run：

```json
{
  "workflow_type": "episode-research",
  "payload": {
    "topic": "五年后重新开始录播客",
    "source_ids": [
      "src_初始素材ID"
    ]
  }
}
```

复制 `run.id`。

### 第三步：确认系统真的暂停

调用 `GET /runs/{run_id}`。成功标志：

- `workflow_version` 是 `v3`；
- `status` 是 `waiting_for_user`；
- `current_step` 是 `awaiting_interview_response`；
- 有 4 个 Task、4 个 Artifact 和 3 个 ModelCall；
- 所有 Task 都不是 queued / running；
- `output_artifact_id` 指向 `build_interview_scaffold_result`。

调用 `GET /runs/{run_id}/events`，最后三个工作流级事件应为：

```text
workflow.interview_scaffold.completed
workflow.user_input.requested
run.waiting_for_user
```

再调用导出 API，等待状态应该也返回 200。

### 第四步：输入补充口述文字

再次使用 `POST /sources`。直接在 `text` 中输入或粘贴文字：

```json
{
  "title": "EP0 第一轮口述补充",
  "source_type": "voice_note_transcript",
  "text": "这里填已经转成文字的补充口述，不是上传音频。",
  "metadata": {
    "round": 1,
    "purpose": "manual_test"
  }
}
```

复制这次返回的新 `source.id`。

### 第五步：Resume

调用 `POST /runs/{run_id}/resume`：

```json
{
  "checkpoint": "interview_scaffold",
  "submission_id": "manual-round-1",
  "source_ids": [
    "src_补充素材ID"
  ]
}
```

第一次应看到：

```text
resumed = true
idempotent_replay = false
run.status = succeeded
run.current_step = complete
```

Run 应有 5 个 Artifact，新增类型为 `user_material_submission`；Task 和
ModelCall 数量不变，`output_artifact_id` 仍指向脚手架。

### 第六步：验证网络重试不会重复写入

保持 `submission_id` 和 `source_ids` 完全不变，再 Execute 一次。

应看到：

```text
resumed = false
idempotent_replay = true
submission_artifact_id 与第一次相同
```

再次查询 Run 和 Events，不应多出第二个 submission Artifact，也不应多出一组
`run.resumed` / `workflow.user_material.accepted`。

## 9. 日志与排错

重点结构化日志事件：

```text
run.waiting_for_user
run.resume.accepted
run.resume.idempotent_replay
run.resume.rejected
```

重点持久化 Event：

```text
workflow.user_input.requested
run.waiting_for_user
run.resumed
workflow.user_material.accepted
run.succeeded
```

排查时保留：

- HTTP 响应的 `X-Request-ID`；
- `run_id`；
- `submission_artifact_id`；
- 补充 Source ID；
- HTTP 状态码和 `detail`。

常见结果：

| 状态码 | 含义 |
| --- | --- |
| 200 | 首次接收成功，或相同提交的安全重放 |
| 404 | Run 或某个 Source ID 不存在 |
| 409 | Run 不在正确检查点、已取消/终止，或 submission ID 发生冲突 |
| 422 | 请求字段、checkpoint、ID 或 Source 数量不符合 Schema |

不要把 API Key、日记正文、口述全文或 SourceSegment 文本复制进日志和 Event。
当前本地 SQLite 会保存 Source 正文，它不是加密保险箱，也还没有用户鉴权；
个人真实素材应只放在被 `.gitignore` 排除的本地数据库，不要提交到 GitHub。

## 10. 这一步学到了什么

### “等待用户”也是正式运行状态

人工参与不是流程外的备注。只要等待可能持续几分钟、几天，甚至跨过服务
重启，它就必须成为数据库中的一等状态。

### Resume 不等于从头再跑

恢复的目标是从保存好的检查点继续，而不是重做三次模型调用。M3.1 通过
持久化 Run、Artifact 和 Event 证明这一点。

### 幂等不是性能优化，而是正确性

用户很难判断超时请求到底成功没有。稳定 submission ID 让“再试一次”不会
变成“再执行一次”。

### 正文与引用应分开

Source 保存原文，Artifact 保存结构和引用，Event / 日志保存运行事实。这能
减少隐私内容到处复制，也让数据职责更清楚。

## 11. 当前非目标与下一步

M3.1 还没有：

- 麦克风权限或浏览器录音；
- 音频文件上传；
- STT / ASR 语音转文字；
- 语音克隆或生成；
- 在网页上逐题填写的采访 Editor；
- 根据补充 Source 运行新的 Editor Task；
- 生成播客成稿、Show Notes 或配乐；
- 多进程 / 多机器 Resume 锁；
- 用户账号、权限控制或数据库内容加密；
- 正式 Web UI。

下一步 M3.2 应把当前的确定性“接受材料后成功结束”，替换成真正的 Editor
Task：读取采访脚手架和用户补充 Source，在不丢失来源引用的前提下生成下一
个可审阅 Artifact。

## 完成检查

- [x] v3 Run 能持久化进入 `waiting_for_user`
- [x] 等待时可以查看和导出采访脚手架
- [x] 补充口述文字通过 Source 导入
- [x] Resume 只接受已入库 Source ID
- [x] 相同请求可幂等重放，冲突请求被拒绝
- [x] 重启、并发、取消和失败路径有测试
- [x] 没有新增 Task、ModelCall 或 API 费用
- [x] 日志和 submission Artifact 不复制补充正文
- [x] 无新 migration，Alembic 无差异
- [x] 麦克风、STT 和 Editor 等非目标已明确
