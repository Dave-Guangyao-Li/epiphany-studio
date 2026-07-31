# M3.7d：真实量级合成人设、写作样本 A/B 与匿名合成评审

状态：实验完成；获得方向性正信号，但 15 分钟长度目标仍未达到

日期：2026-07-31

对应代码提交：

- `3fdf8c1`：真实人设实验驱动器；
- `4b18ab5`：拆分并冻结合成人设语料；
- `cdbed1b`：增加素材充足度余量；
- `3b01f9b`：修复真实实验的 Editor 限制配置漂移。

## 1. 这次实验回答什么

上一轮 M3.7 A/B 的两份候选有 9/10 个口播单元完全相同，最终只能得到
`inconclusive_low_distinctness`。那次结果不能证明 Sample 无效，因为测试样本
本身太短、太同质，也不像一个真实用户长期积累的文字。

本次重新冻结一个完整但虚构的人：

- 4 篇不同题材、不同载体的个人写作样本；
- 3 份事实 Source；
- 1 份完整补充口述；
- 明确的受众、场景、语气和 15 分钟长度目标；
- 从 Research、采访暂停、Resume、Editor、Reviewer 到匿名 A/B 的完整流程。

本实验只回答一个窄问题：

> 在事实、Creative Brief、模型、Prompt 与质量规则相同的前提下，让 Editor
> 看见多篇真实量级的个人 Sample，是否会产生可辨识且方向更好的表达差异？

它不是模型排行榜，也不证明 Sample 对所有用户都有效。合成人设的自动评审不能
冒充真人反馈。

| 快速结论 | 结果 |
|---|---|
| 真实主流程 | 5 次 DeepSeek 调用均返回；Reviewer Task 因证据合同失败而降级 |
| 冻结 A/B | 2 次 Editor + 2 次 Reviewer 全部返回 |
| 15 分钟目标 | 两稿仅约 6.4—6.7 分钟，最终分均封顶为 39 |
| 候选区分度 | 精确口播单元重合率 18.75%，不再是近乎同稿 |
| 匿名评审 | 合成 Agent 方向性偏好 Sample arm，不是真人证据 |
| 产品决定 | M3 收口，下一步在 M4/M5 Trace UI 暴露这些状态 |

## 2. 冻结的人设与 Creative Brief

人设名为林澄，30 岁，在上海从事内容运营。她的稳定表达特征是：

- 先写房间、天气、动作和具体物件；
- 允许一句话说到一半后修正自己；
- 情绪克制，偶尔有一点不刻意的幽默；
- 不把普通经历拔高为人生道理；
- 结尾可以停在一个动作或尚未解决的问题上。

本期标题是：

> 《退掉住了六年的出租屋后，我为什么在楼下坐了四十分钟》

Creative Brief 固定为：

| 字段 | 冻结值 |
|---|---|
| 目标时长 | 15 分钟 |
| 估算语速 | 280 个中文字符/分钟 |
| 合理正文范围 | 3,570—4,830 个非空白字符 |
| 场景 | 可直接录制的个人独白 |
| 受众 | 25—35 岁、正处在人生过渡期的人 |
| 目标 | 用具体生活细节讲清主动离开与情绪滞后 |
| 语气 | 克制、具体、诚实、允许自我修正 |

## 3. 素材规模与事实/风格隔离

语料位于：

`backend/fixtures/e2e/m3-7-realistic-persona/`

| 素材 | 类型 | 非空白字符 | 用途 |
|---|---:|---:|---|
| 入住、六年时间线与退租当天 | factual Source | 798 | 事实 |
| 房间里的物件和生活痕迹 | factual Source | 784 | 事实 |
| 为什么离开，以及不想讲成什么 | factual Source | 794 | 事实 |
| 楼下四十分钟与没有同步完成的告别 | supplemental Source | 3,632 | 事实 |
| 凌晨一点，冰箱里只剩半颗柠檬 | written prose | 925 | style only |
| 我妈把镜头对准阳台上的葱 | written prose | 917 | style only |
| 下雨天去修一把伞 | written prose | 910 | style only |
| 为什么一直没回那条消息 | spoken transcript | 1,121 | style only |

四篇 Sample 合计 3,873 个非空白字符。确定性选择器最终选中 20 个片段、
2,219 个非空白字符供 Editor 和 Reviewer 参考。

安全边界没有改变：

- Sample 只能影响句子长短、节奏、转折、直接程度与口语感；
- Sample 不能为出租屋故事提供事实；
- Sample 不能进入事实 `source_refs`；
- 本次虚构 Sample 作为可重复测试 fixture 提交到 Git，但真实用户的日记、
  口述和文章绝不能提交；
- 完整的模型可见 Sample 文本不进入日志或公开运行 manifest；
- Style Profile 只持久化引用、hash 和统计值。

## 4. 抗断流与可恢复执行法

此前一次长操作同时编辑大文件、运行模型并回传大量日志，桌面会话多次出现：

```text
stream disconnected before completion:
Transport error: network error: error decoding response body
```

这条错误本身不能证明数据库损坏，也不能证明后台命令仍在运行。后续改用小步
执行：

1. 每次只做一个 1—3 分钟的小闭环；
2. 大语料拆成独立 Markdown 文件，manifest 只保留路径和 metadata；
3. Dry Run、Fake E2E、真实主流程、A/B 和盲评使用不同目录；
4. 长命令使用终端 session，按 10—30 秒短轮询；
5. 对话只返回阶段、状态、Token、费用和错误码，不回传完整日志；
6. 每个验证通过的修复单独 commit；
7. 断线后先检查 SQLite、`report.json`、`manifest.json` 和 Provider Dashboard，
   不盲目重跑；
8. 任何失败 Run 都保留原库，新尝试使用新的数据库路径。

本次几次断流没有造成数据库损坏。已经提交或原子落盘的 fixture、SQLite 状态和
实验 manifest 可以在断线后核查，但仍要先确认终端进程与产物状态，不能假设
“只影响展示”或直接重跑。

### 4.1 模块地图与本地复现

| 文件或目录 | 作用 |
|---|---|
| `backend/fixtures/e2e/m3-7-realistic-persona/` | 冻结人设、事实素材、补充口述和风格 Sample |
| `backend/src/epiphany/realistic_style_experiment_e2e.py` | 运行 Dry Run、Fake/DeepSeek 主流程并冻结 A/B 输入 |
| `backend/src/epiphany/writing_style_ab_execute.py` | 执行有界的两次 Editor 与两次 Reviewer |
| `backend/src/epiphany/writing_style_ab_blind.py` | 匿名候选、保存评分、检查区分度并在评分后揭盲 |
| `backend/tests/test_realistic_style_experiment_e2e.py` | 验证 fixture、限制、隐私和失败边界 |

先运行零网络预检：

```bash
cd backend
source .venv/bin/activate
python -m epiphany.realistic_style_experiment_e2e --provider fake
```

再使用一个**从未存在过**的数据库和产物目录运行 Fake 全流程：

```bash
python -m epiphany.realistic_style_experiment_e2e \
  --provider fake \
  --execute \
  --database data/m3-7-realistic-local.db \
  --output-dir artifacts/m3-7-realistic-local
```

同名数据库或目录已存在时不要覆盖，换一个后缀，保留失败现场。聚焦回归：

```bash
pytest tests/test_realistic_style_experiment_e2e.py -q
```

只有明确接受付费调用、确认 `.env` 中的 DeepSeek Key 可用，并为数据库和产物
选择全新路径后，才把 `--provider fake` 改为 `--provider deepseek`。排错时先按
`run_id` 查看 SQLite，再看脱敏日志中的
`realistic_style_experiment_e2e.*`、`model.call.*`、
`writing_style_ab.call.*` 与 `writing_style_ab.execution.*`；不要把完整 Prompt
或正文复制进日志。

## 5. 失败一：原始文件很长，不等于 Research 证据足够

第一次 Fake E2E 在 Resume 后没有排到 Editor。这里的 grounded 字符指带来源、
实际进入 Editor 的去重事实字符：

| 阶段 | 可用 grounded 字符 | 15 分钟最低门槛 | 还缺 |
|---|---:|---:|---:|
| 初始 Research 结果 | 348 | 3,570 | 3,222 |
| 原补充口述后 | 3,117 | 3,570 | 453 |

原始三份事实文件当时已有约 2,376 个字符，但 Researcher 只保留了 348 个进入
下游。这说明 Readiness 必须计算实际进入 Editor 的去重证据，不能用上传文件体积
假装素材充足。

修复方式不是降低门槛，也不是加入 filler，而是在补充口述里增加新的具体事实：
交接动作、楼下四十分钟的身体感受、物件处理、对节目边界的修正和仍未想明白的
部分。第二次 Fake E2E 的 grounded evidence 达到 3,980，顺利过门槛。

真实模型 Run 的选择更丰富：

| 阶段 | 初始字符 | 补充字符 | 合计 | 状态 |
|---|---:|---:|---:|---|
| checkpoint 前 | 1,047 | 0 | 1,047 | needs_more_material |
| Resume 后 | 1,047 | 3,632 | 4,679 | ready |

4,679 个字符按当前保守规则可以支持约 14.2—19.2 分钟。后面实际 Draft 仍然
明显过短，因此那个问题不能再归因于“用户素材不足”。

## 6. 失败二：预检 48k，执行却偷偷用了 32k

第一次真实 DeepSeek Run 的前三个任务成功，Editor 在本地失败：

```text
provider_input_too_large
```

审计发现：

| 项目 | 数值 |
|---|---:|
| Editor 实际序列化 bundle | 37,091 字符 |
| realistic preflight 展示的产品上限 | 48,000 |
| 执行器实际沿用的旧 checkpoint 上限 | 32,000 |
| 完整 system + user prompt | 44,457 字符 |

因此这不是 DeepSeek 拒绝，也不是模型上下文不足，而是测试驱动器出现配置漂移。
Editor 在请求发出前被本地阻断，0 token、0 费用；此前三个成功调用的本地估算
费用为 ¥0.025354。

修复 `3b01f9b` 保持旧 Checkpoint E2E 的默认 32k/6k 不变，只允许真实人设
实验显式传入 Settings 的 48k Editor bundle 和 20k Editor output token 上限。
Dry Run 现在会展示真正生效的限制，回归测试同时校验 Provider 与 preflight。

修复后：

- 13 个相关聚焦测试通过；
- 完整 Fake v8 E2E 通过；
- 真实 Editor 以 37,450 字符 bundle 成功发出并返回；
- 没有通过简单地抬高全局常量掩盖旧测试边界。

## 7. 真实 DeepSeek 主流程

一次沙箱内尝试的第一个模型调用在 29 ms 后以 `provider_network_error` 失败，
0 token、0 费用。随后经明确网络授权，使用全新 `live-v3` 数据库重跑，没有
覆盖失败现场。

真实 Run：

```text
run_5ed9f13efb9843718698b0a32dfe65ee
```

它真实走过：

```text
3 factual Sources + 4 style-only Sources
  -> Timeline Researcher + Theme Researcher
  -> deterministic fan-in
  -> Interviewer
  -> waiting_for_user / awaiting_more_material
  -> application restart
  -> supplemental Source
  -> idempotent Resume
  -> Editor
  -> deterministic metrics
  -> Pro Reviewer
  -> Markdown + quality report + synthetic feedback
```

这里的 `synthetic feedback` 是用于验证反馈 API 和幂等性的固定测试记录；第
10 节的“匿名合成评审”才是隔离上下文后的候选质量判断，两者不是同一份证据。

五次调用均由 DeepSeek 接受：

| Task | 模型 | Input | Output | 耗时 | 估算费用 CNY |
|---|---|---:|---:|---:|---:|
| Timeline Research | V4 Flash | 2,995 | 1,700 | 10,635 ms | ¥0.006395 |
| Theme Research | V4 Flash | 3,037 | 2,217 | 15,011 ms | ¥0.007471 |
| Interviewer | V4 Flash | 5,336 | 2,329 | 15,028 ms | ¥0.009994 |
| Editor | V4 Flash | 21,142 | 3,919 | 23,024 ms | ¥0.028980 |
| Reviewer | V4 Pro | 16,153 | 1,956 | 25,629 ms | ¥0.060195 |
| **合计** |  | **48,663** | **12,121** | **89,327 ms** | **¥0.113035** |

Checkpoint 时是 4 Tasks / 6 Artifacts / 3 Model Calls。最终业务 Run 状态为
`succeeded / complete`，共 6 Tasks / 5 Model Calls；其中 Reviewer 的 Provider
调用返回成功，但 Reviewer Task 因下面的输出证据合同失败而按设计降级。

### Reviewer 的严格证据降级

Pro Reviewer 的网络调用成功，但其中一条 `evidence.exact_quote` 不是对应 Draft
块里的逐字子串。验证器正确拒绝该输出：

```text
invalid_model_review_evidence
```

系统没有把“看起来合理的概括”伪装成可追溯引文，也没有偷偷新增付费重试。
它保留 Draft 与确定性报告，将 `model_review_status` 降级为 `unavailable`。
业务 Run 仍完成，但 Reviewer Result Artifact 与完成事件缺失，并连锁导致依赖
它们的计数、关键事件、质量合同、日志和安全汇总检查失败，所以严格 E2E 报告
`passed=false`。

这验证了一个重要边界：

- Provider 调用成功不等于 Task 合同成功；
- 模型自评不可作为安全或溯源机制；
- Reviewer 不可用时，确定性规则仍必须独立工作；
- 实验工具可以把它当失败，产品 Run 则可以带降级信息完成。

后续 A/B 的两次 Pro Reviewer 都满足相同逐字证据合同，说明这不是必现故障，
但仍是需要 Trace UI 明确展示的可靠性事件。

## 8. Draft 质量：素材够了，稿子仍然短

父 Run Draft 的确定性结果：

| 指标 | 结果 |
|---|---:|
| 口播正文非空白字符 | 1,893 |
| 估算时长 | 6.76 分钟 |
| 15 分钟覆盖率 | 45.1% |
| 引用覆盖 | 100% |
| 来源范围 | 4 Sources / 16 Segments |
| 完全重复段落 | 0 |
| filler 命中 | 3 |
| 模板短语 | 0 |
| “不是……而是……” | 0 |
| 确定性分 | 65 |
| 代码拥有的分数上限 | 39 |
| decision | blocked |

这次必须区分两个问题：

```text
Readiness：4,679 个 grounded 字符，素材已达到 15 分钟保守门槛
Generation：只生成 1,893 个口播字符，没有充分使用现有证据
```

因此产品不应该继续告诉用户“请再补素材”。更准确的动作是：

1. 告知“现有素材量已足够，但初稿没有充分展开”；
2. 列出尚未使用或使用不足的具体 SourceSegments（素材片段）；
3. 建议创建一次显式 Revision；
4. Revision 要求只使用现有证据扩写，不允许 filler 或虚构；
5. Revision 后仍缺事实时，才生成新的定向补充问题。

M3.6 已有不可变父 Run（生成原始初稿的运行记录）、Improvement Plan 和显式
Revision 子 Run。本次结果说明，以后在 UI 中要把“补素材”和“用现有素材重写”
做成两个不同按钮。

## 9. 受控 Sample / No Sample A/B

父 Run 的有效 Editor 输入被冻结。付费前重新计算合同 hash：

```text
af68be3de4202ce8002466c680eed9261948c159f551c2667d85267c3762078c
```

预检证明。这里的 arm 是实验分组，treatment 是“Editor 收到 Sample”的实验
处理：

- 两个 Editor arm 的共同输入完全一致；
- 请求输入层面的唯一处理变量是 Editor 是否收到写作 Sample；
- 两个 Pro Reviewer 都收到同一份 ready Sample；
- 模型、温度、Token 上限、质量规则与 Prompt 版本一致；
- 默认随机化调用顺序；
- 失败即停止，不自动重试。

Editor 温度为 0.2，每个 arm 只生成一次，所以模型采样噪声仍然存在。这个
single-pair 实验能观察方向，不能把两稿的全部差异都因果归于 Sample；更强的
验证需要每个 arm 多次重复，或在 Provider 支持时固定随机 seed。

本次随机顺序是：

```text
Editor with_sample
Editor without_sample
Reviewer with_sample
Reviewer without_sample
```

四次调用全部成功：

| Call | Input | Output | 耗时 | 估算费用 CNY |
|---|---:|---:|---:|---:|
| Editor with Sample | 21,142 | 3,543 | 22,159 ms | ¥0.007530 |
| Editor without Sample | 14,497 | 3,432 | 20,756 ms | ¥0.021361 |
| Reviewer of with-Sample Draft | 15,657 | 2,268 | 26,219 ms | ¥0.060579 |
| Reviewer of no-Sample Draft | 15,625 | 2,272 | 26,683 ms | ¥0.055557 |
| **合计** | **66,921** | **11,515** | **95,817 ms** | **¥0.145027** |

DeepSeek 的上下文缓存会影响单次估算费用，所以不能把两个 Editor 的费用差直接
解释成 Sample 更便宜。实验只比较内容，不比较 arm 成本。两次 Reviewer 均收到
同一份 Sample 作为 voice reference；表格标签描述的是它正在审哪一个 Editor
候选。

结果：

| 指标 | No Sample | With Sample | 差值 |
|---|---:|---:|---:|
| 口播字符 | 1,882 | 1,791 | -91 |
| 估算时长 | 6.72 | 6.40 | -0.32 |
| 确定性分 | 61 | 63 | +2 |
| Pro Reviewer | 82.33 | 84.67 | +2.34 |
| 最终封顶分 | 39 | 39 | 0 |
| decision | blocked | blocked | 相同 |

两个 Reviewer 的 `personal_style_match` 都给 4/5。因为 Reviewer 两边都看见
同一份 Sample，这个分数可以比较，但不能单独宣布 treatment 获胜。

Sample 没有解决长度问题，甚至这一对里略短。它可能影响“怎么说”，不能替代
“说多少”和“是否充分利用事实”的独立控制机制。

## 10. 匿名合成评审与揭盲

盲评器先把两个 arm 随机映射为 Candidate A / B，并隐藏 treatment、模型、
Reviewer 分数和私有 mapping。

两稿的可计算差异：

| 指标 | 数值 |
|---|---:|
| 最少 / 最多口播单元 | 15 / 16 |
| 同位置逐字相同单元 | 3 |
| 精确重合率 | 18.75% |
| 规范化字符相似度 | 56.67% |
| 不同单元数 | 13 |
| distinctness decision | distinguishable |

旧实验曾有 9/10 单元相同。本次不再是低区分度问题，可以进行有意义的盲比。

一名独立合成 Agent 只读取四篇 Sample 与 Candidate A/B，没有读取私有 mapping、
A/B manifest、质量分或调用顺序。它不是人类，结果单独记录为
`synthetic_agent / human_rating=false`：

| 指标 | Candidate A | Candidate B |
|---|---:|---:|
| Voice match | 3/5 | 4/5 |
| Recordability | 2/5 | 4/5 |
| 是否愿意录 | 否，需明显删重 | 是，基本可录 |

合成评审认为 A 有物件感和克制，但存在重复拼接；B 的空抽屉、螺丝刀、水渍、
泡泡和缺口白碗形成更连续的生活质感，自我修正也更自然。它强制选择 B，差距为
中等。

评分持久化并计算 hash 后才读取私有 mapping。揭盲结果：

```text
Candidate A = without_sample
Candidate B = with_sample
```

因此本次得到：

```text
directional_synthetic_evidence_for_writing_samples
```

准确解释是：

- 本轮 Sample 组与对照组产生了可辨识差异，不是同稿换标签；
- Pro Reviewer 与匿名合成评审都给出同方向的小幅到中等正信号；
- 这值得进入真实用户盲评；
- 单次随机生成不能证明差异全部来自 Sample；
- 它仍不是统计证明，也不能取代用户自己的“像不像我、愿不愿意录”判断。

## 11. 成本、验证与结论

本轮真实量级实验的本地估算费用：

| 阶段 | 费用 CNY |
|---|---:|
| 第一次真实 Run，Editor 本地 32k 阻断前的三个调用 | ¥0.025354 |
| 修复后的五调用主流程 | ¥0.113035 |
| 四调用 A/B | ¥0.145027 |
| 沙箱网络失败 | ¥0 |
| **合计** | **¥0.283416** |

DeepSeek Dashboard 可能因缓存、延迟和展示精度与本地估算略有差异；本地账本的
用途是按 Run/Task 解释和约束调用，不冒充官方结算。

最终工程验证：

- 315 个后端测试通过；
- Ruff check 通过；
- 99 个 Python 文件 format check 通过；
- Fake v8 E2E 通过；
- 真实 checkpoint / restart / Resume / Editor 均实际运行；Reviewer 模型调用
  返回，但其 Task 合同失败并按设计降级；
- A/B 4/4 调用成功，合同 hash 一致；
- Sample 未进入事实引用；
- 日志和公开 manifest 未泄漏 API Key、Prompt 或完整 Sample；
- 私有候选、质量文件、mapping 与合成评分均使用本地忽略目录。

本次实验的最终结论不是“Sample 功能已经完成”，而是：

> 换成一个有四篇稳定写作样本、完整事实素材和明确 Brief 的合成人设后，本轮
> Sample 组与对照组产生了可辨识且方向更好的差异。这个结果与“旧语料太弱导致
> 低区分度”的解释一致，但跨实验没有隔离全部变量，不能当作因果证明。长度控制
> 仍然失败，Reviewer 的逐字证据合同也暴露过一次真实降级。M3 不应继续无限优化
> Prompt，而应把这些状态带进 M4 Trace UI，让用户看见素材是否足够、Draft 是否
> 充分展开、模型自评是否可用，以及为什么系统建议补素材或创建 Revision。

后续只保留两项明确技术债，不在本实验继续扩张：

1. Editor Prompt 中完整 Style Profile 与 Style Segments 有部分重复。只压缩
   模型可见 projection（发送给模型的精简视图）、保留数据库完整 provenance
   （来源链），静态估算可把本次 37,091 字符 bundle 降到约 30,857；尚未实现，
   也未通过真实请求验证，应作为独立性能改动。
2. Reviewer 的模型证据应继续严格校验。未来可以研究一次带明确错误反馈的受控
   repair，或让代码拥有 evidence quote；不能简单放宽为接受模型概括。
