# M3.1 realistic E2E：运行证据与内容复核

## 基本信息

- 日期：2026-07-28
- 数据：三份完整初始合成 Source + 一份完整补充口述
- Provider：Fake 与 `deepseek-v4-flash`
- 隐私：不含个人素材；数据库、日志和生成 Markdown 仅保存在 Git 忽略目录

这份文档只保存本次验收的**事实证据**。为什么这样设计、改了哪些模块，见
[一次接近真实用户的 DeepSeek 全流程验收](m3-1-realistic-e2e.zh-CN.md)；
复现命令见 [M3.1 后端 / API E2E runbook](m3-1-backend-e2e.zh-CN.md)。

## 1. 去哪里看结果

每次运行会留下四类本地证据：

| 文件 | 看什么 |
| --- | --- |
| `report.json` | 是否通过、数量、Events、Token、费用、脱敏检查 |
| `runtime.jsonl` | 每个 HTTP、Worker 和 Provider 操作的结构化日志 |
| `interview-scaffold.md` | 人工阅读内容质量和引用 |
| `*.db` | Source、Task、Artifact、ModelCall 与 Event 的持久化真相 |

这些目录已被 Git 忽略，不会提交真实模型输出、数据库或日志。

快速查看：

```bash
jq '{passed,failures,usage,checks}' \
  artifacts/realistic-e2e-deepseek-local/report.json

rg 'provider.deepseek|model.call|run.waiting|run.resume' \
  artifacts/realistic-e2e-deepseek-local/runtime.jsonl

open artifacts/realistic-e2e-deepseek-local/interview-scaffold.md

sqlite3 -readonly data/realistic-e2e-deepseek-local.db \
  "SELECT kind,status,error_code FROM tasks ORDER BY created_at;"
```

排查顺序：

1. `report.json` 的 `failures`；
2. Run 的 `status/current_step`；
3. 失败 Task 的 `error_code`；
4. 对应 ModelCall 的状态、Token 和耗时；
5. 用 `run_id/task_id/request_id` 在 JSONL 中串联日志；
6. 最后才打开数据库正文或生成 Markdown。

日志不会保存 Source 正文、Prompt、模型回复或 API Key。

## 2. 最终 Fake 回归：完整且零费用

最后一轮 Fake 使用和 DeepSeek 相同的三份初始素材与一份补充口述，不再使用
几句占位文字：

```text
Run: run_8f875f1a376741e9abc8bdbee37d8737
waiting_for_user -> Resume -> succeeded
```

本地证据：

```text
backend/data/realistic-e2e-fake-v4.db
backend/artifacts/realistic-e2e-fake-v4/runtime.jsonl
backend/artifacts/realistic-e2e-fake-v4/report.json
backend/artifacts/realistic-e2e-fake-v4/interview-scaffold.md
```

结果是 4 个 Source、21 个 Segment、4 个成功 Task、5 个最终 Artifact 和 3
个零 Token、零费用 Fake ModelCall。45 行日志均为合法 JSON，素材正文没有
进入日志。Markdown 有 1,942 个字符，SHA-256 为：

```text
7ad6a3bcf5f74cbe34e593bd9daf196ca319c7b163a0e23dc4d47965c1723e61
```

人工打开后可以看到真实素材中的时间、原话和主题，不再出现英文 filler；
正文使用 `[S1]`，文末把它解释成来源标题与片段编号。Fake 的价值不是模拟
DeepSeek 文笔，而是免费、确定性地证明整条产品链路可读且可回归。

## 3. 第一次真实 Run：失败，但很有价值

第一次使用完整 fixture 的 DeepSeek Run 没有伪装成成功：

| 项目 | 结果 |
| --- | --- |
| Timeline | 成功 |
| Theme | 成功 |
| Interviewer | 联网前失败 |
| 错误码 | `provider_input_too_large` |
| 已产生调用 | 2 次 |
| Token | 4,748 input / 3,712 output |
| 本地估算费用 | CNY 0.012172 |

两个 Researcher 已经成功生成较丰富结果，fan-in 也完成。失败发生在
Interviewer 的输入保护层：完整 Research Bundle 超过错误复用的 8,000 字
限制。

这证明三件事：

1. 失败调用也会进入 ModelCall 账本；
2. 第三个请求在发送前被拦截，没有产生隐藏费用；
3. 长素材测试发现了短 smoke test 永远不会发现的容量设计问题。

修复是分离两种长度边界，而不是删除输入保护。

本地证据：

```text
backend/data/realistic-e2e-deepseek-v1.db
backend/artifacts/realistic-e2e-deepseek-v1/runtime.jsonl
backend/artifacts/realistic-e2e-deepseek-v1/report.json
```

## 4. 第二次真实 Run：全流程通过

修复以后，同一主题和同一套完整素材成功走完：

```text
Run: run_44c9db75a74744ac940efd2d27172107
waiting_for_user -> Resume -> succeeded
```

本地证据：

```text
backend/data/realistic-e2e-deepseek-v2.db
backend/artifacts/realistic-e2e-deepseek-v2/runtime.jsonl
backend/artifacts/realistic-e2e-deepseek-v2/report.json
backend/artifacts/realistic-e2e-deepseek-v2/interview-scaffold.md
```

### 4.1 持久化数量

| 检查点 | Source | Segment | Task | Artifact | ModelCall | Event |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 等待用户时 | 3 | 15 | 4 | 4 | 3 | 26 |
| 导入补充并 Resume 后 | 4 | 21 | 4 | 5 | 3 | 29 |

四个 Task 全部成功：

1. `research_manager`
2. `timeline_research`
3. `theme_research`
4. `build_interview_scaffold`

等待时已有四个内容 Artifact；Resume 只新增第五个
`user_material_submission`，没有创建第四次模型调用。

### 4.2 Event 顺序

关键过程可以从 29 个 Event 中回放：

```text
run.created
  -> run.started
  -> workflow.fan_out.started
  -> Timeline completed
  -> workflow.fan_in.waiting
  -> Theme completed
  -> workflow.fan_in.completed
  -> workflow.interview_scaffold.queued
  -> Interviewer started + completed
  -> workflow.user_input.requested
  -> run.waiting_for_user
  -> run.resumed
  -> workflow.user_material.accepted
  -> run.succeeded
```

这也解释了 fan-out / fan-in：

- **fan-out**：Manager 把一份工作分给 Timeline 和 Theme 两个同级任务；
- **fan-in**：两个同级任务都完成以后，Manager 才把结果合并成一个 Bundle。

它不是两个模型“互相聊天”，而是后端根据数据库状态确定性调度。

### 4.3 三次模型调用

| Task | Input tokens | Output tokens | 耗时 | 本地估算费用 |
| --- | ---: | ---: | ---: | ---: |
| Timeline | 2,415 | 2,007 | 17.250 s | CNY 0.006429 |
| Theme | 2,457 | 2,119 | 17.103 s | CNY 0.006695 |
| Interviewer | 5,174 | 2,544 | 17.650 s | CNY 0.010262 |
| 合计 | 10,046 | 6,670 | 52.003 s | CNY 0.023386 |

这是 Epiphany Studio 根据配置价格表计算的本地估算。它用于 Run 级分析，
不等于 DeepSeek Dashboard 的最终账单；Dashboard 还可能有延迟、舍入或账户
侧计费口径。

加上第一次失败 Run 已实际产生的两次调用，本轮两次真实验收合计本地估算：

```text
CNY 0.035558
```

### 4.4 日志

成功 Run 产生 102 行合法 JSON 日志，其中：

- 3 次 `provider.deepseek.request.started/completed`；
- 3 次 `model.call.started/completed`；
- 4 次 `source.imported`；
- 1 次 `run.resume.accepted`；
- 1 次 `run.resume.idempotent_replay`；
- 0 个 error code；
- 日志脱敏检查通过，合成 Source 正文和 Key 都未出现。

65 条 `http.request.completed` 主要来自每秒一次的状态轮询。它不影响正确性，
但说明未来可以降低轮询日志噪声，或者用 SSE 替代频繁 polling。

### 4.5 自动验收项

`report.json` 中以下检查均为 `true`：

- 初始与补充 Source 导入；
- 等待检查点、Task/Artifact/ModelCall 数量；
- 四个 Task 和三个 ModelCall 全部成功；
- 等待前没有 `run.succeeded`；
- Markdown 标题、章节、问题和可读引用结构；
- 第一次 Resume 生效；
- 相同 submission 幂等重放；
- submission Artifact 只保存引用，不复制口述正文；
- Resume 只新增三个预期 Event；
- Resume 前后 Scaffold SHA-256 相同；
- Scaffold 不包含尚未进入 Editor 的补充口述；
- JSONL 可解析且完成脱敏检查。

## 5. 生成内容的人工质量复核

这次 Markdown 不再是 filler。它从多份素材中形成了三个有顺序的部分：

1. 第一次录音与拖延的五年；
2. 重听旧录音的触动；
3. 重新开始时设置的边界与行动。

有价值的地方：

- 使用了 2021、2023、2025、2026 等具体时间；
- 抓住“三秒停顿”“提纲从一页变五页”“准备变成拖延”等具体证据；
- 每个 section 有两道可以继续口述的问题；
- 指出了“2021 具体担忧”和“2025 日记具体瞬间”两个材料缺口；
- 正文用 `[S1]`，文末列 11 个可读来源，不再暴露 `src_...#seg_...`；
- 标题、结构、引用范围和 JSON Schema 全部通过机器校验。

但人工复核也发现一处重要问题：

```text
模型写成：第一期作为 Episode 0 已发布
原素材只说：它更像 Episode 0，想先按下录音键、试着寄出第一封语音信
```

引用确实指向相关段落，但“相关”不等于“直接支持已经发布”。这说明：

> 引用合法性校验能证明模型没有引用不存在的 Source，却不能自动证明每个
> 自然语言断言都被原文完整蕴含。

本次已经在 Interviewer Prompt 中增加约束：计划、草稿、愿望、尝试和未确定
事项不得改写成已完成或已发布的事实；证据不足时应改成 question 或
material_gap。自动化测试会保护这条 Prompt 规则。

但 Prompt 只能降低概率，不能提供形式化保证。进入 Editor 阶段以后仍需要：

- 人工审阅事实状态；
- 或增加逐条 claim-to-source 校验；
- 在正式发布前保留可编辑和确认步骤。

我们保留这次原始输出作为测试证据，没有为了得到“更漂亮的结果”再次付费
重跑或修改历史 Artifact。

## 验收结论

这次 Run 可以判定为：

- **工程 E2E：通过**；
- **日志、账本与脱敏：通过**；
- **引用可追踪与人类可读：通过**；
- **脚手架内容质量：可用，但需要人工事实复核**；
- **最终播客稿：尚未进入 M3.2，因此不在本次通过范围内**。
