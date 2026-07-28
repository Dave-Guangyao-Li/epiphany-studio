# M3.1：后端 / API 全流程 E2E 验证

## 一句话理解

这一步用一套不含个人隐私的合成素材，像真实客户端一样调用 HTTP API，把
M3.1 从 Source 导入、Agent 执行、人工暂停、Markdown 导出、补充材料、
Resume，一直走到幂等重放。它验证的是多个模块能否协同工作，不是某一个函数
能否单独通过测试。

当前不必等待 Web UI。UI 只是 API 的另一种操作界面；如果后端状态链本身有
问题，先做页面只会让排错多一层。等 M5 有界面后，同一份 fixture 和验收规则
还可以直接复用到 Playwright 浏览器测试。

## 1. 测试数据是什么

固定 fixture 位于：

```text
backend/fixtures/e2e/m3-1-episode.zh-CN.json
```

它包含：

- 3 份初始 Source，模拟时间线笔记、声音与时间的反思、EP0 开场草稿；
- 1 份补充口述转写 Source；
- 1 个主题和 1 个稳定 `submission_id`；
- `synthetic: true` 和 `contains_personal_data: false` 标记。

这些内容是专门为测试编写的合成材料，不是用户日记或真实播客原稿，因此可以
安全提交到 Git。API Key、真实生成结果、数据库和运行日志仍不能提交。

## 2. 三种运行方式

进入后端并启用虚拟环境：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
```

### 2.1 Dry-run：只看安全边界

```bash
python -m epiphany.checkpoint_e2e --provider deepseek
```

没有 `--execute` 时，只读取并校验 fixture、检查 Key 是否存在、打印调用上限
和输出路径。它不会请求 DeepSeek，也不会创建 E2E 数据库或输出文件。

### 2.2 Fake：免费跑完整路径

```bash
python -m epiphany.checkpoint_e2e --provider fake --execute
```

Fake Provider 会经过真实 FastAPI、Worker、Orchestrator、SQLite 和 Markdown
导出代码，但不会联网，Token 和费用都是 0。它最适合日常回归和排查本项目
自己的状态机、幂等与数据保存问题。

### 2.3 DeepSeek：显式进行小额真实调用

先把 Key 和结算币种只放在被 Git 忽略的 `backend/.env`：

```env
EPIPHANY_DEEPSEEK_API_KEY=your-local-key
EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY
```

再明确执行：

```bash
python -m epiphany.checkpoint_e2e --provider deepseek --execute
```

这条命令最多允许 3 次模型调用，每个模型 Task 只尝试一次，同时最多只有一个
请求，模型固定为 `deepseek-v4-flash`。失败不会在背后无限重试或继续花钱。
预检中的费用上界只是保护性估算，不是厂商账单保证。

运行 E2E 不需要先启动 Uvicorn 或打开 Swagger。命令会在进程内启动 FastAPI
lifespan 和真正的 Worker，再通过 HTTP ASGI 接口操作它。

## 3. 完整状态链

```text
导入 3 个初始 Source
  -> POST /runs
  -> Timeline / Theme Researcher
  -> fan-in Research Bundle
  -> Interviewer 生成 Interview Scaffold
  -> waiting_for_user / awaiting_interview_response
  -> 第一次导出 interview-scaffold.md
  -> 导入 1 个补充 voice_note_transcript Source
  -> POST /runs/{run_id}/resume
  -> succeeded / complete
  -> 原请求完全相同地再 Resume 一次
  -> idempotent_replay = true
  -> 再次导出并比较 SHA-256
```

等待点应有 4 Tasks、4 Artifacts、3 ModelCalls。Resume 后没有新模型 Task，
最终应有 4 Tasks、5 Artifacts、3 ModelCalls。新增的第五个 Artifact 是
`user_material_submission`，只保存补充 Source 的引用。

两次导出内容应完全相同。这不是漏用了补充材料，而是 M3.1 的明确边界：
Resume 只可靠接收材料；M3.2 Editor 才会读取它并生成新的播客稿。

## 4. 运行后看哪四类证据

默认输出都在被 Git 忽略的位置：

| 路径 | 用途 |
| --- | --- |
| `data/checkpoint-e2e.db` | Source、Run、Task、Event、ModelCall 和 Artifact 的持久化真相 |
| `artifacts/checkpoint-e2e/runtime.jsonl` | 一行一个 JSON 的 HTTP、Worker 与 Provider 操作日志 |
| `artifacts/checkpoint-e2e/report.json` | 机器可读的通过项、数量、Token、费用、事件和文件摘要 |
| `artifacts/checkpoint-e2e/interview-scaffold.md` | 可以直接打开检查的采访脚手架 |

推荐查看顺序：

1. 先看终端最后的 `passed`、`failures` 和路径；
2. 用 `report.json` 判断哪一项验收没有通过；
3. 用 `run_id` 在 `runtime.jsonl` 中追踪执行顺序；
4. 必要时只读查看 SQLite；
5. 最后再检查 Markdown 的可读性和来源标签。

日志和 report 只保存 ID、状态、数量、耗时、Token、费用和错误码，不保存
API Key、Source 正文、Prompt 或生成正文。Markdown 内容单独存放，数据库则
仍包含 Source 与 Artifact 正文，因此二者都属于本地文件。

## 5. 已完成的 Fake E2E 证据

2026-07-28 的 Fake 全流程结果为：

- 完整后端测试基线：`120 passed`；
- 等待点：4 Tasks、4 Artifacts、3 个成功 Fake ModelCalls；
- 最终状态：4 Tasks、5 Artifacts、3 ModelCalls；
- 首次 Resume 生效，相同请求重放被识别为幂等 replay；
- 两次 Markdown 导出的 SHA-256 都是
  `68d24de09fcfbf670e90be9ffc950b4f441921617f7c0870ff513d3fee2e6a27`；
- 结构化日志全部可解析，合成 Source 正文没有出现在日志中；
- Token 和本地费用均为 0；
- Ruff、Alembic current/check 也通过。

这证明当前项目自己的 API、Worker、状态机、数据库、日志、导出和 Resume
可以连成一条可重复执行的完整路径。

## 6. DeepSeek 真实 E2E 的诚实结果

同一天进行了 3 次彼此隔离、有调用上限的 DeepSeek E2E 尝试：

1. 前两个 Researcher 成功，Interviewer 输出达到上限，被记录为
   `provider_output_truncated`；
2. 提高受控输出空间后，两个 Researcher 再次成功，Interviewer 仍在完整
   JSON 结束前达到上限；
3. Timeline 成功，Theme 遇到一次 `provider_network_error`，Run 按设计失败，
   没有无限重试。

三次尝试保留了独立的数据库和脱敏日志。本地按配置的 CNY 价格表累计估算为：

```text
¥0.035096
```

这是本地 Token 计价估算，不是 DeepSeek Dashboard 的最终结算承诺。

结论必须如实写成：**DeepSeek Provider 已经真实进入 E2E、成功完成多次研究
调用，并且失败账本、错误传播和成本边界有效；但真实 DeepSeek 版本尚未完整
走到 `waiting_for_user -> Resume -> succeeded`，所以不能宣称 live E2E 已
通过。** 当前完全通过的是 Fake E2E。

这类结果正是提前做 E2E 的价值：它在 UI 之前暴露了模型输出长度、严格 JSON
契约与外部网络可靠性问题，同时没有掩盖已经验证成功的后端编排行为。

## 7. 自动化测试

E2E harness 自身由不联网测试保护：

```bash
pytest tests/test_checkpoint_e2e.py -vv
```

它覆盖 dry-run 不产生运行文件、Fake 完整 HTTP 路径、日志脱敏、已有活跃
Task 的数据库拒绝复用等边界。常规 `pytest` 永远不会读取 Key 或执行真实
DeepSeek 请求；真实调用只能由人工显式加 `--execute`。

## 8. 当前产物与下一步

当前生成的 `interview-scaffold.md` 是带来源引用的采访问题脚手架，不是最终
播客稿，也不是 Show Notes。M3.1 在收到补充口述文字后只完成可靠登记。

M3.2 会增加 Editor Task：

```text
Interview Scaffold + supplemental Sources
  -> Editor
  -> podcast draft Markdown
  -> Show Notes
  -> human review
```

到那时，E2E 才会继续断言最终播客 Markdown 已生成并包含补充材料。M5 Web UI
完成后，页面测试会复用本章节的 fixture、状态数量、幂等提交和导出断言，再
增加浏览器点击、表单与可视反馈；不会另造一套互相矛盾的测试流程。
