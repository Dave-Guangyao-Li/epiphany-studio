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

## 4. 手动走通 Source 到双 Agent

### 第一步：导入测试素材

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
    "source_ids": [
      "src_把这里替换成上一步返回的ID"
    ]
  }
}
```

复制返回的 `run.id`。

### 第三步：查询最终状态

在 `GET /runs/{run_id}` 中填入 Run ID。

成功结果应该包含：

- Run 状态为 `succeeded`；
- 一个 `research_manager`；
- 一个 `timeline_research`；
- 一个 `theme_research`；
- 三个 Artifact；
- `model_call_count` 为 2。

这里的两次调用是 Fake Provider 执行，不是付费模型调用。

### 第四步：查看执行历史

在 `GET /runs/{run_id}/events` 中填入 Run ID。

重点观察：

- `workflow.fan_out.started`
- 两个 `task.started`
- `workflow.fan_in.waiting`
- `workflow.fan_in.completed`
- `run.succeeded`

这能直观看见 fan-out 和 fan-in 不是抽象术语，而是数据库中可回放的真实
执行过程。

## 5. 自动化测试

运行全部测试：

```bash
pytest
```

当前基线：

```text
28 passed
```

只测试双 Agent：

```bash
pytest tests/test_research_schemas.py tests/test_research_workflow.py -vv
```

其中包含：

- 严格 Schema 测试；
- 越权引用拒绝测试；
- 原话必须存在于来源片段的测试；
- 两个 Child Task 确实同时执行的并发探针；
- 正常 fan-out/fan-in 测试；
- 一个 Child 失败后的父子失败传播；
- 完整 HTTP API 集成测试。

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
- `event`
- `error_code`

日志禁止包含素材正文、prompt、模型完整输出和 API Key。

## 7. 数据库怎样查看

默认数据库文件：

```text
backend/data/epiphany.db
```

它被 `.gitignore` 排除，不能提交到 GitHub。

如果本机安装了 `sqlite3`：

```bash
sqlite3 data/epiphany.db
```

进入后可以执行：

```sql
.headers on
.mode column
SELECT id, workflow_type, status, current_step FROM runs;
SELECT id, run_id, parent_task_id, kind, status FROM tasks;
SELECT run_id, sequence, type, task_id FROM events ORDER BY run_id, sequence;
```

输入 `.quit` 退出。

不要直接手动修改这些表。结构变化通过 Alembic，业务状态通过应用代码。

## 8. 标准排错顺序

出现问题时按下面顺序，而不是先猜：

1. 查看 HTTP 状态码和响应中的 `X-Request-ID`；
2. 保存 `run_id`；
3. 调用 `GET /runs/{run_id}` 看哪个 Task 失败；
4. 调用 `GET /runs/{run_id}/events` 回放顺序；
5. 用 ID 搜索 stdout JSON 日志；
6. 查看 Task 的 `error_code`，不要只看自然语言错误；
7. 用最小的单个 pytest 重现；
8. 必要时再查看 SQLite 中的持久化状态。

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

### 为什么没有调用 OpenAI

这是当前阶段的设计。M2.2 先用 Fake Provider 证明编排、并发、引用和失败
传播正确；M2.3 才在同一契约后面接真实模型。
