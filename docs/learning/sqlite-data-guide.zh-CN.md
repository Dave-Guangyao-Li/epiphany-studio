# SQLite 数据与排查指南

更新时间：2026-07-28

这篇文档只回答四个问题：

1. `data/` 目录里的数据库文件分别是什么；
2. 数据库里的表分别保存什么；
3. 怎样以只读方式查看 Run、Task、Event、ModelCall 和 Artifact；
4. 怎样避免误删数据、误读日志或泄露个人素材。

## 1. 为什么项目需要数据库

可以把数据库理解为 Agent Runtime 的“工作档案柜”。

Python 进程中的变量在服务停止后会消失，但一次 Workflow 的状态不能因此
消失。系统需要在进程重启后仍然知道：

- 用户导入了哪些素材；
- 当前 Run 进行到哪一步；
- 哪些 Task 成功、失败或正在重试；
- 调用了几次模型、用了多少 Token、耗时和预估费用是多少；
- Agent 生成了哪些候选 Artifact；
- 整个执行过程按什么顺序发生。

因此，本项目把 SQLite 当作状态真相来源。Swagger 和 SSE 只是读取这些状态
的不同窗口，不是另一份真相。

## 2. `data/` 中的文件分别是什么

从 `backend/` 目录看，目前最重要的是：

| 文件 | 用途 | 是否提交 Git |
| --- | --- | --- |
| `data/epiphany.db` | Uvicorn、Swagger 和普通本地开发默认使用的数据库 | 否 |
| `data/deepseek-live-smoke.db` | 受限真实 DeepSeek smoke 独立 Trace | 否 |
| `*.db-wal` | SQLite Write-Ahead Log 写前日志 | 否 |
| `*.db-shm` | WAL 模式下的共享协调索引 | 否 |

普通开发和真实 smoke 分开保存，是为了避免：

- Fake Provider 的免费测试记录与真实付费调用混在一起；
- 排查真实调用时被其他 Run 干扰；
- 为了重跑 smoke 而误删日常开发数据。

pytest 通常使用临时目录里的独立数据库。测试结束后可以丢弃，它们不是产品
数据。

### 2.1 `.db`、`-wal` 和 `-shm` 的关系

可以用记账来类比：

- `.db` 是正式总账；
- `-wal` 是先追加记录的新账页，之后再 checkpoint 回正式总账；
- `-shm` 是多个读写者查找这些新账页时使用的协调索引。

`-wal` 和 `-shm` 不是两套额外的业务数据库。它们可能在连接关闭后消失，也
可能保留并在下次启动时重新使用。

应用正在运行时：

- 不要单独删除、移动或改写 sidecar 文件；
- 不要只复制 `.db` 并假设它一定包含最新状态；
- 最安全的离线备份方式是先停止 Uvicorn，再复制数据库；
- 必须在线备份时，应使用 SQLite 的 `.backup`，而不是 Finder 只复制
  `.db`。

## 3. 每张表保存什么

| 表 | 普通话解释 | 主要内容 |
| --- | --- | --- |
| `alembic_version` | 数据库结构版本号 | 当前执行到了哪个 migration |
| `sources` | 一份导入素材 | 规范化全文、类型、hash、metadata |
| `source_segments` | 可以稳定引用的素材片段 | 原文片段、顺序、字符区间、hash |
| `runs` | 一次完整 Workflow | 类型、状态、当前步骤、调用上限、时间 |
| `tasks` | Run 中可领取和重试的工作单 | Agent 类型、父子关系、attempt、lease、错误码 |
| `events` | Workflow 的持久化时间线 | sequence、事件类型、关联 Task、结构化 payload |
| `model_calls` | 每次 Provider attempt 的调用账本 | 模型、Token、耗时、费用、币种、状态 |
| `artifacts` | Agent 产生的候选成果 | Timeline、Theme、Bundle 等结构化内容 |

它们之间的关系可以简单理解为：

```text
Source
  -> SourceSegment

Run
  -> Task
      -> ModelCall
      -> Artifact
  -> Event timeline
```

数据库中会保存素材正文和生成结果，所以本地 `.db` 文件本身也属于私密数据，
不能提交到 GitHub。

## 4. 优先从 API 查看，必要时再查数据库

日常调试的推荐顺序是：

1. `GET /runs/{run_id}` 查看 Run、Task、ModelCall 和 Artifact 摘要；
2. `GET /runs/{run_id}/events` 回放执行顺序；
3. 用 `run_id`、`task_id`、`request_id` 搜索 stdout JSON 日志；
4. API 信息仍不足时，再只读查询 SQLite。

当前 Uvicorn 默认连接 `data/epiphany.db`。因此 Swagger 不会自动显示
`data/deepseek-live-smoke.db` 里的真实 smoke Run；查看 smoke Trace 时，直接
使用下面的只读 SQLite 命令更简单。

## 5. 使用 `sqlite3` 只读查看

进入后端目录：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
```

打开真实 smoke 数据库：

```bash
sqlite3 -readonly data/deepseek-live-smoke.db
```

设置易读输出：

```sql
.headers on
.mode column
.tables
```

### 5.1 查看 Run

```sql
SELECT id, workflow_type, status, current_step,
       model_call_count, created_at
FROM runs
ORDER BY created_at DESC;
```

### 5.2 查看 Task

```sql
SELECT id, kind, agent_type, status, attempt, max_attempts,
       COALESCE(error_code, '') AS error_code
FROM tasks
ORDER BY created_at;
```

### 5.3 查看模型调用

```sql
SELECT provider, model, status,
       input_tokens, output_tokens, duration_ms,
       estimated_cost_micros, cost_currency,
       COALESCE(error_code, '') AS error_code
FROM model_calls
ORDER BY started_at;
```

`estimated_cost_micros` 是一单位货币的百万分之一。例如：

```text
214 micros USD = 0.000214 USD
3510 micros CNY = 0.003510 CNY
```

### 5.4 查看 Event 时间线

```sql
SELECT sequence, type,
       COALESCE(task_id, '') AS task_id,
       created_at
FROM events
ORDER BY sequence;
```

### 5.5 查看 Artifact 种类，不读取正文

```sql
SELECT kind, COUNT(*) AS count
FROM artifacts
GROUP BY kind
ORDER BY kind;
```

输入下面命令退出：

```sql
.quit
```

## 6. 哪些字段不要随手打印

例行排查时，不要执行 `SELECT *`。以下字段可能包含日记、口述、Prompt
上下文或模型生成内容：

- `sources.content_text`
- `source_segments.text`
- `artifacts.content_json`
- `events.payload`
- `tasks.input_json`
- `runs.input_json`

优先只查 ID、状态、类型、时间、Token、耗时、费用和错误码。这样既容易阅读，
也能避免终端日志、截图或聊天记录意外泄露个人素材。

## 7. 使用 GUI 查看

可以使用支持 SQLite 的桌面工具或编辑器扩展，但应满足：

- 尽量使用只读模式；
- 不手动修改业务状态；
- Uvicorn 运行时不要单独处理 `-wal`、`-shm`；
- 想保留一个一致快照时，先停服务再复制，或使用 SQLite `.backup`。

数据库结构通过 Alembic migration 修改；Run、Task、Event 等业务状态通过
应用代码修改。不要通过 GUI 手工“修好”某个状态，否则数据库 Trace 与真实
执行过程会失去可信度。

## 8. 常见误解

### Finder 把 `.db` 显示成“文稿”

这只是 macOS 不认识扩展名，不代表文件内容是文稿。它仍然是 SQLite
数据库。

### `-wal` 是 0 字节

通常表示目前没有等待 checkpoint 的 WAL frame，不代表数据库没有数据。

### 新开的 CLI 连接显示不同 PRAGMA

`foreign_keys`、`busy_timeout` 等部分设置属于“每个连接自己的配置”。应用
创建连接时会设置它们；新开的 `sqlite3` CLI 没有执行应用连接钩子，显示默认值
不代表应用配置失败。

### `no such table`

通常表示当前数据库还没有执行 migration：

```bash
alembic upgrade head
```

也可能是打开了错误的 `.db` 路径。先运行 `.tables`，再确认当前目录和文件名。

## 9. 本次 DeepSeek smoke 数据

2026-07-28 的独立 Trace 位于：

```text
backend/data/deepseek-live-smoke.db
```

它保存：

- 1 个成功 Run；
- 3 个成功 Task；
- 18 个 Event；
- 2 个成功 DeepSeek ModelCall；
- 3 个 Artifact。

两次调用合计：

```text
1,092 input tokens
1,209 output tokens
2,301 total tokens
15,435 ms Provider latency
0.000491 USD historical local estimate
```

DeepSeek Dashboard 同时显示 2 次 API 请求和 2,301 Tokens，因此真实请求数量
和 Token 用量已经完成端到端对账。费用币种与双币种设计见
[M2.3b DeepSeek Provider](m2-3b-deepseek-provider.zh-CN.md)。
