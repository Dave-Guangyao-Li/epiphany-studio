# M3.1：一次接近真实用户的 DeepSeek 全流程验收

## 基本信息

- 阶段：M3.1 Realistic E2E Acceptance
- 日期：2026-07-28
- Commit：本章节与实现处于同一个 focused commit
- 状态：Fake 与 DeepSeek 全流程均已完成；真实内容质量已人工复核

## 一句话理解

这不是用两三句占位文字证明接口“能返回 200”，而是用四份前后连贯、明确标记
为合成数据的长素材，模拟一个用户真的在准备一期播客：

```text
三份初始素材
  -> Timeline / Theme 研究
  -> Interview Scaffold
  -> 持久化暂停
  -> 导入一份完整补充口述
  -> Resume
  -> 核对 Markdown、数据库、Event、日志、Token 与费用
```

Fake 模式负责免费、稳定地证明工程链路；DeepSeek 模式负责证明真实模型面对
较完整素材时，链路和内容都能工作。两者不能互相替代。

## 1. 为什么要做这次验收

之前的短素材测试有两个价值：

- 快速验证状态机、Schema 和 API；
- 失败时容易定位是哪个函数或状态转换出错。

但它不能回答以下产品问题：

- 三份几百字的素材合并后，Prompt 会不会超过边界；
- 模型能否从不同来源中找出具体时间、矛盾和变化；
- 采访问题是不是有内容，而不是重复主题的车轱辘话；
- 导出的引用普通人能不能看懂；
- 用户补充一整段口述以后，系统是否仍然可靠；
- 一次真实 Run 到底调用几次模型、花多少 Token、多少钱；
- “引用了某段素材”是否真的等于“这句话被素材支持”。

因此，这次验收的目标不是追求一个漂亮截图，而是同时检查：

1. **产品可读性**：Markdown 像一份可以使用的采访提纲；
2. **来源可追踪**：人看见 `[S1]`，数据库仍保存原始 Source/Segment ID；
3. **运行可靠性**：暂停、恢复、幂等和失败都留下证据；
4. **费用可解释**：每次真实调用都有 Token、耗时、币种和估算费用；
5. **局限可见**：模型内容有轻微失真时，不把它包装成“全部正确”。

## 2. 生活化类比

可以把它理解为一次新餐厅的试营业。

单元测试像分别检查炉灶、冰箱和收银机。短 smoke test 像只做一杯水，确认
服务员能把订单送到桌上。这次 realistic E2E 则像请来一桌试吃客人，真的点
前菜、主菜和饮料，再检查出餐顺序、账单、服务记录和味道。

类比的边界是：本次素材仍是安全的**合成数据**，不是真实用户隐私；但它的
长度、叙事关系和补充口述方式尽量接近真实使用。

## 3. 测试素材是什么

固定 fixture：

```text
backend/fixtures/e2e/m3-1-episode.zh-CN.json
```

主题是：

> 五年后重听一段旧录音：我为什么决定重新开始记录生活

初始输入不是三份重复简介，而是三种互补材料：

| Source | 作用 | 大致内容 |
| --- | --- | --- |
| A：时间线与场景 | 提供可核对事实 | 2021 首次录音、搬家、停更、2025 日记、2026 重听 |
| B：反思与原则 | 提供认知变化 | 完美主义如何变成拖延、声音和文字的差别、重新开始的边界 |
| C：EP0 草稿 | 提供口播语气 | 开场、系列设想、写给未来的语音信 |

补充 Source 是一段完整口述，进一步回答：

- 重听旧录音时房间里的具体感受；
- 为什么停更的核心不是忙，而是完美主义；
- 新一轮录音怎样定义“完成”；
- 节目主要录给谁听；
- 第一季六期的暂定结构；
- 希望五年后的自己听见什么。

四份 Source 都明确包含：

```json
{
  "synthetic": true,
  "contains_personal_data": false
}
```

自动化测试还会检查三份初始素材均不少于 600 字、补充口述不少于 800 字，
并且包含足够的自然段。这样以后不能不小心把 fixture 又退化成几句 filler。

## 4. 为真实验收补了哪些能力

### 4.1 Fake 不再返回英文占位句

旧 Fake 的目的只是满足 Schema，所以会出现：

```text
A deterministic timeline candidate from the cited segment.
```

工程上合法，产品上不可读。

现在 Fake 会从分配给 Task 的 SourceSegments 中确定性提取真实句子、时间
表达、主题和逐字 quote；本次 E2E 的输入正是上述 fixture。
再生成三段、每段两题的采访脚手架。它仍然：

- 不联网；
- 不读取 API Key；
- Token 和费用为 0；
- 相同输入得到相同输出；
- 适合自动化回归。

这里要分清：Fake 的任务不是模拟 DeepSeek 的创作水平，而是让免费测试产物
至少与输入相关、能被人检查。

### 4.2 Markdown 使用人类可读的引用

旧导出会直接显示：

```text
src_...#seg_...
```

这些 ID 对数据库有用，对读者没有用。

现在正文使用短标签：

```text
来源：[S1]、[S2]
```

文末集中列出：

```text
- [S1] 《合成素材A｜五年时间线与重听旧录音的晚上》片段 1
```

显示层只做“翻译”，没有删除底层追踪信息：

```text
Markdown: [S1]
    ↓ export 时解析
Database / Artifact: source_id + source_segment_id
```

因此普通用户能读懂。开发者通过 Artifact 中的原始引用和数据库解析到原始
段落；Event 则解释相关 Artifact 与检查点是在什么时候产生的。
如果引用元数据丢失，导出返回 409，而不是默默显示一个错误来源。

### 4.3 topic 真正进入 Researcher

完整素材里可能同时有很多线索。现在 Orchestrator 会把 Run 的 `topic` 一起
传给两个 Researcher，Prompt 要求优先提取与主题直接相关、又有原文证据的
内容。

`topic` 和 `source_segments` 都被明确视为不可信数据；即使其中出现“忽略
系统规则”之类的文字，也不能改变 Agent 的任务。

### 4.4 原始素材与研究 Bundle 使用不同长度边界

第一次真实 Run 暴露了一个真实架构问题：

- Researcher 读取的是原始 Source，8,000 字边界足够；
- Interviewer 读取的是两个 Researcher 的结构化结果合并后的 Bundle；
- 合并 Bundle 约 14,750 字，却错误复用了 8,000 字边界；
- 第三个模型调用在联网前被 `provider_input_too_large` 拦截。

修复后使用两个有不同语义的配置：

```text
max_research_source_chars = 8,000
max_interview_bundle_chars = 24,000
```

这不是简单“把限制调大”，而是承认两个输入处在不同阶段、体积规律不同。
边界仍然存在，避免无限 Prompt 和不可控费用。

## 5. 代码模块地图

| 文件 | 作用 | 本次重点 |
| --- | --- | --- |
| `backend/fixtures/e2e/m3-1-episode.zh-CN.json` | 固定测试数据 | 完整初始素材与补充口述 |
| `backend/src/epiphany/checkpoint_e2e.py` | E2E 驱动器 | 真实 HTTP 流程、验收断言、日志与报告 |
| `backend/src/epiphany/runtime/providers/fake.py` | 免费 Provider | 从素材中确定性提取相关内容 |
| `backend/src/epiphany/runtime/research_prompts.py` | Researcher Prompt | topic 相关性与不可信输入边界 |
| `backend/src/epiphany/runtime/interview_prompts.py` | Interviewer Prompt | Bundle 边界、精简输出、事实状态约束 |
| `backend/src/epiphany/runtime/providers/deepseek.py` | DeepSeek Provider | 分离原始 Source 与 Bundle 长度上限 |
| `backend/src/epiphany/config.py` / `main.py` | 应用配置与装配 | 两个长度上限可分别通过环境配置传入 |
| `backend/src/epiphany/interview_markdown.py` | Markdown Renderer | `[S1]` 与来源索引 |
| `backend/src/epiphany/services.py` | 导出服务 | 从数据库解析 Source 标题和 Segment 位置 |
| `backend/tests/test_checkpoint_e2e.py` | E2E 自动化保护 | fixture 长度、内容相关性、状态和脱敏 |
| `backend/tests/test_deepseek_provider.py` | Provider 与配置测试 | 双长度上限、Prompt、Fake 与 DeepSeek 契约 |
| `backend/tests/test_interview_scaffold.py` | Scaffold 契约测试 | 事实状态 Prompt、Schema 与渲染 |
| `backend/tests/test_interview_export_api.py` | 导出 API 测试 | `[S1]`、来源索引、缺失元数据和转义 |
| `backend/tests/test_research_workflow.py` | 编排集成测试 | topic 传递、fan-out / fan-in 和等待 |

## 6. 怎样复现

完整命令、参数与安全边界统一保存在
[M3.1 后端 / API E2E runbook](m3-1-backend-e2e.zh-CN.md)，避免学习章节和
操作手册维护两套可能漂移的命令。

本次实际 Run 的数据库数量、Event 顺序、三次调用费用、日志统计、失败诊断
和人工内容复核单独保存在
[运行证据与内容复核](m3-1-realistic-e2e-evidence.zh-CN.md)。把证据拆开后，
未来新增一次验收不需要重写本章的技术解释。

## 7. 自动化测试

常规测试不会读取真实 Key，也不会请求 DeepSeek。完整验证命令：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

pytest -q
ruff check src tests
ruff format --check src tests
alembic upgrade head
alembic check
```

本次结果为 `130 passed`，Ruff 全部通过，Alembic 到
`0003_model_call_trace (head)` 且没有 schema drift。

重点覆盖：

- 正常路径：完整 Fake HTTP E2E、等待、导出、Resume 和幂等重放；
- 输入边界：Researcher 与 Interviewer Bundle 分别拒绝超限输入；
- 内容契约：topic 进入研究 Prompt，Fake 输出和输入相关；
- 引用：正文短标签、来源索引、转义、缺失 Source 元数据返回 409；
- 事实状态：Prompt 中计划/草稿/愿望不得改成已完成事实；
- 安全：dry-run 不创建文件，普通测试不联网，日志不含 Key 或素材正文。

真实 DeepSeek 成功只能由人工显式运行 `--provider deepseek --execute` 验证；
它不属于默认 `pytest`，避免 CI 或日常开发意外产生费用。

## 8. 为什么补充口述没有出现在当前 Markdown

这是 M3.1 的明确边界，不是漏处理：

```text
Interview Scaffold
  -> 等用户补充
  -> 补充 Source 入库
  -> Resume 可靠登记引用
  -> M3.1 结束
```

因此 Resume 前后导出的 Scaffold SHA-256 完全相同：

```text
8462c26aaa3e555e4cb1904ab9fd39eb02ecdb3bc918f51695ff929762f946b2
```

补充口述正文存在 Source/SourceSegment 表中；`user_material_submission`
Artifact 只保存引用。M3.2 才会新增 Editor Task，读取：

```text
Interview Scaffold + supplemental Sources
  -> podcast draft Markdown
  -> Show Notes
```

到那时，E2E 应继续断言最终稿确实吸收了补充口述，而不是只检查它已经入库。

## 9. 这一步学到了什么

### 9.1 E2E 不只是“最后再跑一下”

完整素材直接改变了架构判断：Researcher 的原始输入限制不能机械复用于
Interviewer 的聚合输入。

### 9.2 Fake、真实模型和人工复核是三层不同测试

| 层 | 最擅长发现 |
| --- | --- |
| Fake 自动 E2E | 状态、数量、幂等、日志、引用格式回归 |
| DeepSeek 真实 E2E | Prompt 容量、模型 JSON、Token、延迟、真实输出 |
| 人工内容复核 | 车轱辘话、问题价值、语义夸大、叙事节奏 |

只做其中一层都不够。

### 9.3 可观测性让失败变成证据

第一次 Run 失败后，可以明确回答：

- 哪两个调用成功；
- 第三个为什么没有联网；
- 已经花了多少钱；
- 哪个 Task 和 Event 记录了失败；
- 修复后应该比较什么。

这正是结构化日志、持久化 ModelCall 和独立 E2E 数据库的价值。

## 10. 限制与下一步

本次已经证明：

- 完整合成素材可以经过真实 DeepSeek 生成可读采访脚手架；
- Run 能持久化暂停、接收完整补充口述、幂等恢复并成功结束；
- 数据库、日志、费用和来源引用可以解释整个过程。

本次还没有：

- 根据补充口述生成最终播客稿；
- 生成 Show Notes；
- 对自然语言每个事实做自动蕴含验证；
- 麦克风录音、STT、TTS 或语音克隆；
- Web UI；
- 线上部署和多进程并发。

下一步进入 M3.2：Resume 后排队 Editor Task，让同一份 fixture 真正走到包含
补充口述的播客 Markdown 和 Show Notes。届时优先复用本次 E2E 驱动器和
验收数据，而不是重新造一套简短测试。

## 完成检查

- [x] 完整合成初始素材与补充口述
- [x] Fake 全流程通过
- [x] DeepSeek 真实全流程通过
- [x] 失败 Run 与修复原因有持久化证据
- [x] Markdown 内容和引用完成人工复核
- [x] 日志不含素材正文和 API Key
- [x] Token、耗时和费用完成记录
- [x] M3.1 与 M3.2 边界写清楚
- [x] 130 项测试、Ruff、Alembic 与文档同步完成
- [x] 本次实现与文档已准备进入同一个 focused commit
