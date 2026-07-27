# M1：持久化 Agent Runtime

日期：2026-07-23

对应 commit：`65f6046 feat(runtime): add durable fake workflow`

## 1. 这一步要解决什么

在调用真实大模型以前，系统先要回答：

- 一次创作工作怎样被保存；
- 工作被拆成哪些步骤；
- 后台程序怎样领取和完成任务；
- 中途失败、重试、取消或重启会怎样；
- 怎样知道最后结果属于哪一步。

如果这些问题没有解决，Agent 即使生成了好内容，也可能因为进程退出、
网络错误或重复执行而丢失或混乱。

## 2. 生活化类比

把一次 Run 想成餐厅的一张订单：

- Run：整张订单；
- Task：备菜、烹饪、装盘；
- Worker：实际处理订单的厨房工作人员；
- Event：每个时间点的操作记录；
- Artifact：每一步留下的半成品或最终菜品；
- Database：不会因为员工下班而消失的订单系统。

Fake Provider 像模拟厨房：它不真的烹饪昂贵食材，但可以证明订单系统、
交接和异常处理正确。

## 3. 第一条 Fake Workflow

```text
prepare_sources
  -> fake_research
  -> assemble_artifact
  -> complete
```

三步都使用确定性 Fake Provider，不调用 OpenAI。

完成后会留下：

- 1 个 Run；
- 3 个 Task；
- 3 个 Artifact；
- 一组按顺序编号的 Event。

## 4. 四个核心数据对象

### Run

代表一次完整工作流，保存：

- 工作流类型和版本；
- 当前状态和步骤；
- 输入；
- 最终 Artifact；
- 调用次数；
- 是否请求取消。

### Task

代表一个可执行步骤，保存：

- 属于哪个 Run；
- Task 类型；
- 状态和 attempt；
- 最大尝试次数；
- 输入和输出 Artifact；
- idempotency key；
- lease token 和过期时间；
- 错误代码。

### Event

是 append-only 的执行历史。Append-only 表示只追加，不回头改写过去。
每条 Event 有 Run 内递增的 `sequence`，所以可以准确回放顺序。

### Artifact

是可持久化的任务结果。它保存结构化 JSON，并通过 `task_id` 和
`run_id` 回到来源。

## 5. 状态机

Run 的主要状态：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

Task 的主要状态：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

`state_machine.py` 显式校验状态转换。例如成功状态是终态，不能重新回到
running。

这比“直接给 status 字符串随便赋值”更安全。

## 6. Worker 怎样工作

Worker 是后台循环：

1. 从 SQLite 查找一个 `queued` Task；
2. 在事务中把它标记为 `running`；
3. 生成独立 lease token；
4. 调用 Provider；
5. 保存 Artifact；
6. 将 Task 标记为成功；
7. 追加 Event；
8. 让 Orchestrator 判断下一步。

Worker 不把整个 Workflow 藏在内存里，因此进程重启后可以从数据库继续
理解现状。

## 7. Orchestrator 与 Provider 的区别

### Orchestrator

负责流程规则：

- 第一条 Task 是什么；
- 成功后创建哪一条 Task；
- 最后一步完成后如何结束 Run。

### Provider

负责“给定 Task 输入，产生结构化输出”。

M1 只有 Fake Provider，但定义了统一接口。以后托管模型 Provider 可以替换
生成方式，而不用重写 Run/Task/Worker。

这是常见的依赖反转：业务运行时依赖抽象契约，不直接绑定外部模型厂商。

## 8. 可靠性概念

### 有界重试

只有暂时性 Provider 错误才重新排队，而且不能超过 `max_attempts`。

### Lease

领取 Task 的 Worker 必须携带当前 lease 才能提交结果。

### Fencing

旧 lease 返回的迟到结果会被拒绝，避免旧执行覆盖恢复后的新执行。

### Idempotent Artifact

Task 结果使用稳定 key。即使提交操作重试，也只生成一份有效 Artifact。

### Startup Recovery

Worker 启动时查找 lease 已过期的 `running` Task：

- 还有重试机会：重新排队；
- 尝试耗尽：失败；
- Run 已取消：取消 Task。

项目不声称 exactly-once，而是采用：

```text
at-least-once execution + idempotent commit
```

也就是任务可能重做，但结果提交必须可安全去重。

## 9. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `main.py` | 组装数据库、服务、Worker 和应用生命周期 |
| `api.py` | Run HTTP API |
| `models.py` | SQLAlchemy 数据表模型 |
| `schemas.py` | API 返回结构 |
| `db.py` | SQLite engine、session 和 WAL 配置 |
| `state_machine.py` | Run/Task 合法状态转换 |
| `events.py` | 追加有顺序的 Event |
| `services.py` | 创建、查询和取消 Run |
| `runtime/orchestrator.py` | 确定性工作流编排 |
| `runtime/worker.py` | 领取、执行、提交、重试和恢复 |
| `runtime/providers/base.py` | Provider 接口 |
| `runtime/providers/fake.py` | 免费确定性测试实现 |

## 10. API

M1 增加：

```text
GET  /health
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
POST /runs/{run_id}/cancel
```

可以通过 Swagger 或 curl 创建 Fake Run，然后查看 Task、Artifact 和 Event。

## 11. 自动化测试

M1 最初有 11 个测试，重点包括：

- 状态转换是否合法；
- 三步 Workflow 能否完成；
- 数据库重启后能否查询；
- 暂时性错误是否重试；
- 重试耗尽是否失败；
- 取消后是否停止执行；
- lease 过期是否恢复；
- 旧 lease 的结果是否被拒绝；
- API 是否能创建和读取 Run。

关键测试文件：

- `tests/test_state_machine.py`
- `tests/test_runtime.py`
- `tests/test_api.py`

## 12. 本地怎样验证

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
alembic upgrade head
pytest
uvicorn epiphany.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>，调用 `POST /runs`：

```json
{
  "workflow_type": "fake-podcast",
  "payload": {
    "topic": "runtime smoke test"
  }
}
```

稍后查询 Run，应看到 3 条成功 Task 和 3 份 Artifact。

## 13. 这一步学到了什么

- Agent Runtime 首先是任务和状态系统，不只是 prompt；
- 后台 Worker 与 HTTP API 可以共享数据库状态；
- 重试必须区分暂时性错误和永久错误；
- lease 和 fencing 是处理迟到结果的重要方法；
- Fake Provider 能把模型质量与系统正确性分开测试；
- 数据库持久化让重启恢复成为可能；
- 每一步都应该留下可查询的交付物与执行历史。

## 14. 当时还缺少什么

- 没有真实 Source 概念；
- 没有来源分段和引用；
- 没有父子 Task；
- 没有并发 fan-out/fan-in；
- 没有真实模型；
- 没有正式前端。

M1 证明了 Runtime 骨架，M2 才开始加入真实产品领域对象。
