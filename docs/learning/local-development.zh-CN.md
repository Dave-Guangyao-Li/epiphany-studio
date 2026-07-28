# 本地运行、测试与调试

这份文档回答四个问题：

1. 怎样把后端启动起来；
2. 当前没有正式前端时，去哪里操作；
3. 怎样证明功能真的工作；
4. 出错以后从哪里开始查。

## 1. 第一次安装

打开终端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
```

这些命令分别代表：

- `cd`：进入后端目录；
- `venv`：为项目创建隔离的 Python 环境；
- `source`：启用这个环境；
- `pip install`：安装运行和测试依赖；
- `alembic upgrade head`：把本地数据库结构升级到最新版。

以后重新打开终端，通常只需要：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
```

## 2. 启动后端

```bash
uvicorn epiphany.main:app --reload
```

终端保持运行，然后打开：

- Swagger 操作页面：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

`--reload` 表示修改代码后自动重启，适合本地开发。

## 3. 当前的“界面”是什么

正式 Web UI 计划在 M5 实现。当前使用 FastAPI 自动生成的 Swagger：

- 它能展示所有 API；
- 可以填写 JSON；
- 可以点击 Execute 发请求；
- 可以看到 HTTP 状态码、响应 JSON 和 Header。

它是开发调试界面，不是产品最终体验。

## 4. 手动走通 Source、人工暂停与 Resume

这一节使用的是 Swagger 里的文字输入框，不需要麦克风。补充“口述”是指
已经转成文字的内容；`voice_note_transcript` 只是 Source 分类，不会执行
录音或语音识别。

### 第一步：导入初始测试素材

在 Swagger 找到 `POST /sources`，点击 **Try it out**，填写：

```json
{
  "title": "本地测试素材",
  "source_type": "podcast_draft",
  "text": "2019年第一次记录项目。\n\n2024年重新整理了旧笔记。",
  "metadata": {
    "purpose": "manual_test"
  }
}
```

执行后复制返回的 `source.id`。

### 第二步：创建研究 Run

找到 `POST /runs`，填写：

```json
{
  "workflow_type": "episode-research",
  "payload": {
    "topic": "五年后重新开始录播客",
    "source_ids": [
      "src_把这里替换成上一步返回的ID"
    ]
  }
}
```

复制返回的 `run.id`。

### 第三步：确认系统停在人工检查点

在 `GET /runs/{run_id}` 中填入 Run ID。

成功结果应该包含：

- Run 状态为 `waiting_for_user`；
- `current_step` 为 `awaiting_interview_response`；
- `workflow_version` 为 `v3`；
- 一个 `research_manager`；
- 一个 `timeline_research`；
- 一个 `theme_research`；
- 一个串行的 `build_interview_scaffold`；
- 四个 Artifact：Timeline、Theme、Research Bundle 和 Interview Scaffold；
- `model_call_count` 为 3；
- `output_artifact_id` 指向 `build_interview_scaffold_result`。

Manager 只做确定性编排，不调用模型。三次 ModelCall 分别属于 Timeline、
Theme 和 Interviewer；默认由 Fake Provider 执行，Token 和费用都是 0。
四个 Task 此时都已结束，不应再出现 queued 或 running Task。等待状态保存在
SQLite 中，重启 Uvicorn 后仍可查询，Worker 也不会重复运行这些 Task。

### 第四步：查看执行历史

在 `GET /runs/{run_id}/events` 中填入 Run ID。

重点观察：

- `workflow.fan_out.started`
- 两个 `task.started`
- `workflow.fan_in.waiting`
- `workflow.fan_in.completed`
- `workflow.interview_scaffold.queued`
- `workflow.interview_scaffold.completed`
- `workflow.user_input.requested`
- `run.waiting_for_user`

`workflow.fan_in.completed` 必须早于
`workflow.interview_scaffold.queued`。这能直观看见：两个 Researcher
先 fan-out 并行，fan-in 汇总完成后，Interviewer 才串行开始。
`run.waiting_for_user` 应该是此时最后一个 Event，而且 Events 中不应已经有
`run.succeeded`。

### 第五步：下载 Markdown

在 `GET /runs/{run_id}/exports/interview-scaffold.md` 中填入同一个 Run ID。

成功时应该看到：

- HTTP 200；
- `content-type: text/markdown; charset=utf-8`；
- 文件名为 `interview-scaffold-{run_id}.md`；
- 正文包含开场、采访段落、问题、素材缺口和
  ``source_id#source_segment_id`` 来源标签。

`waiting_for_user` 和 Resume 后的 `succeeded` Run 都允许导出。脚手架尚未
生成或最终 Artifact 不是合法脚手架时会返回 409。导出器会重新校验严格
Schema，并转义模型文字中的 HTML 与 Markdown 控制字符，避免它偷偷插入
标题、链接或远程图片。

### 第六步：把补充口述文字导入为新 Source

再次打开 `POST /sources`。直接输入或粘贴已经转成文字的补充内容：

```json
{
  "title": "EP0 第一轮口述补充",
  "source_type": "voice_note_transcript",
  "text": "重新听见五年前的声音时，我意识到声音保存的不只是内容，还有当时的语气。",
  "metadata": {
    "round": 1,
    "purpose": "manual_test"
  }
}
```

复制这一次返回的新 `source.id`。不要把最初的 Source ID 和补充 Source ID
混淆。

### 第七步：用补充 Source 恢复 Run

找到 `POST /runs/{run_id}/resume`，填写同一个 Run ID 和：

```json
{
  "checkpoint": "interview_scaffold",
  "submission_id": "manual-round-1",
  "source_ids": [
    "src_把这里替换成补充Source的ID"
  ]
}
```

第一次成功响应应该包含：

```text
resumed = true
idempotent_replay = false
run.status = succeeded
run.current_step = complete
```

此时：

- Task 仍是 4 个；
- Artifact 从 4 个增加到 5 个；
- 新 Artifact 类型为 `user_material_submission`；
- ModelCall 仍是 3 个，没有新调用或费用；
- `output_artifact_id` 仍指向采访脚手架；
- submission Artifact 只保存 Source / SourceSegment 引用，不复制口述正文。

在 `GET /runs/{run_id}/events` 中，最后应该依次出现：

```text
run.resumed
workflow.user_material.accepted
run.succeeded
```

保持 `submission_id` 和 `source_ids` 完全不变，再执行一次 Resume。安全重放
应该返回：

```text
resumed = false
idempotent_replay = true
submission_artifact_id 与第一次相同
```

第二次不会新增 Artifact、Event 或 ModelCall。同一个 `submission_id` 如果
换成不同 Source，会返回 409；空值、重复 Source ID、未知 checkpoint 或
额外字段会返回 422；不存在的 Run 或 Source 会返回 404。

## 5. 自动化测试

运行全部测试：

```bash
pytest
```

当前全量基线：

```text
113 passed
```

定向测试研究、采访脚手架、人工检查点和导出：

```bash
pytest tests/test_research_schemas.py \
       tests/test_research_workflow.py \
       tests/test_interview_scaffold.py \
       tests/test_interview_export_api.py \
       tests/test_human_input_schemas.py \
       tests/test_human_checkpoint_api.py -vv
```

其中包含：

- 严格 Schema 测试；
- Researcher 与 Interviewer 两层越权引用拒绝测试；
- 原话必须存在于来源片段的测试；
- 两个 Child Task 确实同时执行的并发探针；
- 正常 fan-out/fan-in 后串行 Interviewer 测试；
- v3 以 4 Tasks、4 Artifacts、3 ModelCalls 进入人工等待；
- 补充 Source 后以 4 Tasks、5 Artifacts、3 ModelCalls 结束；
- v3 新流程与已在途 v1 / v2 Run 的兼容测试；
- Resume 的输入边界、404 / 409 / 422、幂等重放与冲突；
- 等待状态跨重启恢复、并发相同提交只应用一次；
- 等待中的 Run 可取消，取消后不能 Resume；
- Resume 与 Cancel 并发时只有一个终态能成功；
- submission Artifact 与日志不复制补充口述正文；
- Markdown 确定性、HTML/Markdown 注入转义和 200/404/409 API 测试；
- 一个 Child 失败后的父子失败传播；
- 完整 HTTP API 集成测试。

这里的 113 是当前基线；M2.2 的 28 项、M2.3a 的 32 项、M2.3b 的 83 项和
M2.4 的 99 项仍是各阶段当时的历史结果，不应回写修改。

检查代码质量：

```bash
ruff check src tests
ruff format --check src tests
```

检查数据库模型是否忘记生成 migration：

```bash
alembic current
alembic check
```

正确结果应该显示当前 revision 为 head，并且：

```text
No new upgrade operations detected.
```

M2.4 和 M3.1 都复用已有 Run、Task、Artifact、Event、ModelCall、Source 与
SourceSegment 表，没有新增数据库字段，因此 Alembic 仍为
`0003_model_call_trace (head)`，没有新的 migration。

## 6. 日志怎么看

启动 Uvicorn 的终端会输出一行一个 JSON 日志，例如：

```json
{
  "level": "INFO",
  "event": "worker.task.completed",
  "run_id": "run_...",
  "task_id": "task_...",
  "task_kind": "timeline_research"
}
```

排查时优先搜索：

- `request_id`
- `run_id`
- `task_id`
- `artifact_id`
- `model_call_id`
- `event`
- `error_code`

M2.4 重点事件是 `workflow.interview_scaffold.queued`、
`workflow.interview_scaffold.completed` 和
`run.interview_scaffold_markdown.exported`。完成日志只增加段落数、问题数
或 Markdown 字符数，不打印生成正文。

M3.1 再增加：

- `run.waiting_for_user`：采访脚手架已完成，当前没有后台 Task 在运行；
- `run.resume.accepted`：首次接收了一次补充 Source 提交；
- `run.resume.idempotent_replay`：相同 submission 的网络重试安全返回；
- `run.resume.rejected`：Run 状态或 submission 冲突，拒绝恢复。

这些日志可以包含 checkpoint、Source/Segment 数量和 Artifact ID，但不能包含
补充口述正文。日志禁止包含素材正文、prompt、模型完整输出和 API Key。

## 7. 数据库怎样查看

普通本地开发与 Swagger 默认使用：

```text
backend/data/epiphany.db
```

真实 DeepSeek smoke 使用独立的：

```text
backend/data/deepseek-live-smoke.db
```

这些文件都被 `.gitignore` 排除，不能提交到 GitHub。`.db`、`-wal`、`-shm`
各自是什么、每张表存什么、怎样用 `sqlite3 -readonly` 安全查看，以及哪些
正文与输出字段不应打印，统一参见
[SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)。

## 8. 标准排错顺序

出现问题时按下面顺序，而不是先猜：

1. 查看 HTTP 状态码和响应中的 `X-Request-ID`；
2. 保存 `run_id`；
3. 调用 `GET /runs/{run_id}` 看哪个 Task 失败；
4. 调用 `GET /runs/{run_id}/events` 回放顺序；
5. 如果 Run 正在等待，确认 `current_step` 是
   `awaiting_interview_response`，并核对 Resume 使用的是补充 Source ID；
6. 用 ID 搜索 stdout JSON 日志；
7. 查看 Task 或 Resume 响应中的稳定错误信息，不要只猜自然语言原因；
8. 用最小的单个 pytest 重现；
9. 必要时再查看 SQLite 中的持久化状态。

## 9. 常见问题

### `no such table`

通常表示没有执行：

```bash
alembic upgrade head
```

### 端口 8000 已被占用

可以换端口：

```bash
uvicorn epiphany.main:app --reload --port 8001
```

然后访问 <http://127.0.0.1:8001/docs>。

### 修改代码后行为没有变化

确认使用了 `--reload`，并确认当前终端启用的是
`backend/.venv`。

### 为什么 `voice_note_transcript` 没有弹出麦克风权限

这是预期行为。它只是“这段文字原本来自口述”的 Source 分类。M3.1 只接受
文字输入或粘贴，不含浏览器录音、音频上传或 STT。先在项目外完成语音转文字
也可以，但导入本系统的仍然是 `text`。

### 为什么 Resume 后立刻变成 succeeded

M3.1 的目标是证明等待点、补充 Source、幂等提交和持久化恢复。它在同一次
事务中执行：

```text
waiting_for_user -> running -> succeeded
```

这一步不会创建 Editor Task。通过 `GET /runs/{run_id}/events` 可以看见
`run.resumed` 和 `workflow.user_material.accepted`。下一阶段才会让 Editor
读取补充素材并生成新的内容 Artifact。

### 为什么还没有调用真实模型

默认不调用真实模型是安全设计。M2.2 先用 Fake Provider 证明编排、并发、
引用和失败传播；M2.3a 证明预算、retry、timeout、tokens、延迟和费用记录；
M2.3b-1 已接入 DeepSeek HTTP 契约，但继续用 MockTransport 免费验证。

检查零费用模型调用 Trace：

```bash
pytest tests/test_model_call_trace.py -vv
```

成功路径的 Run JSON 会出现 `model_calls`。Fake 调用的 Token 和费用是 0，
但 status、attempt 和 duration 会真实记录。

检查 DeepSeek 适配器但不联网：

```bash
pytest tests/test_deepseek_provider.py \
       tests/test_deepseek_research_workflow.py -vv
```

默认环境必须保持：

```env
EPIPHANY_MODEL_PROVIDER=fake
```

检查显式 smoke 命令但不联网：

```bash
python -m epiphany.live_deepseek_smoke
```

它默认只打印 preflight，不创建数据库，也不会发送请求。真正执行前，把 Key
只放在忽略提交的 `backend/.env`：

```env
EPIPHANY_DEEPSEEK_API_KEY=your-local-key
```

然后显式运行：

```bash
python -m epiphany.live_deepseek_smoke --execute
```

这条独立命令不要求修改默认的 `EPIPHANY_MODEL_PROVIDER=fake`，也不需要启动
Uvicorn 或 Swagger。它只使用短合成素材；当前 v3 harness 最多调用三次，
覆盖两个 Researcher 和一个串行 Interviewer，每个任务只尝试一次，并在
`waiting_for_user` 停止，不会自动 Resume。Trace 保存在忽略提交的
`data/deepseek-live-smoke.db`。M2.3b 已完成的历史 live smoke 仍是两次调用、
总计 2301 tokens；不要用当前三调用上限反向改写那次记录。也不要把 Key、
个人日记、播客原稿或真实响应复制进命令历史、测试 fixture、日志和 Git。

常见 smoke 排错：

- `api_key_status=absent`：Key 尚未写入 `backend/.env`；
- `live_smoke.crashed`：先看紧邻的结构化日志和稳定 `error_code`；
- `passed=false`：检查摘要中 Task 与 ModelCall 的 status / `error_code`；
- `ModelCall=succeeded` 但 Task failed：厂商调用成功，失败发生在 Schema、
  引用或逐字 quote 校验；
- `pytest: command not found`：先执行 `source .venv/bin/activate`。
