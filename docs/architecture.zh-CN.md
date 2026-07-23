# 轻量 Agent Runtime 架构

状态：Draft

日期：2026-07-23

## 1. 目标

第一版架构需要同时满足：

- 足够简单，可以个人开发和本机运行；
- 能真实练习 Agent 编排、后端状态和可靠性；
- 进程失败后不完全丢失工作；
- 不把产品状态藏进某个 Agent 框架的内部对象；
- 将来可以逐步替换数据库、队列或模型 Provider。

## 2. 系统边界

```text
Web UI
  |
  | HTTP + SSE
  v
FastAPI
  |- Run Service
  |- Source Service
  |- Event Service
  |- Orchestrator
  |
  +--> SQLite (source of truth)
  +--> Local Artifact Store
  +--> Durable Worker Loop
          |
          +--> OpenAI Provider
          +--> Fake Provider
```

## 3. 编排与调度的边界

### 模型可以决定

- 哪些已有素材可能相关；
- 应从哪个受允许的研究角色获取帮助；
- 证据中还缺少什么；
- 如何组织问题和草稿。

### 代码必须决定

- 允许调用哪些 Agent；
- 最大深度和并发数；
- Task 的依赖、状态和停止条件；
- timeout、retry 和预算；
- 取消传播；
- 哪些写操作需要用户确认；
- 最终状态是否有效。

MVP 使用固定 Workflow。模型不能发明任意 Agent 名称，也不能递归创建
Subagent。

## 4. 第一个 Workflow

```text
create_run
  -> prepare_sources
  -> fan_out
       |- timeline_research
       `- theme_research
  -> fan_in
  -> build_interview_scaffold
  -> wait_for_user
  -> incorporate_user_material
  -> draft_episode
  -> validate_sources
  -> complete
```

其中 `prepare_sources`、`fan_in` 和状态更新是普通代码；
`timeline_research`、`theme_research`、`build_interview_scaffold` 和
`draft_episode` 才调用模型。

## 5. Subagent 定义

Subagent 是一个受约束的 Child Task，而不是独立微服务：

```json
{
  "task_id": "task_...",
  "run_id": "run_...",
  "parent_task_id": "task_manager",
  "agent_type": "timeline_researcher",
  "objective": "从允许的素材中提取人生时间节点",
  "source_segment_ids": ["src_01:12", "src_02:04"],
  "output_schema": "TimelineEvent[]",
  "tool_policy": ["read_source_segments"],
  "deadline_seconds": 120,
  "model_call_budget": 1
}
```

子任务只返回结构化候选 Artifact。它不能直接更改已确认的记忆。

## 6. 持久化模型

### `runs`

- `id`
- `workflow_type`
- `workflow_version`
- `status`
- `current_step`
- `input_ref`
- `output_ref`
- `model_call_count`
- `cancel_requested_at`
- `created_at`
- `updated_at`

### `tasks`

- `id`
- `run_id`
- `parent_task_id`
- `agent_type`
- `status`
- `attempt`
- `input_ref`
- `output_ref`
- `idempotency_key`
- `lease_token`
- `lease_expires_at`
- `error_code`
- `created_at`
- `updated_at`

### `events`

- `id`
- `run_id`
- `task_id`
- `sequence`
- `type`
- `payload`
- `created_at`

### 领域对象

- `projects`（M3 前补充）
- `sources`
  - 规范化全文、SHA-256、类型、metadata、字符数
- `source_segments`
  - 稳定片段 ID、顺序、原文、字符区间、SHA-256
- `artifacts`
- `memory_candidates`

M2.1 使用规范化正文 hash 生成稳定 Source ID，使用 Source hash、片段顺序
和片段 hash 生成稳定 Segment ID。唯一约束保证重复或并发重试不会生成
第二份 Source。列表 API 只返回摘要，详情 API 返回有序片段，不直接返回
整篇 `content_text`。

## 7. 状态机

### Run

```text
queued
  -> running
  -> waiting_for_user
  -> running
  -> succeeded | failed | cancelled
```

### Task

```text
queued
  -> running
  -> succeeded | failed | cancelled
```

终态不可回退。重试创建新的 `attempt`，但保留稳定的逻辑 `task_id`。

## 8. Worker

MVP 只有一个 Worker 进程，但任务存在 SQLite 中，不只存在内存中。

Worker 循环：

1. 在事务中领取一个 `queued` Task。
2. 写入 `lease_token` 和过期时间。
3. 运行模型调用或确定性 Handler。
4. 持久化 Artifact。
5. 使用当前 lease/fencing token 提交终态。
6. 追加 Event。
7. 触发 Orchestrator 判断下一个可运行步骤。

同一进程内通过 `asyncio.Semaphore(2)` 控制并发。

进程启动时将已过期的 `running` Task 重新排队。后续如果需要多 Worker，
再迁移 PostgreSQL，不在 SQLite 上模拟分布式队列。

Alembic 是数据库 schema 的唯一变更入口。正常应用启动不得调用
`metadata.create_all()` 自动补表，否则会出现“表已经存在但 migration
版本未前进”的 schema drift。`create_all()` 只用于隔离的临时测试库。

## 9. 可靠性基线

MVP 必须实现：

- 稳定的 Run/Task/Event ID；
- append-only 事件；
- 每个副作用的 idempotency key；
- 有界 retry，默认只重试瞬时读取或模型网络错误；
- parent cancel 标记；
- Child 提交结果时验证 cancel 状态和 lease token；
- 已完成只读 Artifact 可保留；
- 模型输出经过 Pydantic 严格校验；
- 单 Run 调用和并发上限；
- 启动恢复测试。

不承诺 exactly-once。采用 at-least-once execution + idempotent commit。

## 10. 模型 Provider

定义最小接口：

```python
class ModelProvider(Protocol):
    async def generate_structured(
        self,
        *,
        task: AgentTask,
        schema: type[BaseModel],
    ) -> BaseModel: ...
```

首批实现：

- `OpenAIProvider`：官方 SDK 和 Responses API；
- `FakeProvider`：测试状态机和恢复，不产生 API 费用。

模型名、reasoning effort 和 `store` 均通过配置传入。默认
`store=false`。这不是 Zero Data Retention 的替代，但能避免把响应作为
应用状态保存到 API；本地数据库仍是产品状态来源。

## 11. API 和事件

最小 API：

```text
POST /projects
POST /projects/{id}/sources
POST /projects/{id}/episode-runs
GET  /runs/{id}
POST /runs/{id}/resume
POST /runs/{id}/cancel
GET  /runs/{id}/events
GET  /runs/{id}/events/stream
```

SSE 用于低成本实时显示。客户端断线后先从数据库按 `sequence` 补事件，
再连接实时流。SSE 不是状态真相。

## 12. 可观测性与调试

系统区分两种 Trace：

- 数据库中的 append-only Event 是持久化产品执行轨迹，用于回答某个 Run
  经过了哪些 Task、状态和 Artifact；
- stdout JSON 日志是运行诊断轨迹，用于回答请求耗时、Worker 领取、重试、
  失败和恢复发生在何时。

HTTP 接受并返回 `X-Request-ID`。同一请求内的服务日志继承该 ID；异步
Worker 日志使用 `run_id`、`task_id` 和 `attempt` 关联。日志只记录标识、
状态、错误代码和耗时，不记录素材正文、prompt、模型输出、密钥或录音。

未来 Web UI 必须保留后端返回的 request ID，在错误界面展示它，并通过
Run/Event API 呈现可回放状态。浏览器控制台不能成为唯一调试来源。

每个纵向切片的完成条件都包括：

- 正常与失败路径测试；
- 稳定的日志 event 名称；
- 可从 API 或持久化 Event 复现问题；
- 必要的手工演示和文档同步。

## 13. 升级触发条件

只有出现以下证据时才升级：

- 多进程或多机器 Worker：SQLite -> PostgreSQL；
- 任务量要求独立 Broker：引入队列；
- Workflow 数量和分支难以维护：评估 LangGraph；
- 长任务跨部署恢复要求显著提升：评估 Temporal；
- 语义检索成为质量瓶颈：评估 embeddings/vector index；
- 执行不受信任的用户代码：引入 sandbox/container。
