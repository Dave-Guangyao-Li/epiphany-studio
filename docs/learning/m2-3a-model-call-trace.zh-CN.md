# M2.3a：先把模型调用的“电表”装好

## 基本信息

- 阶段：M2.3a 零费用 Provider 基础设施
- 日期：2026-07-27
- Commit：本章节与实现处于同一个 focused commit
- 状态：已验证

## 1. 为什么做这一步

下一步要把 Fake Provider 换成真正收费的模型 API。真正调用以前，系统必须
先回答几个很现实的问题：

- 这次 Run 到底调用了几次模型？
- 两个并行 Child 会不会一起越过调用上限？
- 一次调用用了多久、多少输入和输出 Token？
- retry 是否又产生了一次可能收费的请求？
- timeout 以后，后台是否留下了可追查的记录？
- 使用不同厂商、不同币种时，费用怎样表达？

如果先接模型、后补这些能力，一旦代码重试失控，我们只能在厂商账单里发现
问题，而且很难对应到具体 Run、Task 和 attempt。

因此本步骤不追求模型质量。它先安装“调用电表和保险丝”，继续用完全免费的
Fake Provider 验证外围系统。

## 2. 生活化类比

可以把模型 API 想成按表收费的出租车：

- Provider 是出租车公司；
- Model 是车型；
- Task 是这次出行目的；
- attempt 是第几次叫车；
- ModelCall 是一张独立行程单；
- Token 是里程；
- estimated cost 是预估车费；
- Run 调用上限是预算保险丝。

正确顺序不是“车开走后再看看有没有超预算”，而是：

```text
检查预算
  -> 预留一张行程单
  -> 真正叫车
  -> 回填里程、耗时、费用和结果
```

类比的限制是：模型服务的 Token 不是物理距离，而且网络超时时，服务端可能
已经完成工作并产生费用。因此 timeout 记录只能说明客户端没有按时收到结果，
不能保证厂商一定没有计费。

## 3. 完成了什么

现在系统可以在不访问互联网的情况下验证：

1. 每次 Provider attempt 开始前创建一条持久化 `ModelCall`；
2. Run 的 `model_call_count` 在预留调用时增加，而不是等成功后才增加；
3. 成功后记录 provider、model、input/output tokens、duration 和预估费用；
4. retry 的每个 attempt 独立记账；
5. timeout 记录为 `timed_out`，并继续遵守原有有界 retry；
6. 达到单 Run 上限后，在进入 Provider 以前拒绝下一次调用；
7. `GET /runs/{run_id}` 返回完整 `model_calls`，重启后仍可查询；
8. Events 和 JSON stdout 日志都能用 Run、Task、attempt、ModelCall ID 关联；
9. 日志和 Events 不包含素材、prompt、模型响应或 API Key。

当前默认上限是六次：

```env
EPIPHANY_MODEL_MAX_CALLS_PER_RUN=6
```

## 4. 代码模块地图

| 文件或目录 | 作用 | 为什么放在这里 |
| --- | --- | --- |
| `models.py` | 定义持久化 `ModelCall` | 调用历史属于产品运行状态 |
| `0003_model_call_trace.py` | 创建 `model_calls` 表 | Alembic 是 schema 唯一来源 |
| `runtime/providers/base.py` | Provider 结果、Token、费用和错误契约 | Workflow 不依赖具体厂商 |
| `runtime/providers/fake.py` | 零网络测试实现 | 将运行时与模型质量分开验证 |
| `runtime/model_call_ledger.py` | 预留、结束、预算和恢复调用账本 | 把计费可靠性从任务执行中分离 |
| `runtime/worker.py` | 执行 Provider，并把生命周期交给 Ledger | Worker 负责任务执行而非账本细节 |
| `config.py` | 定义单 Run 最大调用数 | 运维限制不能写死在业务代码里 |
| `schemas.py` | 定义 API 中的 `ModelCallView` | 客户端需要可观察调用 Trace |
| `services.py` | 查询 Run 时加载调用记录 | SQLite 仍是状态真相 |
| `test_model_call_trace.py` | 正常、retry、timeout、超限测试 | 不使用真实 Key 就能稳定复现 |

## 5. 背后的技术点

### 5.1 为什么必须在调用前预留

旧实现只在 Task 成功时执行：

```text
run.model_call_count += 1
```

这会漏掉 timeout、网络错误和失败后 retry，而这些请求都可能已经产生费用。

新实现先写：

```text
ModelCall(
  status="started",
  provider=...,
  model=...,
  cost_currency=...
)
```

并同时增加 Run 计数。无论后面成功还是失败，这次尝试都不会消失；即使失败
发生在 Provider 返回 Token usage 以前，Trace 也保留请求开始前已经确定的
费用币种。

### 5.2 为什么 retry 要单独记账

Task ID 在重试时保持不变，但 `attempt` 从 1 增加到 2。数据库使用：

```text
UNIQUE(task_id, attempt)
```

它同时做到：

- 同一次 attempt 不会重复记两张账单；
- 新的 retry 可以有自己的调用记录；
- 以后可以解释“一个 Task 为什么产生两次 API 请求”。

### 5.3 并行任务如何共同遵守预算

Timeline 和 Theme 两个 Child 会并发执行。如果它们分别执行：

```text
先读当前次数 -> 再决定是否调用
```

就可能同时读到“还剩一次”，然后一起越过上限。

当前是单进程 Worker，因此使用一个很短的 `asyncio.Lock` 串行保护：

```text
检查当前调用数 + 插入预留记录
```

耗时的 Provider 工作仍在锁外并行执行。以后如果升级为多进程或多机器
Worker，这个锁不再够用，需改成 PostgreSQL 行锁或等价的原子预算操作。

### 5.4 为什么费用使用整数 micros

直接用浮点数保存钱可能出现：

```text
0.1 + 0.2 != 0.3
```

因此数据库保存一单位货币的百万分之一：

```text
estimated_cost_micros = 400000
cost_currency = "CNY"
```

表示预估 `0.4 CNY`。币种单独保存，因为 DeepSeek、Qwen、Kimi 或其他
Provider 可能使用不同结算币种，不能把它们直接相加。

一条 `ModelCall` 当前保存一组不可拆开的“估算金额 + 币种”，不是厂商发票。
历史 USD 与 CNY 记录可以并存，但统计时必须按币种分组。将来如果 UI 需要
显示换算币种，应额外保留汇率、来源和日期，不能覆盖原始历史估算。

### 5.5 ModelCall、Event 和 stdout 日志有什么区别

- `ModelCall`：结构化调用账本，适合统计次数、Token、耗时和费用；
- `Event`：Run 内可重放的产品执行过程；
- stdout 日志：排查 Worker、数据库和网络等运行问题。

同一个事实可以在三处有不同视角，但素材正文和模型响应不能进入日志或
Event。

### 5.6 为什么 Fake Provider 也产生 ModelCall

Fake Provider 没有真正调用模型，但它要通过和真实 Provider 完全相同的
Worker 边界。这样才能在免费阶段证明：

- 预算判断位置正确；
- retry 会独立记账；
- timeout 状态正确；
- Run API 能查询记录；
- 并发不会突破上限。

Fake 的 tokens 和 cost 都是零。下一步换成 DeepSeek 时，只替换产生结果的
适配器，不重写这些可靠性规则。

## 6. 自动化测试

专门测试文件：

```text
backend/tests/test_model_call_trace.py
```

覆盖：

- `test_usage_latency_and_cost_are_persisted_without_network`
  - 两个并行 Fake 调用成功；
  - Token、耗时、费用和币种进入数据库与 Run API；
- `test_retry_attempts_are_each_accounted_for`
  - 第一次失败、第二次成功；
  - 两个 attempt 各有一条记录；
- `test_call_limit_stops_before_an_extra_provider_invocation`
  - 上限设为 1；
  - 第二个 Task 失败，但 Counting Provider 只被调用一次；
- `test_timeout_is_retryable_and_each_attempt_is_traced`
  - 两次 timeout 都被记录；
  - 达到最大 attempt 后 Run 失败。

运行命令：

```bash
cd backend
source .venv/bin/activate
pytest tests/test_model_call_trace.py -vv
ruff check src tests
ruff format --check src tests
pytest -q
```

本次结果：

- Ruff lint：通过；
- Ruff format：通过；
- 全套测试：32 项通过；
- 测试全部使用 Fake Provider，无网络调用和 API 费用。

数据库 migration 也在全新临时 SQLite 上验证：

```bash
alembic upgrade head
alembic current
alembic check
alembic downgrade 0002_source_contract
alembic upgrade head
```

结果为 `0003_model_call_trace (head)`，没有未生成的 schema 变更，且
downgrade/upgrade 往返通过。

## 7. 本地手动验证

### 第一步：升级数据库

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

不执行这一步就会遇到：

```text
no such table: model_calls
```

### 第二步：启动后端

```bash
uvicorn epiphany.main:app --reload
```

打开：

<http://127.0.0.1:8000/docs>

### 第三步：通过 Swagger 操作

1. 使用 `POST /sources` 导入一段合成文字；
2. 复制返回的 `source.id`；
3. 使用 `POST /runs` 创建 `episode-research`；
4. 请求体中放入该 `source_id`；
5. 使用 `GET /runs/{run_id}` 查询结果。

完成后应看到：

```json
{
  "model_call_count": 2,
  "model_calls": [
    {
      "provider": "fake",
      "model": "fake-v1",
      "status": "succeeded",
      "input_tokens": 0,
      "output_tokens": 0,
      "estimated_cost_micros": 0,
      "cost_currency": "USD"
    }
  ]
}
```

实际会有两条 `model_calls`，分别对应 Timeline 和 Theme Child。

本次实际手动结果：

- Run 最终为 `succeeded`；
- `current_step` 为 `complete`；
- `model_call_count` 为 2；
- 两条记录都是 `fake / fake-v1 / succeeded`；
- Token 和预估费用都是 0；
- Event 中各有两条 `model.call.started` 和 `model.call.completed`。

## 8. 日志与排错

新增稳定 Event / log 名称：

```text
model.call.started
model.call.completed
model.call.failed
model.call.limit_exceeded
```

新增主要错误码：

```text
provider_timeout
model_call_limit_exceeded
```

建议排查顺序：

1. 看 Run 的 `status` 和 `current_step`；
2. 看失败 Task 的 `attempt` 和 `error_code`；
3. 看 `model_calls` 是否存在、处于什么状态；
4. 用 `run_id`、`task_id`、`model_call_id` 搜索 stdout 日志；
5. 如果表不存在，执行 `alembic upgrade head`；
6. 如果达到上限，检查 `EPIPHANY_MODEL_MAX_CALLS_PER_RUN` 和 retry 次数。

## 9. 这一步学到了什么

- “调用次数”不能只数成功结果，因为失败与 timeout 也可能收费；
- 预算控制必须发生在副作用之前；
- retry 是同一个逻辑 Task 的新 attempt，也是新的外部副作用；
- 并发上限和费用上限是两种不同约束；
- 钱适合用整数最小精度存储，不适合直接依赖浮点数；
- Provider 抽象不仅用于换模型，也用于把业务编排与网络、计费隔离；
- Fake 的价值不是假装有 AI，而是让外围可靠性可以稳定、免费地测试。

## 10. 限制与下一步

这一步仍然没有：

- 发送任何 DeepSeek 网络请求；
- 读取 `DEEPSEEK_API_KEY`；
- 生成 Timeline/Theme 真实 prompt；
- 处理 HTTP 401、402、429、500、503；
- 从真实 API 响应读取 usage；
- 验证真实模型的 JSON 和引用质量；
- 将费用显示成面向普通用户的金额。

下一步 M2.3b 将实现一个小型 DeepSeek Provider，先只用合成素材完成两次
受限调用。真实 Key 只放在本地 `.env`，不进入 Git、日志或测试 fixture。

## 完成检查

- [x] 正常路径测试通过
- [x] 失败路径测试通过
- [x] 临时数据库 migration 与本地 API 手动验证通过
- [x] 日志中无隐私内容
- [x] README / Roadmap / Devlog 已同步
- [x] 学习手册已同步
- [x] 已创建 focused commit
