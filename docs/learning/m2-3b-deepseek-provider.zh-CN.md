# M2.3b：让真实模型接入，并完成第一次受限调用

## 基本信息

- 阶段：M2.3b-1 Provider 离线适配 + M2.3b-2a/2b 受限 live smoke
- 日期：2026-07-28
- Commit：本章节与实现处于同一个 focused commit
- 状态：Mock、dry-run、安全边界与真实 live smoke 均已验证

## 1. 为什么做这一步

M2.2 已经证明两个 Researcher 可以并行执行，M2.3a 又在 Provider 外围装好了
调用次数、Token、耗时和费用的“电表”。但它们一直使用 `FakeProvider`，只能
证明编排正确，不能证明真实模型接口能接通。

这一步开始回答：

- 怎样把 Timeline 和 Theme Task 变成 DeepSeek 请求？
- 怎样要求模型只返回可校验的 JSON？
- 401、429、503 和网络超时分别应该怎样处理？
- 哪些错误可以 retry，哪些错误重试只会继续花钱？
- HTTP 成功但引用错误时，为什么 ModelCall 成功、Task 却失败？
- 已经付费但输出被截断时，怎样仍然保留 Token 和费用？
- 如何确保测试不读取 Key、不联网、不产生费用？

我们先完成离线适配，再单独执行一次显式的小额 live smoke。这样如果接口
代码有 bug，最先失败的是 Mock 测试，而不是账单。

## 2. 生活化类比

可以把 Provider 想成一名翻译兼海关代理。

Epiphany Studio 内部只认识统一格式的“包裹”：

```text
TaskInvocation -> ProviderResult
```

DeepSeek API 使用自己的 HTTP 地址、认证、请求字段和错误码。Provider 负责：

```text
内部 Task
  -> DeepSeek 请求
  -> 检查 HTTP 和响应格式
  -> 内部 ProviderResult 或稳定错误码
```

Workflow 不需要知道 Bearer Token、`/chat/completions` 或 HTTP 429。

这个类比的限制是：现实包裹失败通常不会按字数收费，而模型请求即使输出被
截断或内容不可用，也可能已经产生费用。因此失败响应的 usage 也必须进入
`ModelCall`。

## 3. 完成了什么

当前离线切片已经具备：

1. `DeepSeekProvider` 直接调用 OpenAI-compatible
   `POST /chat/completions`；
2. 当前模型限定为 `deepseek-v4-flash` 或 `deepseek-v4-pro`；
3. Timeline 与 Theme 分别有受约束的中文 Prompt；
4. 素材被明确标记为不可信数据，不能把素材内文字当系统指令；
5. 使用 JSON Output，关闭 thinking，限制输出 Token；
6. 解析实际 model、input/output tokens 和缓存命中数据；
7. 使用 2026-07-27 官方价格快照估算微美元费用；
8. 400/401/402/422 为终止错误，429/500/503/网络错误为可重试错误；
9. Provider 自己不重试，每个真实 HTTP 请求都必须回到 Worker 产生新 attempt；
10. HTTP 客户端超时在账本中记为 `timed_out`；
11. 已付费但截断、过滤或 JSON 非法的响应仍可记录 usage 与预估费用；
12. API Key 使用 `SecretStr`，默认 Provider 仍是 Fake；
13. Base URL 只允许官方 `https://api.deepseek.com`，避免把 Key 和素材发给
    拼错或恶意地址；
14. 单次 Research Task 默认最多发送 24,000 个素材字符、最多生成 2,000
    Token；
15. Provider HTTP 自动化测试使用 `httpx.MockTransport`，smoke 安全测试使用
    Fake Provider，没有网络和费用。
16. 独立 smoke 命令默认只做 dry-run，只有显式 `--execute` 才联网；
17. smoke 固定使用合成素材、Flash 模型、两次调用、一次 attempt 和每次 800
    个输出 Token；
18. smoke 串行发送请求，因此第一个任务失败时，可以在第二个请求发出前取消
    兄弟任务；
19. smoke 使用独立 SQLite 并自动执行 Alembic，便于失败后检查 Trace；
20. 最终摘要只显示 ID、状态、Token、耗时、费用与错误码，不显示内容或 Key。
21. Alembic 在同一进程运行时不会禁用应用 logger，后续日志与排错能力保持
    可用。

本步骤没有修改数据库表，因此没有新增 migration。M2.3a 的 `model_calls`
已经足够保存当前字段。

## 4. 代码模块地图

| 文件或目录 | 作用 | 为什么放在这里 |
| --- | --- | --- |
| `runtime/providers/deepseek.py` | HTTP、认证、错误、usage 和费用 | 隔离厂商协议 |
| `runtime/research_prompts.py` | Timeline/Theme Prompt | Prompt 是领域行为，不藏在 HTTP 代码里 |
| `runtime/providers/base.py` | 统一结果与稳定错误类型 | Worker 不依赖 DeepSeek 错误文本 |
| `config.py` | Provider、Key、模型和输入输出上限 | 运行参数不写死在 Workflow |
| `main.py` | 根据配置构造 Fake 或 DeepSeek | 默认安全，显式开启才联网 |
| `runtime/worker.py` | timeout、retry 和失败用量记账 | 每次副作用都由 durable attempt 管理 |
| `observability.py` | 输出 Provider/ModelCall 元数据 | 能按 ID 排错，但不打印内容 |
| `live_deepseek_smoke.py` | 显式付费验收入口与安全摘要 | 普通测试和启动应用不会误触发 |
| `test_deepseek_provider.py` | HTTP 适配器单元测试 | 精确验证厂商边界 |
| `test_deepseek_research_workflow.py` | 完整双 Agent Mock 集成 | 验证 Provider 接上现有编排与账本 |
| `test_live_deepseek_smoke.py` | dry-run、调用上限与脱敏测试 | 联网前先证明命令本身安全 |

## 5. 背后的技术点

### 5.1 为什么直接用 httpx

DeepSeek 提供 OpenAI-compatible HTTP API。当前只需要一个 endpoint，直接
使用 `httpx.AsyncClient` 比引入完整 SDK 更容易理解：

```text
POST https://api.deepseek.com/chat/completions
Authorization: Bearer ...
```

`httpx` 从 dev dependency 移入 runtime dependency，因为正式运行也需要它。

### 5.2 为什么 Provider 不能偷偷 retry

错误示例：

```text
一个 Task attempt
  -> Provider 内部请求三次
  -> 数据库只有一条 ModelCall
```

这会让调用数和费用少记。正确方式是：

```text
attempt 1 -> HTTP 429 -> ModelCall 1 failed
Worker 判断可重试
attempt 2 -> HTTP 200 -> ModelCall 2 succeeded
```

因此 Provider 的一次 `generate()` 最多发送一次 HTTP 请求。

### 5.3 HTTP 成功不等于业务成功

系统分两层判断：

```text
HTTP/Provider 层
  -> 是否得到合法 JSON、usage 和正常 finish_reason

Research 业务层
  -> 是否符合 Pydantic schema
  -> 引用是否在允许范围
  -> quote 是否逐字存在
```

所以可能出现：

```text
ModelCall = succeeded
Task = failed(invalid_source_reference)
```

它表示厂商确实完成了请求并可能收费，但生成内容不能进入产品 Artifact。

### 5.4 为什么失败也要保存 usage

`finish_reason="length"` 表示输出被截断。内容不可用，但输入和输出 Token
已经发生。Provider 会把 usage 放进错误对象，Worker 再写回失败的
`ModelCall`，避免显示成“零 Token、零费用”。

### 5.5 怎样估算 DeepSeek 费用

官方价格按每一百万 Token 计价。数据库使用微美元：

```text
1 USD = 1,000,000 micros
```

因此计算时恰好可以使用：

```text
cache_hit_tokens  * cache_hit_price_per_million
+ cache_miss_tokens * cache_miss_price_per_million
+ output_tokens     * output_price_per_million
```

并用 `Decimal` 舍入到整数 micros。若响应没有缓存拆分，系统保守地把全部
输入当 cache miss。价格会变化，所以这里永远是 estimate，不是正式账单。

### 5.6 错误怎样决定是否重试

| 场景 | 稳定错误码 | retry |
| --- | --- | --- |
| 400 / 422 | `provider_invalid_request` | 否 |
| 401 / 403 | `provider_authentication_failed` | 否 |
| 402 | `provider_insufficient_balance` | 否 |
| 429 | `provider_rate_limited` | 是 |
| 500 / 502 / 504 | `provider_server_error` | 是 |
| 503 | `provider_overloaded` | 是 |
| HTTP/Worker timeout | `provider_timeout` | 是 |
| 连接或 DNS | `provider_network_error` | 是 |
| 响应协议或 JSON 非法 | `provider_response_invalid` | 否 |

DeepSeek 官方明确列出 400、401、402、422、429、500、503。403、408、
502、504 是本项目的保守工程映射，不是厂商额外承诺。

### 5.7 为什么限制 Base URL 和素材字符数

Provider 会发送真实 Key 和 Source Segment。任意 Base URL 会带来泄露风险，
因此首版只接受官方 HTTPS 地址。

“最多六次调用”也不等于“费用有上限”：一次请求可以很大。所以另设：

```env
EPIPHANY_DEEPSEEK_MAX_SOURCE_CHARS=24000
EPIPHANY_DEEPSEEK_MAX_TOKENS=2000
```

这只是第一道字符级保险丝，还不是精确 Token 预算。

### 5.8 为什么真实调用不能放进 pytest

`pytest` 应该可以反复运行、结果稳定，并且默认没有外部副作用。真实 API 会
受网络、余额、限流和模型版本影响，也会产生费用。如果把它混进普通测试：

```text
开发者运行全量测试
  -> 不知情地联网
  -> 结果因外部服务波动
  -> 费用和失败难以预测
```

所以本项目把两类验证分开：

```text
pytest / dry-run
  -> 默认、免费、确定性、安全边界

python -m epiphany.live_deepseek_smoke --execute
  -> 人主动确认、两次上限、合成素材、保留 Trace
```

这叫 explicit side effect：有费用或外部影响的动作必须通过一个清楚的命令
显式开启，不能藏在“启动应用”或“跑测试”里面。

## 6. 自动化测试

定向运行：

```bash
cd backend
source .venv/bin/activate
pytest tests/test_deepseek_provider.py \
       tests/test_deepseek_research_workflow.py \
       tests/test_live_deepseek_smoke.py -vv
```

主要覆盖：

- URL、Bearer Auth、model、JSON mode、thinking 和 Prompt；
- Timeline/Theme 两种结构；
- usage、缓存 Token 和费用；
- 401/402/429/500/503/timeout/network；
- Provider 内没有隐藏 retry；
- Key、素材和错误响应正文不进入日志；
- 非官方 Base URL 在 HTTP 前被拒绝；
- 双 Researcher 完整 fan-out/fan-in；
- 429 由 Worker 重试并产生独立 ModelCall；
- 401 不重试并取消 sibling；
- timeout 记为 `timed_out`；
- HTTP 成功但非法引用会让 Task 失败；
- 截断响应仍记录非零 Token 与费用；
- DeepSeek 模式下不支持的 Fake Workflow 不会误发 HTTP。
- dry-run 不创建数据库、不联网；
- preflight 只接受 Key 是否存在，无法打印 Key 值；
- 专用 smoke 数据库确实通过 Alembic 升级到当前 head；
- 程序化迁移后，应用 logger 仍能被后续测试和调试捕获；
- smoke harness 固定两次调用、一次 attempt；
- 第一个调用失败时，排队中的第二个任务会在调用 Provider 前取消；
- 最终摘要不包含 Source、Artifact 内容或错误正文。

当前结果：

- 新增 smoke 安全测试：5 项通过；
- 完整测试：73 项通过；
- Ruff lint 与 format check：通过；
- Alembic 当前为 `0003_model_call_trace (head)`，`alembic check` 无差异；
- Provider HTTP 测试使用 MockTransport，smoke 安全测试使用 Fake Provider；
- 没有读取本地 API Key，没有访问互联网，没有费用。

以上是自动化测试结果。真实 smoke 是单独的显式命令，不属于默认 pytest。

## 7. 本地手动验证

先验证离线 Provider 行为：

```bash
cd backend
source .venv/bin/activate
pytest tests/test_deepseek_research_workflow.py -vv
```

测试会在临时 SQLite 中：

```text
导入合成 Source
  -> 创建 episode-research
  -> 发出两个 Mock DeepSeek HTTP 请求
  -> 校验 JSON 和引用
  -> 写入两个 ModelCall 和三个 Artifact
  -> Run succeeded
```

再检查 live-smoke preflight：

```bash
python -m epiphany.live_deepseek_smoke
```

它会显示：

```text
mode = dry-run
network_enabled = false
synthetic_source_only = true
max_model_calls_per_run = 2
max_attempts_per_task = 1
max_concurrency = 1
api_key_status = present 或 absent
```

dry-run 不创建数据库，也不发送请求。真正执行前，把 Key 只放在不会提交的
`backend/.env`：

```env
EPIPHANY_DEEPSEEK_API_KEY=your-local-key
```

然后运行：

```bash
python -m epiphany.live_deepseek_smoke --execute
```

不需要启动 Uvicorn 或 Swagger，也不需要把默认 Provider 改成 DeepSeek。
脚本会自动对 `data/deepseek-live-smoke.db` 执行 Alembic，再创建一次
`episode-research`。成功标准是：

```text
2 个 Research Child Task succeeded
  -> 2 个 ModelCall succeeded
  -> 3 个 Artifact
  -> Run succeeded
```

默认启动应用仍使用：

```env
EPIPHANY_MODEL_PROVIDER=fake
```

所以运行 `uvicorn`、打开 Swagger 或执行普通 pytest 都不会因为本章节而
产生真实调用。

### 7.1 本次真实结果

2026-07-28 执行：

```bash
python -m epiphany.live_deepseek_smoke --execute
```

得到的脱敏结果：

| 项目 | 结果 |
| --- | --- |
| Run | `run_e8ad6452087c479cb84293ae3919201d` |
| 最终状态 | `succeeded / complete` |
| Provider / Model | `deepseek / deepseek-v4-flash` |
| Timeline | attempt 1，525 input，502 output，8,756 ms，214 micros |
| Theme | attempt 1，567 input，707 output，6,679 ms，277 micros |
| 总计 | 1,092 input，1,209 output，15,435 ms，491 micros |
| 预估费用 | 0.000491 USD |
| Artifact | Timeline、Theme、Bundle 共 3 个 |
| retry / timeout / error | 均无 |

脚本退出码为 0，`passed=true`。SQLite 独立查询再次确认：

```text
Run succeeded
3 Tasks succeeded
2 model.call.started
2 model.call.completed
1 workflow.fan_out.started
1 workflow.fan_in.completed
1 run.succeeded
```

这证明的不只是“API 能返回 200”，而是完整链路已经跑通：

```text
合成 Source
  -> 两个真实 DeepSeek 请求
  -> usage / cost 持久化
  -> Pydantic Schema 校验
  -> Source Reference / Quote 校验
  -> deterministic fan-in
  -> Run succeeded
```

这里记录的是一次历史 smoke 结果，不代表以后的固定延迟或账单价格。正文没有
写进日志或文档，Key 也只以 `api_key_status=present` 出现。

## 8. 日志与排错

新增稳定日志事件：

```text
provider.deepseek.request.started
provider.deepseek.request.completed
provider.deepseek.request.failed
```

已有持久化 Event：

```text
model.call.started
model.call.completed
model.call.failed
```

建议按以下顺序排查：

1. 看 Run 和失败 Task 的状态；
2. 看 Task 的 `error_code` 和 `attempt`；
3. 看对应 `model_calls` 的 status、tokens 和 cost；
4. 用 `run_id`、`task_id`、`model_call_id` 搜索 JSON stdout；
5. 401 检查 Key，402 检查余额，429/503 检查服务状态；
6. `provider_response_invalid` 检查请求契约或 Mock fixture；
7. `provider_input_too_large` 检查素材字符上限。
8. smoke 失败后，使用摘要中的 `run.id` 在
   `data/deepseek-live-smoke.db` 对照 Task、ModelCall 和 Event；
9. `api_key_status=absent` 表示 Key 尚未放进 `backend/.env`，dry-run 本身
   仍然是成功的安全检查。

日志只包含 ID、状态、模型、Token、费用和错误码，不包含素材、Prompt、模型
正文、API Key 或 DeepSeek 错误响应正文。

## 9. 这一步学到了什么

- Provider 是厂商协议适配层，不是 Agent 编排框架；
- retry 必须归 durable Worker 管，才能让每次付费副作用都可见；
- 调用成功、业务校验成功和 Workflow 成功是三个不同层次；
- 失败请求也可能收费，所以错误对象有时必须携带 usage；
- JSON mode 只能保证 JSON，不保证符合业务 schema，更不保证引用真实；
- 调用次数、输入体积和输出上限是三种不同的成本保险丝；
- MockTransport 可以真实测试 HTTP 请求和响应逻辑，而不联网；
- SecretStr 和日志脱敏不能替代目标地址白名单。

## 10. 限制与下一步

当前仍然没有：

- 评价 Timeline/Theme 内容质量；
- retry backoff 或 `Retry-After` 调度；
- 精确的输入 Token 预估和美元总预算；
- 保存 DeepSeek response ID；
- 支持代理或任意 OpenAI-compatible Base URL；
- 为 DeepSeek 模式运行旧 `fake-podcast`。

显式 live smoke 工具已经具备：

- 只使用短合成素材；
- `max_calls=2`；
- `max_attempts=1`；
- 使用 `deepseek-v4-flash`；
- 拉长 Worker 总 timeout；
- 串行发送最多两个请求，第一次失败时不继续浪费第二次调用；
- 执行前显示配置，执行后只输出 ID、tokens、耗时和预估费用；
- 不打印 Prompt、响应正文或 Key。

M2.3b 已完成。下一小步进入 M2.4 Interview Scaffold：把 Timeline 与 Theme
Bundle 转换成带来源引用、可供本人继续口述补充的半开放采访脚手架。

官方参考：

- [DeepSeek Quick Start](https://api-docs.deepseek.com/quick_start/pricing-details-usd/)
- [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)

## 完成检查

- [x] 正常路径测试通过
- [x] 失败路径测试通过
- [x] 本地离线手动验证通过
- [x] 日志中无隐私内容
- [x] smoke harness / dry-run 安全验证通过
- [x] 小额 live smoke 通过
- [x] README / Roadmap / Devlog 已同步
- [x] 学习手册已同步
- [x] 已创建 focused commit
