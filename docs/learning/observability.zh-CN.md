# 可观测性：如何知道系统发生了什么

日期：2026-07-23

对应 commit：`0f5dd5a feat(observability): add structured runtime logs`

## 1. 为什么“功能能跑”还不够

当系统只有一个按钮时，可以靠肉眼猜问题。Agent Workflow 有 HTTP 请求、
后台 Worker、多个 Task、模型 Provider 和数据库事务，错误可能发生在任意
一层。

如果没有追踪能力，只会看到：

> 失败了。

但不知道是哪次请求、哪条 Run、哪个 Task、第几次尝试出了问题。

可观测性的目标是让系统自己留下足够线索。

## 2. 生活化类比

快递系统同时需要两种记录：

- 包裹轨迹：已揽收、运输中、到达站点、已签收；
- 仓库监控：扫描枪故障、接口耗时、服务器错误。

前者对应数据库 Event，后者对应运行日志。只保留其中一种都不够。

## 3. 两种 Trace

### Durable Event

Event 存入 SQLite，用来回答：

- Run 经历了哪些状态；
- Task 以什么顺序执行；
- 哪一步产生了哪个 Artifact；
- 重启后过去发生过什么。

它是产品执行轨迹。

### stdout JSON Log

日志写到运行终端，用来回答：

- HTTP 请求耗时多久；
- Worker 何时领取任务；
- 是否发生重试、失败或 lease 恢复；
- 哪个 error code 出现；
- 代码异常堆栈是什么。

它是运维诊断轨迹。

## 4. 为什么用结构化 JSON

普通日志：

```text
Task failed again!
```

结构化日志：

```json
{
  "event": "worker.task.failed",
  "run_id": "run_...",
  "task_id": "task_...",
  "task_kind": "timeline_research",
  "attempt": 1,
  "error_code": "invalid_source_reference"
}
```

JSON 字段可以稳定搜索、过滤，也更容易在未来接入日志平台。

## 5. Request ID

HTTP 中间件会：

1. 读取调用方提供的 `X-Request-ID`；
2. 如果没有就自动生成；
3. 在响应 Header 中返回；
4. 让同一次请求内的服务日志携带该 ID。

手动指定：

```bash
curl -i http://127.0.0.1:8000/health \
  -H 'x-request-id: req_manual_debug'
```

看到错误时应保存 Request ID，它是从浏览器错误回到后端日志的第一条线索。

## 6. Worker 怎样关联

后台 Task 通常发生在原始 HTTP 请求结束之后，因此不能只靠 Request ID。

Worker 日志使用：

- `run_id`
- `task_id`
- `task_kind`
- `attempt`

这几个字段把异步执行串起来。

## 7. 稳定 Event Name

日志不只写自然语言 message，还写稳定 `event`：

```text
http.request.completed
run.created
worker.started
worker.task.claimed
worker.task.completed
worker.task.retry_scheduled
worker.task.failed
worker.tasks.recovered
worker.task.stale_result
workflow.fan_out.started
workflow.fan_in.completed
```

自然语言以后可以修改，稳定 event name 便于测试、告警和统计。

## 8. 隐私边界

日志中可以记录：

- ID；
- 状态；
- Task 类型；
- attempt；
- 数量；
- 耗时；
- error code。

日志中禁止记录：

- 日记或转录正文；
- prompt；
- 完整模型输出；
- API Key；
- 录音；
- voice clone 参考文件。

错误信息也要尽量使用经过设计的安全信息，不能把上游整段响应直接打印。

## 9. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `observability.py` | JSON Formatter、字段白名单和 Request ID 上下文 |
| `main.py` | HTTP 中间件，记录状态码和耗时 |
| `services.py` | Run 创建、取消日志 |
| `source_service.py` | Source 导入和去重日志 |
| `runtime/worker.py` | claim、complete、retry、fail、recover 日志 |
| `runtime/orchestrator.py` | fan-out/fan-in 日志 |
| `events.py` | 数据库中的持久化 Event |

## 10. 怎样测试

自动化测试：

```bash
pytest tests/test_observability.py -vv
pytest tests/test_api.py -vv
```

测试验证：

- JSON 中包含预期字段；
- Context 中的 Request ID 自动进入日志；
- HTTP 响应返回 `X-Request-ID`；
- 调用方提供的 ID 不会被替换。

M2.2 的失败注入测试还验证：

- `worker.task.failed` 被记录；
- 被取消 Task 的迟到结果产生 `worker.task.stale_result`。

## 11. 本地怎样观察

```bash
uvicorn epiphany.main:app --reload
```

在 Swagger 创建 Source 和 Run，观察同一个终端中的 JSON 行。

然后调用：

```text
GET /runs/{run_id}/events
```

对照检查：

- 数据库 Event 是否反映业务顺序；
- stdout Log 是否给出运行诊断；
- 两边是否能通过 Run ID 和 Task ID 关联。

## 12. 标准排错方法

1. 从 HTTP 响应保存 Request ID；
2. 从响应保存 Run ID；
3. 查询 Run，看失败的 Task 和 `error_code`；
4. 查询 Events，确定状态变化顺序；
5. 搜索 stdout 中相同 Run ID / Task ID；
6. 如果怀疑恢复问题，再检查 attempt、lease 和 recovered 日志；
7. 使用最小测试复现。

## 13. 这一步学到了什么

- 可观测性不是上线后再加的装饰；
- Event 和 Log 服务于不同问题；
- 异步任务不能只靠 HTTP Request ID；
- 稳定字段比随意打印一句话更适合长期维护；
- 隐私数据不应该为了“方便调试”进入日志；
- 可测试的日志契约能防止后续重构把调试能力弄丢。

## 14. 当时还缺少什么

- 尚未有前端 Trace 页面；
- 尚未有 SSE 实时事件；
- 尚未接入集中式日志平台；
- 尚未记录真实模型 tokens、延迟和费用；
- 尚未有生产环境告警。

当前目标只是建立本地开发和未来部署都能复用的最小观测基线。
