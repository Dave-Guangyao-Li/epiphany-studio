# M3.5：中文口播质量校准与冻结稿 Reviewer 实验

更新时间：2026-07-30

## 1. 这一阶段为什么重要

M3.4 已经能生成质量报告，但第一次真实实验暴露了一个危险现象：

```text
目标：10 分钟
代码估算：5.1 分钟
同模型 Reviewer：六个维度全部 5/5
旧版实验分：83.2
```

这不表示代码和模型中必须有一个“算错了”。模型可能真心认为现有五分钟稿的
结构、来源和语气不错，但这种局部评价不能回答“它是否满足十分钟要求”。

M3.5 的目标不是找一个永远正确的判卷模型，而是建立一层任何 Reviewer 都不能
绕开的事实：

> 编辑可以评价故事是否动人，但不能把尺子量出的五分钟说成十分钟。

### 1.1 为什么新 Run 是 workflow v7

M3.5 修改了会被持久化并在重启后继续使用的 Reviewer Task 输入、Prompt、
确定性规则与报告公式，因此不能继续假装它仍是 M3.4 的同一个 workflow v6。

- v6 冻结为 `model_self_review_task_v1`、`quality_review_prompt_v1`、
  `draft_quality_rules_v1` 和 `draft_quality_v1_60_40`；
- v7 使用带可信事实的 Task/Prompt v2、
  `draft_quality_rules_v2_chinese_calibration` 和
  `draft_quality_v2_non_compensatory_caps`；
- 旧 v6 Run 无论停在等待补充素材、Reviewer 队列还是 lease recovery，
  升级后都继续走旧合同，不会被偷偷改写为 v7。

自动测试会把一条 v6 Run 走到持久 Reviewer 队列，再用新的 Database、
Orchestrator 和 Worker 恢复它，验证旧 Prompt、旧评分、`:v1` Artifact key
以及 Draft 最终产物都保持不变。

开发期间还存在一个更窄的过渡形态：Run 已经写成 v6，但持久 Reviewer Task
已经带 `deterministic_quality_facts`。恢复代码不能只看 Run 标签，否则会把
这个 current/v2 Task 错误降回旧公式。Orchestrator 因而以**持久 Task 推断出的
合同版本**选择 Metrics、报告公式和 Artifact key；另一条重启回归会模拟
`v6 + current facts + :v1 metrics key`，确认它仍得到 v2 cap 与 conflict。

## 2. 一个不需要技术背景的类比

把 Draft Quality Report 想成房屋验收：

- 代码像卷尺：测面积、层高和门宽；
- Reviewer 像室内设计师：评价动线、氛围和风格；
- 用户像真正住进去的人：判断是否舒服、是否像自己的家。

设计师可以喜欢一个小房间，但不能因为它漂亮就把 40 平方米评价成 80 平方米。
同样，模型可以给来源忠实度 5 分，却不能让一份只有目标一半长度的稿子进入
“可以直接录制”。

## 3. 时长到底统计什么

`script_character_count` 只统计真正会被说出口的三类字段：

```text
podcast_script.opening.text
podcast_script.sections[*].paragraphs[*].text
podcast_script.closing.text
```

代码移除这些正文中的空白后计数。以下内容全部排除：

- Podcast 标题；
- Section 标题；
- `source_refs`；
- Markdown 中的 `[S1]`；
- 来源索引；
- Show Notes；
- Markdown 标记；
- Artifact metadata。

因此引用再多，也不会把稿子的估算时长虚增。对应回归测试会故意给标题、引用
ID、Show Notes 和渲染后的 Markdown 加入大量文字，然后确认口播字符数完全
不变。

估算公式仍然是：

```text
估算分钟数 = 口播正文非空白字符数 / Creative Brief 中的每分钟字符数
```

它是文字阶段估算，不是录音实测。真实停顿、笑声、英文比例和临场发挥仍由
`observed_duration_minutes` 另行记录。

## 4. 代码拥有的可信事实层

Reviewer 现在会收到一个小而固定的
`deterministic_quality_facts`：

- 目标分钟数；
- 口播字符数；
- 估算分钟数；
- 时长覆盖比例与状态；
- 引用覆盖；
- blocker / warning 数量；
- filler、模板表达和中文风格类别计数；
- 使用的规则版本。

这些事实由 Orchestrator 从**当前 Draft 对应的持久化 Metrics Artifact**
生成，不接受公共 API 随意提交。Task Schema 还会从同一份 Draft 重新计算
字符数、时长和引用覆盖；只要二者不一致，就拒绝 Reviewer Task。

Prompt 明确要求 Reviewer：

- 不得重新估算、否认或覆盖代码事实；
- 时长 blocker 或覆盖低于 60% 时，`brief_adherence` 最高 2/5；
- 时长 warning 时，`brief_adherence` 最高 3/5；
- “稿子短”不能被解释成“非常精炼”；
- `must_include` 要判断语义覆盖，不默认要求逐字复述。

模型仍可能不遵守 Prompt，所以最终代码还会记录冲突并施加评分上限。

## 5. 四个不同的分数不要混在一起

M3.5 使用 `draft_quality_v2_non_compensatory_caps`。报告会分别展示：

1. **模型局部维度平均**：六张 Reviewer 卡片的简单换算，仅研究模型偏差；
2. **未封顶实验分**：确定性 60% + 模型 40%；
3. **代码上限**：硬性事实允许显示的最高分；
4. **封顶后实验分**：`min(未封顶分, 代码上限)`。

代码上限：

| 条件 | 最高分 |
| --- | ---: |
| 任一确定性 blocker | 39 |
| 时长覆盖低于 60% | 59 |
| 任一确定性 warning | 79 |
| 多条同时满足 | 取最严格上限 |

最终 decision 也不是简单按平均分决定：

- 有 blocker：`blocked`；
- Reviewer 不可用且无 blocker：`automated_review_incomplete`；
- 有 warning 或低分维度：`revision_recommended`；
- 其余情况：`candidate_ready_for_human_review`。

这叫“非补偿式”：来源、语气和结构的高分不能抵消严重时长缺口。

### 5.1 报告本身也必须通过一致性验收

字段类型正确，不代表整份报告可信。例如，下面这组数据在数学上不可能同时
成立：

```text
未封顶分：86
代码上限：39
最终分：80
decision：candidate_ready_for_human_review
```

因此 `DraftQualityReport` 在创建、从 SQLite 读回、通过 API 返回和导出
Markdown 时，都会重新校验：

- profile 是否与确定性结果一致；
- Reviewer 的 completed / unavailable 状态是否与卡片、原因和模型关系一致；
- 六维分是否能重新算出保存的模型分；
- 60/40 公式是否能重新算出未封顶分；
- findings 是否能重新推出 39 / 59 / 79 cap 及其原因；
- 模型/代码 conflict 是否能由同一张模型卡和 duration finding 重新推出；
- 最终分是否严格等于 `min(未封顶分, cap)`；
- decision 是否与 blocker、warning、Reviewer 可用性和低分维度一致。

这让质量报告本身也成为一个经过验证的合同，而不是一包看起来合理的 JSON。
历史 v1 Artifact 仍按旧合同读取，不会被 M3.5 回写或伪造。

## 6. 为什么仍保留模型原始分

原始分不应被包装成整体质量，但它仍有研究价值。例如，同一份冻结稿：

- Flash 可能觉得口播自然度是 4；
- Pro 可能觉得是 5；
- 两者可能都没有意识到某种中文模板感；
- 某次模型输出甚至可能不符合严格 JSON Schema。

如果把这些原始差异删掉，就无法分析 Reviewer 的宽松程度、稳定性和成本。
因此系统保留原始卡片，同时把用户真正看到的总判断交给代码边界。

质量报告中的文字特意叫：

> 模型六个局部维度的简单平均（未应用硬性上限，仅用于偏差研究）

而不叫“最终质量分”。

## 7. 中文口播风格信号 v1

首版实现七类可解释信号：

| 类别 | 例子 | 冷启动 warning |
| --- | --- | ---: |
| `parallel_contrast` | 不是……而是……；与其……不如…… | 3 次 |
| `escalation` | 不仅……还……；从……到……再到…… | 3 次 |
| `enumeration` | 首先、其次、最后 | 3 次 |
| `generic_transition` | 值得注意的是、与此同时、换句话说 | 3 次 |
| `generic_epiphany` | 我突然意识到、原来……才是 | 3 次 |
| `generic_coda` | 让我们一起、我们下一期再见 | 3 次 |
| `over_polite` | 非常荣幸、请允许我、衷心感谢 | 2 次 |

此外还计算：

- 句长变异系数：至少 6 句，低于 0.12 才 warning；
- 段长变异系数：至少 4 个口播段落，低于 0.10 才 warning。

这些阈值是保守冷启动值，不是中文写作的普遍真理。列表类节目、知识节目和个人
独白未来应该有不同 profile。

### 7.1 它不是 AI 检测器

一位真人完全可能自然说出“不是……而是……”，模型也可能写得很口语。规则
只能说明某个可观察模式重复出现，不能证明作者身份，也不会输出 AI 概率。

研究依据、候选信号和误报边界见
[中文个人口播稿的风格信号研究](../research/chinese-podcast-style-signals.zh-CN.md)。

### 7.2 为什么旧指标变成 info

旧 `style.not_but_pattern` 是新 `parallel_contrast` 的子集；旧模板短语也和
`generic_transition` / `generic_coda` 重叠。如果两套规则都扣分，同一句话
会被处罚两次。

M3.5 保留旧计数以读取和展示历史报告，但状态变为 `info`：

- 继续显示；
- 不扣分；
- 不增加 warning 数；
- 不触发 79 分上限；
- 实际分数影响只由版本化新类别负责。

## 8. `must_include` 为什么不能只用字符串判断

真实 DeepSeek 稿已经写到：

- 搬家第一晚热水器失败；
- 发烧时害怕独居；
- 周日切菜、晾衣服；
- 自由逐渐变成照顾自己的能力。

但 Brief 中的四个 `must_include` 使用了不同措辞，旧字符串规则把四项全部
报成缺失并扣了 16 分。

普通代码只能证明“这串文字有没有逐字出现”，不能证明“这个意思有没有出现”。
因此 M3.5 改成：

- 逐字未命中保存为 `info`；
- 不扣分、不触发 cap；
- Markdown 明确写“不代表语义缺失”；
- Reviewer 结合 Draft 和 Source 判断语义覆盖。

`avoid_patterns` 也可能是具体禁句或抽象偏好。当前代码只对逐字命中发 warning，
抽象要求交给 Reviewer。未来应把语义要求与逐字要求拆成不同字段。

## 9. Flash / Pro 冻结稿比较

比较 Reviewer 时，不能让 Flash 审一份稿、Pro 审另一份稿。工具会冻结：

- 同一 Draft；
- 同一 Creative Brief；
- 同一来源片段；
- 同一确定性事实；
- 同一 Prompt；
- 同一输入 hash。

它不会重新运行 Editor。默认只是 dry-run：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

python -m epiphany.quality_reviewer_compare \
  --run-id <RUN_ID> \
  --database data/<DATABASE>.db
```

如果旧 Run 的 Metrics 来自开发中的旧规则，可显式从持久 Draft 派生当前规则
快照，不修改旧 Run：

```bash
python -m epiphany.quality_reviewer_compare \
  --run-id <RUN_ID> \
  --database data/<DATABASE>.db \
  --recompute-current-rules
```

只有加入 `--execute` 才会发出 Flash、Pro 各一次付费调用。输出已经存在时会
拒绝覆盖，必须换路径或显式使用 `--force`。结果记录：

- frozen task hash；
- deterministic result hash；
- entire comparison bundle hash；
- Prompt hash；
- Token、耗时、估算 CNY；
- 六维原始分；
- 未封顶分、cap、最终分和 decision；
- Schema 失败时的脱敏字段路径与错误类型。

比较工具是独立本地实验，不进入产品 Workflow 的 `model_calls` 表，也没有
durable retry；它的费用账本只在本地 comparison JSON 中。中途断开时可能已经
产生厂商费用，因此不能把它当生产 Runner。Provider 已经返回用量、但后续
strict Schema 或 evidence 校验失败时，失败记录仍保留安全的 Token、币种和
估算费用；只有连 Provider 用量都没有得到时，这些字段才为 `null`。

## 10. 真实实验发现了什么

### 10.1 完整 Workflow

合成主题是“第一次真正独居：自由、孤独与照顾自己”。Brief 包含明确听众、
沟通目标、语气、15 分钟目标、四项必须覆盖内容和六项避免模式。四份初始
Source 共 2,867 字，系统先停在 `awaiting_more_material`；补充 3,519 字
口述转写后，素材门槛变为 ready。

真实 Run：

```text
run_id: run_0af27a7596474a92ba79e298e912e35e
Tasks: 6 succeeded
Workflow Artifacts: 11
ModelCalls: 5
input tokens: 34,701
output tokens: 12,174
Provider duration: 90,377 ms
local estimated cost: CNY 0.089433
```

Editor 使用 Flash，Workflow Reviewer 使用 Pro。实际 Draft：

```text
spoken chars: 2,055
target / estimated duration: 15 / 7.34 minutes
duration coverage: 48.9%
citation coverage: 100%
source range: 5 Sources / 15 Segments
```

产品 Workflow 已成功完成。旧 E2E 检查器曾假设五次调用都必须使用 Editor
模型，因此把“Flash Editor + Pro Reviewer”误报为失败；修复后对同一数据库
离线重验：

```text
model_calls_match_routed_providers: true
quality_report_contract_valid: true
reviewer_relation: cross_tier_same_family
```

不需要为修复测试器重复支付五次模型调用。

### 10.2 规则清理前后

同一 Draft 的开发中旧快照为：

```text
deterministic score: 43
blocker: 1
warning: 3
```

旧分数重复处罚了 `not_but`，并把 `must_include` 的同义改写当缺失。当前规则
离线重算为：

```text
deterministic score: 62
blocker: 1
warning: 1
info: 2
```

唯一 blocker 仍是 7.34 / 15 分钟；唯一 warning 是四次
`parallel_contrast`。`must_include` 与 legacy `not_but` 变成 info。
诊断分变化，但严格 cap 仍为 39，decision 仍为 `blocked`。

### 10.3 Flash 与 Pro 并不等于“便宜模型宽松、贵模型严格”

规则清理前的同一冻结快照中，两次结果都通过严格 Schema：

| 指标 | Flash | Pro |
| --- | ---: | ---: |
| 模型局部维度平均 | 76.67 | 80.00 |
| 口播自然度 | 4/5 | 5/5 |
| 最终 cap / 分数 | 39 / 39 | 39 / 39 |
| 耗时 | 13,453 ms | 28,297 ms |
| 估算费用 | CNY 0.014246 | CNY 0.042816 |

Pro 反而比 Flash 高 3.33 分，并不更严格；它约为三倍价格、两倍耗时。代码
边界让两者最终都不能越过 39 分。

用**当前规则重算**后的第二轮中：

- Flash 请求成功返回，但输出没有通过严格
  `ModelSelfReviewOutput` Schema，因此不产生可用评分；
- Pro 通过 Schema，六维为 `2 / 5 / 3 / 4 / 4 / 3`；
- Pro 局部维度平均 70，未封顶 65.2，最终仍被 cap 为 39；
- Flash / Pro 本轮估算费用分别为 CNY 0.013950 / 0.044008。

这两轮只能说明该 fixture 上的 Reviewer 敏感性，不能证明 Pro 等同真人编辑，
也不能估计长期成功率。更重要的结论是：模型更贵不等于更严格，Prompt 也不等于
强制约束；Schema、可信事实和代码 cap 才是安全边界。

## 11. 零费用最终 Fake E2E

当前规则完成后运行：

```bash
python -m epiphany.draft_quality_e2e \
  --provider fake \
  --execute \
  --fixture fixtures/e2e/m3-5-chinese-quality-calibration.zh-CN.json \
  --database data/m3-5-quality-calibration-fake-v7.db \
  --output-dir artifacts/m3-5-quality-calibration-fake-v7
```

结果：

```text
passed: true
workflow: v7
run_id: run_f41eac8520cd4b47b97cc1181acb3d63
Tasks: 6
Artifacts before feedback: 11
ModelCalls: 5
estimated cost: 0
rules: draft_quality_rules_v2_chinese_calibration
hard blocker count: 1
warning count: 1
model conflict count: 1
```

Fake Draft 本身很短，所以质量 decision 为 blocked；E2E 工程成功不要求 Fake
内容冒充优质成稿。它证明暂停、重启、补充 Source、Resume、Editor、Metrics、
Reviewer、报告、Markdown、反馈幂等、日志和数据库链路全部工作。

## 12. 代码模块地图

| 模块 | 作用 |
| --- | --- |
| `draft_quality.py` | 口播边界、确定性规则、可信事实、cap 与冲突 |
| `draft_quality_schemas.py` | Metrics、Reviewer、Report 的严格合同与跨字段一致性校验 |
| `draft_quality_markdown.py` | 把事实、info、warning、模型卡和 cap 渲染成人类可读报告 |
| `runtime/quality_prompts.py` | 告诉 Reviewer 如何使用可信事实和语义 Brief |
| `runtime/orchestrator.py` | 从持久 Draft/Metrics 排队可信 Reviewer Task |
| `runtime/worker.py` | 只把 Reviewer Task 路由到可选 Reviewer Provider |
| `quality_contract_e2e.py` | 完整 v7 E2E、legacy v6 恢复与跨模型路由校验 |
| `quality_reviewer_compare.py` | 同一冻结稿的本地 Flash/Pro 对照实验 |

## 13. 自动测试与本地调试

Focused 测试：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

pytest \
  tests/test_draft_quality.py \
  tests/test_draft_quality_provider.py \
  tests/test_draft_quality_workflow.py \
  tests/test_quality_contract_e2e.py \
  tests/test_quality_reviewer_compare.py -vv
```

其中 workflow 测试同时覆盖正式 v7、纯 legacy v6 重启，以及预发布
`v6 + current facts` 重启恢复。第三种情况专门防止升级后意外丢失 v2 上限和
模型/代码 conflict。

完整验证：

```bash
ruff format --check src tests
ruff check src tests
pytest -q
alembic upgrade head
alembic check
git diff --check
```

排查时先看：

1. E2E `report.json` 中每个 `checks`；
2. `runtime.jsonl` 的 `run_id` / `task_id`；
3. SQLite 的 `tasks`、`artifacts`、`model_calls` 和 `events`；
4. `draft-quality-report.json` 的 deterministic / model / cap 三层；
5. comparison JSON 的三个 hash、模型结果和 `failures`。

日志不得写 Source 正文、Draft 正文、反馈评论或 API Key。比较工具的 schema
错误只记录路径和类型，不保存模型原始失败文本。

## 14. 这一阶段仍然没有解决什么

- 一个合成 fixture 不能校准全体中文播客；
- Flash / Pro 同属 DeepSeek，不是独立跨家族裁判；
- 中文正则不会理解所有隐喻和同义改写；
- 当前反馈只保存，不会自动生成新版；
- 尚未使用真实录音校准字符速度；
- 尚未根据用户历史稿建立个人声音基线；
- 尚未提供 Web UI。

## 15. 下一步：M3.6 显式 Revision Run

用户反馈对产品最有价值的方式，不是让模型在后台无限自我改稿，而是由用户选择
一次明确修订：

```text
已接受的 Draft
  + 用户选择的 feedback
  + 可选新增 Source
  + 一次明确 revision instruction
  -> 新 Revision Run
  -> 新 Draft
  -> 新 Metrics / Reviewer / Report
```

计划中的安全边界：

- 旧 Draft、旧报告和旧反馈不可变；
- 新 Run 保存 `parent_run_id`；
- 只消费用户明确选择的反馈；
- 每次修订使用独立模型调用预算；
- 所有 Task、Artifact、Event 和费用单独可追踪；
- 不实现“模型自己改到分数达标”的自动循环。

M3.5 的核心成果不是“模型更会打分”，而是：

> 任何 Reviewer 都不能用主观高分掩盖代码已经测出的硬性问题。
