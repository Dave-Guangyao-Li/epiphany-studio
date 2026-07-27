# M2.2：双 Agent 并行编排

日期：2026-07-23

对应 commit：`8e4306a feat(workflow): add fake parallel research agents`

## 1. 这一步要解决什么

M2.1 已经能保存和引用素材，但还没有 Agent 真正消费这些素材。

M2.2 要验证一条最小父子 Agent 工作流：

1. Manager 创建两条职责不同的 Child Task；
2. 两个 Child 真正并发执行；
3. 每个结果必须满足严格 Schema；
4. 每条结论必须引用获准读取的 Segment；
5. 所有 Child 成功后才能合并；
6. 一个 Child 失败时，父任务和兄弟任务正确收敛；
7. 被取消任务的迟到结果不能写入。

依然使用 Fake Provider，因为这一阶段验证的是 Runtime 正确性，不是模型
内容质量。

## 2. Fan-out 是什么

Fan-out 中文可以理解为“扇出”或“分发”。

生活类比：播客编辑把同一批材料交给两位研究员：

- Timeline Researcher：只找时间节点和事件；
- Theme Researcher：只找主题、认知变化和可引用原话。

```text
                         -> Timeline Researcher
research_manager
                         -> Theme Researcher
```

一条父工作展开为两条独立工作，因此叫 fan-out。

在代码中，Manager 创建两条拥有相同 `parent_task_id` 的 Child Task。

## 3. Fan-in 是什么

Fan-in 中文可以理解为“扇入”或“汇合”。

生活类比：编辑必须等两位研究员都交付，才能把时间线和主题材料装订成一个
研究包。

```text
Timeline Artifact
                    -> Manager -> Research Bundle
Theme Artifact
```

本项目的 fan-in 是普通后端代码，不是第三个 AI：

1. 查询同一个 Manager 下的 Child Task；
2. 统计成功数量；
3. 如果没有全部成功，记录 `workflow.fan_in.waiting`；
4. 全部成功后读取两个 Artifact；
5. 创建一个 `episode_research_bundle`；
6. 将 Manager 和 Run 标记为成功。

“等待所有必需分支，再确定性合并”就是 fan-in 的核心。

## 4. 完整执行流程

```text
POST /runs
  -> 创建 Run
  -> 创建 running research_manager
  -> fan-out
       |- queued timeline_research
       `- queued theme_research
  -> Worker 领取两个 Child
  -> asyncio.gather 并发调用 Fake Provider
  -> 严格校验两个输出
  -> 分别保存 Child Artifact
  -> 第一个完成时等待
  -> 第二个完成后 fan-in
  -> 创建 episode_research_bundle
  -> Manager succeeded
  -> Run succeeded
```

## 5. 为什么要拆成两个 Agent

不是为了让 Agent 数量看起来更多，而是为了明确职责：

- 两者可以使用不同输出 Schema；
- 可以分别失败和重试；
- 可以独立查看 Artifact；
- 可以并行减少总耗时；
- 将来可以分别评估模型质量；
- Manager 不需要依赖一个巨大而模糊的 prompt。

如果两个角色长期没有真正不同的输入、输出或评估标准，就不应该为了形式
强行拆 Agent。

## 6. 怎样证明它真的并发

“创建两个 Task”不代表并发。它们也可能一个完成后另一个才开始。

M2.2 的 Worker：

1. 依次用短事务领取最多两个 queued Task；
2. 为每个 Task 生成独立 lease；
3. 使用 `asyncio.gather` 同时运行 Provider；
4. Provider 完成后再提交结果。

测试中的 `ConcurrencyProbeProvider` 会记录当前活跃调用数。

只有当 `max_active == 2`，才能证明两个 Provider 调用在时间上发生重叠。

## 7. 为什么结果提交仍然串行

Provider 调用是真正并发的，但单 Worker 内使用一个很短的 finalization
临界区提交数据库终态。

原因：如果两个 Child 在完全相同的时刻完成，它们不能都读到“另一个还没
成功”的旧状态，否则可能永远没人触发 fan-in。

所以当前策略是：

- 耗时的模型调用并发；
- 短暂的状态提交串行。

这适合单进程 MVP。未来多 Worker 时，需要 PostgreSQL 行锁或等价数据库
协调，不能依赖 Python 进程内的 lock。

## 8. 严格输出 Schema

Timeline 输出要求：

- 至少一个 timeline event；
- label；
- description；
- 可选 time expression；
- 0 到 1 的 confidence；
- 至少一条 SourceReference；
- 禁止额外字段。

Theme 输出要求：

- 至少一个 theme；
- insight；
- confidence；
- SourceReference；
- 可选 QuoteCandidate；
- Quote 必须有唯一来源；
- 禁止额外字段。

Schema 的目的不是让 JSON 好看，而是让后续代码可以可靠消费结果。

## 9. 来源引用校验

每条 Child Task 的输入包含它获准读取的 Segment：

```json
{
  "source_id": "src_...",
  "source_segment_id": "seg_...",
  "text": "允许读取的测试片段"
}
```

输出校验会构造允许集合，并检查每个引用的
`(source_id, source_segment_id)`。

如果 Agent 引用了输入范围之外的 Segment：

```text
invalid_source_reference
```

如果 Quote 文本并没有逐字存在于引用片段：

```text
quote_source_mismatch
```

这样可以区分：

- 用户真正说过的原话；
- 模型对原话的总结或改写。

改写可以作为 insight，但不能伪装成 quote。

## 10. 三份 Artifact

成功 Run 会生成：

1. `timeline_research_result`
2. `theme_research_result`
3. `episode_research_bundle`

前两份属于 Child Task，第三份属于 Manager，也是 Run 的最终 Artifact。

Bundle 保留两个 Child Artifact 的 ID 和内容，便于追踪合并来源。

## 11. 失败传播

故障注入测试让 Timeline 返回一个越权引用，同时故意延迟 Theme。

预期结果：

```text
timeline_research -> failed
research_manager  -> failed
theme_research    -> cancelled
Run               -> failed
```

Theme 即使稍后返回，也会因为：

- Task 已取消；
- lease 已清除；
- Run 已失败；

而触发 `StaleLease`，不能写入 Artifact。

这叫 cancellation propagation 和 late-result fencing。

## 12. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `research_schemas.py` | Timeline/Theme 严格输出与引用校验 |
| `schemas.py` | 接受 `episode-research` Workflow |
| `services.py` | 校验 source_ids、加载允许的 Segment、创建 Run |
| `runtime/orchestrator.py` | Manager、fan-out、等待、fan-in、失败传播 |
| `runtime/worker.py` | 双并发、输出校验、提交隔离、迟到结果拒绝 |
| `runtime/providers/fake.py` | 两个研究角色的确定性 Fixture |
| `config.py` | `worker_max_concurrency=2` |
| `observability.py` | 并发和父子任务相关日志字段 |

## 13. Durable Events

成功路径重点 Event：

```text
run.created
run.started
task.started                    # Manager
workflow.fan_out.started
task.queued                     # Timeline
task.queued                     # Theme
task.started                    # Timeline
task.started                    # Theme
task.succeeded                  # 第一个 Child
workflow.fan_in.waiting
task.succeeded                  # 第二个 Child
workflow.fan_in.completed
task.succeeded                  # Manager
run.succeeded
```

Event 使 fan-out/fan-in 可以从数据库回放，而不只存在于代码注释里。

## 14. 自动化测试

专项运行：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_research_schemas.py tests/test_research_workflow.py -vv
```

7 个专项测试：

1. Timeline strict Schema 和合法引用；
2. 越权引用拒绝；
3. Quote 必须逐字存在；
4. 正常 fan-out/fan-in；
5. 两个 Child 确实并发；
6. 非法引用导致父子失败传播和迟到隔离；
7. 从 Source 导入到三个 Artifact 的完整 HTTP 集成路径。

当时全量结果：

```text
28 passed
```

M2.2 没有新增数据库字段，因此不创建新 migration，但仍执行：

```bash
alembic current
alembic check
```

结果必须是 `0002_source_contract (head)` 且没有待生成的升级操作。

## 15. 本地手动验证

完整操作见 [本地运行、测试与调试](local-development.zh-CN.md)。

最短路径：

1. `uvicorn epiphany.main:app --reload`
2. 打开 <http://127.0.0.1:8000/docs>
3. `POST /sources`
4. 复制 Source ID
5. `POST /runs`，workflow_type 使用 `episode-research`
6. 查询 `GET /runs/{run_id}`
7. 查询 `GET /runs/{run_id}/events`

成功时检查：

- 3 个 succeeded Task；
- 两个 Child 拥有同一个 `parent_task_id`；
- 3 个 Artifact；
- Run output 指向 Bundle；
- Events 中出现 fan-out、waiting 和 fan-in。

## 16. 这一步学到了什么

- Subagent 可以是受限 Child Task，不需要独立微服务；
- fan-out 是创建分支，真正并发需要单独证明；
- fan-in 是等待和确定性汇合，不一定需要模型；
- 并发执行和串行提交可以同时存在；
- 结构化输出必须在持久化前校验；
- 来源地址合法不等于 Quote 内容真实，两者都要检查；
- 父任务失败必须传播到仍在运行的兄弟任务；
- lease/fencing 能阻止取消后的迟到结果污染状态；
- Fake Provider 能低成本测试生产级运行语义。

## 17. 当前限制

- Fake 输出没有内容质量；
- Manager 目前只是确定性协调任务；
- 单进程 finalization lock 不支持多 Worker；
- 没有 tokens、延迟和真实费用记录；
- 没有模型调用上限的完整实现；
- 没有 Interview Scaffold；
- 没有前端 Trace UI。

下一步 M2.3 是在保持这些已验证运行语义不变的前提下，接入真实托管模型
Provider，并增加调用预算、tokens、延迟和成本记录。
