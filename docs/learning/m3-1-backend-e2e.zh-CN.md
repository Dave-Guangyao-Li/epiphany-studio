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
每个 Researcher 的原始素材最多 8,000 字，Interviewer 的已校验合并 Bundle
最多 24,000 字；两者使用不同边界，因为聚合结果会比单份原始输入更大。
预检中的 `0.08` 使用所配置的 billing currency（本次记录为 CNY），它是计划
阈值，不是程序强制的花费上限，也不是厂商账单保证。

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

- 完整后端测试基线：`130 passed`；
- 等待点：4 Tasks、4 Artifacts、3 个成功 Fake ModelCalls；
- 最终状态：4 Tasks、5 Artifacts、3 ModelCalls；
- 首次 Resume 生效，相同请求重放被识别为幂等 replay；
- 三份初始素材均不少于 600 字，补充口述不少于 800 字；
- Fake 从素材中确定性提取具体文字、时间、主题和 quote，不再输出英文 filler；
- Markdown 正文使用 `[S1]`，文末按“来源标题 + 片段编号”集中索引，不显示
  原始 `src_...#seg_...`；
- 结构化日志全部可解析，合成 Source 正文没有出现在日志中；
- Token 和本地费用均为 0；
- Ruff、Alembic current/check 也通过。

这证明当前项目自己的 API、Worker、状态机、数据库、日志、导出和 Resume
可以连成一条可重复执行的完整路径。

## 6. DeepSeek 真实 E2E 的诚实结果

### 6.1 第一次完整素材 Run：输入边界失败

两个 Researcher 均成功并完成 fan-in，但合并后的研究 Bundle 约 14,750 字。
Interviewer 错误复用了原始 Source 的 8,000 字上限，因此在第三个网络请求
发出前以 `provider_input_too_large` 失败。

这次 Run 已产生两次真实调用：

- 4,748 input tokens；
- 3,712 output tokens；
- 本地估算 CNY 0.012172。

失败记录没有被删除。它证明 ModelCall 账本和费用追踪有效，也促使代码把
8,000 字原始素材边界与 24,000 字研究 Bundle 边界分开。

### 6.2 修复后的完整 Run：通过

同一套完整合成素材随后成功走完：

```text
Source -> Research -> Scaffold -> waiting_for_user
  -> supplemental Source -> Resume -> succeeded
```

关键结果：

- Run：`run_44c9db75a74744ac940efd2d27172107`；
- 4 个 Sources、21 个 Segments；
- 4 个 Tasks 全部成功；
- 等待点为 4 Artifacts、3 ModelCalls、26 Events；
- Resume 后为 5 Artifacts、3 ModelCalls、29 Events；
- 10,046 input tokens、6,670 output tokens；
- 三次 Provider 耗时合计 52,003 ms；
- 本地估算 CNY 0.023386；
- 102 行 JSONL 日志全部可解析，没有 error code，素材正文和 Key 均未进入日志。

两次真实验收合计本地估算 CNY 0.035558。这是本地价格表估算，不是 DeepSeek
Dashboard 的最终结算承诺。

内容人工复核认为脚手架已经包含具体时间、场景、认知变化、六道采访问题和
11 个可读来源，不再是机械 filler；同时发现模型把“计划录 Episode 0”轻微
夸大成“Episode 0 已发布”。因此 Prompt 已增加“计划、草稿、愿望不得改写成
已完成事实”的约束。引用合法仍不等于语义蕴含，正式内容仍需要人工确认。

完整失败分析、数据库数量、Event 顺序、费用表和内容复核见
[realistic E2E 运行证据与内容复核](m3-1-realistic-e2e-evidence.zh-CN.md)；
技术改动和学习总结见
[一次接近真实用户的 DeepSeek 全流程验收](m3-1-realistic-e2e.zh-CN.md)。

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
