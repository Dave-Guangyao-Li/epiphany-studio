# M5.1b：真实浏览器 + DeepSeek 全流程 E2E

状态：**工程闭环通过；内容仍建议修订**

实验日期：2026-08-03

> 本文不是一份只验证 HTTP 200 的烟雾测试。它记录一个合成用户如何从 0 Source
> 开始使用 AI 起步助手，以及另一个合成用户如何把多份生活素材加工成 15 分钟目标
> 的播客候选稿。所有操作都经本地 React UI 和 Playwright 完成，模型为真实
> DeepSeek；数据库、Run trace、Artifact、Token 和 CNY 费用均被核对。

## 1. 最终结论

| 验收面 | 结果 | 证据 |
| --- | --- | --- |
| 浏览器完整操作 | PASS | Project、Source、Writing Sample、Run、检查点、Revision、导出均可从 UI 操作 |
| Durable workflow | PASS | 三条不可变 parent/child Run 均可恢复并到达 `succeeded / complete` |
| 15 分钟时长下限 | PASS | 最终 4,086 个非空白字符，估算 14.59 分钟；高于 12.75 分钟下限 |
| 来源引用 | PASS | 26/26 正文段落有引用，覆盖 5 Sources / 31 Segments |
| 安全边界 | PASS | Writing Sample 未被当成事实；AI 起步候选未自动保存；最终稿无内部 ID |
| 重复硬指标 | PASS | 完全重复段落 0，重复 8 字窗口比例 0.002 |
| 内容可直接发布 | **未通过** | 综合分受上限约束为 79，7 次平行对照句，仍有少量语义重复 |
| 真人可录性 | PENDING | 需要真实用户判断“像不像我、愿不愿意开麦” |

这说明系统已经能完成可靠的产品闭环，但“工作流成功”不等于“内容已经优秀”。
质量闸门没有被模型的高分自评覆盖，最终仍给出 `revision_recommended`。

## 2. 测试环境与安全边界

| 项目 | 设置 |
| --- | --- |
| Frontend | React/Vite，`http://127.0.0.1:5174` |
| Backend | FastAPI，`http://127.0.0.1:8000` |
| 持久化 | 本地 SQLite；数据库不提交 |
| Provider | DeepSeek |
| 模型 | `deepseek-v4-flash` |
| 币种 | CNY |
| 驱动 | Playwright CLI，使用页面按钮、输入框和确认框 |
| 数据 | 完全合成，不对应真实用户 |

约束：

- API Key 只在被 Git 忽略的本地 `.env` 中；日志和本文不记录 Key；
- Source 内容不写入普通应用日志；
- Writing Sample 只提供风格，不能成为事实证据；
- AI 候选稿不会自动导入 Source，用户必须编辑并确认；
- 失败 Run 保留失败状态，修复后创建新的 child Run；
- 达不到时长时只能利用有价值的未展开素材或询问用户，不能用空话补齐。

可复现输入位于
[`backend/fixtures/e2e/m5-1b-real-browser/`](../../backend/fixtures/e2e/m5-1b-real-browser/)。

## 3. 旅程 A：0 Source 的 AI 起步助手

### 3.1 场景

合成用户完全没有潜水经验，只知道自己对水下世界好奇，同时担心耳压和失控。
Project 初始为 0 Source。相同的 Project、Source 标题和意图分别运行：

- `exploration_outline`：帮助用户发现可回答的问题；
- `starter_draft`：给正文编辑框一份可改写的第一笔。

输入和最终人工确认文本见：

- [`source-starter-scenario.zh-CN.json`](../../backend/fixtures/e2e/m5-1b-real-browser/source-starter-scenario.zh-CN.json)
- [`source-starter-confirmed-user-edit.md`](../../backend/fixtures/e2e/m5-1b-real-browser/source-starter-confirmed-user-edit.md)

### 3.2 真实结果

| 模式 | Run | 调用 | Token | 费用 | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| 探索提纲 | `run_3c637233a17843119f546c1521ec0024` | 1 | 1,432 / 729 | ¥0.002890 | 严格校验一次通过；用户取消，未导入 Source |
| 示例草稿 | `run_8eda93938e4b4a1881eb47453bd5073f` | 2 | 3,127 / 1,527 | ¥0.004801 | 第二次仍含无依据第一人称；服务器逐行 grounding 后交给用户编辑 |

示例草稿没有被简单判死，也没有把虚构经历悄悄保存。执行路径是：

```text
真实模型候选
  -> 严格结构和安全校验
  -> 最多一次模型修复
  -> server_line_grounding 保留安全、话题相关的内容
  -> 无法安全保留时才退到 server_safe_template
  -> awaiting_source_confirmation
  -> 用户编辑 + 明确勾选确认
  -> 导入 Source
```

用户最终确认导入 516 字 Source：
`src_65decd08d6282ace16615e26dcde0b09`。其 provenance 为
`origin=ai_assisted`、`user_confirmed=true`、
`fallback=server_line_grounding`。

重要解释：516 字正文是**合成用户核对和补写后的内容**，不是模型直接生成的事实。
零个人素材时，AI 能提供问题、结构和第一笔，但不可能凭空产生可信的个人经历。

### 3.3 UI 发现

- 刷新或重新进入页面后，mode 与 intent 必须从 Run input 恢复；已补回归测试；
- 生成过程的“准备上下文、模型生成、校验结果、等待确认”步骤能在页面展示；
- 模型成功不代表候选可用，Trace 必须同时显示 attempt、fallback 和错误码；
- 专业潜水信息仍标记为待核实，Source Starter 不是联网事实研究工具。

## 4. 旅程 B：15 分钟个人叙事播客

### 4.1 Persona 与 Creative Brief

徐棠，30 岁，广告策划。结束七年关系后从北京搬到杭州，第一次独居。她想讲
“一个人吃晚饭”如何从被剩下的证据，慢慢变成一件不需要观众的普通生活。

| 字段 | 值 |
| --- | --- |
| 场景 | 单人叙事播客 `narrative_solo` |
| 目标 | 15 分钟，280 个非空白字符/分钟 |
| 受众 | 25—35 岁，刚经历分手、搬家或第一次独居的人 |
| 语气 | 具体、诚实、有一点自嘲，不强行治愈 |
| 必须出现 | 焦掉的番茄炒蛋、半把葱、蓝边碗、邻居借开瓶器、母亲视频和水饺 |
| 避免 | 空泛总结、大量排比、强行升华、模板对照句、泄漏 Writing Sample 事实 |

初始事实来自 3 份 Source，另有 1 份 `style_only` Writing Sample；两个材料检查点
继续补充邻居、购物清单、厨房与餐桌细节。完整分工见 fixture README。

### 4.2 实际浏览器步骤

1. 新建 Project，导入 3 份事实 Source；
2. 导入 Writing Sample，并确认拥有内容和允许模型处理；
3. 创建 15 分钟 `episode-research` Run；
4. 在两个 Durable 人工检查点分别补充口述；
5. 生成有引用的采访脚手架、初稿、Show Notes 和质量报告；
6. 初稿时长不足，创建一次有证据的 `reuse_unused_material` Revision；
7. 恢复稿仍短，系统阅读当前稿件并生成 6 个具体场景问题；
8. 在 UI 回答 q1/q2/q4/q6，系统导入为新 Source；
9. 创建第二条 child Revision，生成最终候选稿并重新 Reviewer；
10. 在 Run Trace 检查 Task、Event、Model Call、费用、Artifact 和 Markdown。

## 5. 三段成功 Run 链

| 阶段 | Run | 调用 | 正文 / 时长 | 引用范围 | 费用 |
| --- | --- | ---: | --- | --- | ---: |
| 父稿 | `run_c41c726fdcca4136bd1e317dbcbce21a` | 6 | 2,831 / 10.11m | 4 Sources / 22 Segments | ¥0.093521 |
| 有依据的时长恢复 | `run_c344c19e9cb844c29c4daac81434cb00` | 4 | 3,530 / 12.61m | 4 / 28 | ¥0.084374 |
| 定向回答 Revision | `run_2fec917404234405b9ec7c2c9ab16802` | 3 | 4,086 / 14.59m | 5 / 31 | ¥0.078293 |
| **成功主链合计** | — | **13** | — | — | **¥0.256188** |

三条 Run 都是 `succeeded / complete`，后两条通过 `parent_run_id` 形成不可变谱系。

### 5.1 父稿：模型完成不等于时长达标

父稿结构、引用和具体场景可用，但只有 10.11 分钟。系统没有让模型为了 15 分钟
自由发挥，而是先计算正文实际利用了哪些 Segment，以及仍有哪些高价值素材未展开。

### 5.2 第一次 Revision：小 patch，不重写整篇

仅当 action 为 `reuse_unused_material` 且存在受控 `length_recovery_plan` 时，模型使用
`podcast_revision_patch_v1`：返回新增/替换的口播单元，服务端再与不可变父稿合并，
执行和完整稿完全相同的来源、引用、重复、时长和安全校验。

这次从 10.11 分钟提升到 12.61 分钟，并新增有来源的场景；没有把所有未使用素材硬塞
进去。Reviewer 使用 D001/W001 这类不透明证据 ID 选择证据，服务端再映射为可逐字
验证的 quote/location/reference；第一次不合规时允许一次 bounded repair，但不放松
验证器。

恢复后仍低于 12.75 分钟下限。系统停止自动扩写，并根据当前稿件提出 6 个问题，
而不是继续让模型改写同一批话。

### 5.3 第二次 Revision：用用户的新事实完成闭环

用户在页面回答 q1/q2/q4/q6，形成 1,007 字补充 Source：
`src_64646662b09fa5cbcfc045b5ea82595f`。精确问答见
[`targeted-interview-answers-round-1.md`](../../backend/fixtures/e2e/m5-1b-real-browser/targeted-interview-answers-round-1.md)。

这条 `add_supplemental_material` Revision 仍要求完整 Draft。Editor 第一次返回的结构
不合规，第二次 bounded repair 成功；Reviewer 第一次成功。最终结果：

| 指标 | 值 |
| --- | ---: |
| 非空白正文字符 | 4,086 |
| 估算时长 | 14.59 分钟 |
| 目标下限 | 12.75 分钟 |
| 引用覆盖 | 26 / 26 段（100%） |
| 来源范围 | 5 Sources / 31 Segments |
| 完全重复段落 | 0 |
| 重复 8 字窗口比例 | 0.002 |
| 确定性质量分 | 94 |
| 最终综合分 | 79（受不可补偿上限约束） |
| 决策 | `revision_recommended` |

最终 Markdown 使用 `[S1]` 等可读引用，文末映射来源标题和段落；内部
`src_.../seg_...` 只留在数据库。Writing Sample 中的公交经历没有进入事实正文。

## 6. 为什么 14.59 分钟仍不是“可以发布”

DeepSeek Reviewer 多个维度给出高分，但确定性分析检出 7 次平行对照表达，并且人工
阅读发现番茄炒蛋、蓝边碗等意象有轻微语义重复。系统采用两层证据：

```text
模型 Reviewer：语义、结构、声音、受众适配、编辑建议
确定性规则：时长、引用、重复、模板句、Brief 明确约束
```

硬事实可以设置不可补偿的分数上限。因此模型即使倾向于给高分，也不能把明显太短、
引用不足或模板句过多的稿子宣布为可发布。最后一票仍属于真实用户：是否像本人、
是否愿意录、实录时长是多少。

## 7. 本轮发现并修复的问题

### 7.1 Source Starter 输出合规但事实不安全

问题：模型可返回结构正确、却虚构第一人称经历的 JSON。

修复：统一严格校验；一次模型修复后仍不安全时逐行 grounding，只保留有上下文支持
或明确标为候选/待核实的内容；最后才使用安全模板。Artifact 保存 execution
provenance，UI 仍要求用户编辑确认。

### 7.2 Revision 整树 JSON 太脆弱

问题：时长恢复要求模型重发整篇树，既浪费 Token，又容易在长 JSON 中漏字段；旧
Prompt 只说“利用未使用素材”，没有告诉模型当前/最低/目标字符差额和具体 Segment。

修复：时长恢复改为受限 patch contract，传入当前、最低、目标、缺口和候选 Segment；
服务端负责合并并完整校验。其他 Revision 不受影响，仍返回完整 Draft。

### 7.3 Reviewer 自己复制证据不稳定

问题：模型的 `exact_quote` 可能差一个标点，导致严格验证失败。

修复：服务端建立有界 D/W evidence catalog；模型只选 ID，服务端回填精确证据；未知、
重复或越界 ID 仍被拒绝，并最多修复一次。

### 7.4 元编辑指令泄漏检测漏报

问题：恢复稿曾出现“如果稿子需要更长，我希望展开的是……”这类面向编辑者的话，
旧启发式没有命中。

修复：扩展中文 editorial-instruction 规则并增加回归测试。最终稿不含该句。

### 7.5 Run 页面请求不存在的可选产物

问题：v9 Revision 页面仅凭 workflow version 请求 supplemental plan，且显示父 Run 才
有的采访脚手架，产生 409。

修复：UI 根据实际 Artifact/Task 做能力门控；不存在的导出按钮不显示，也不发送请求。
干净浏览器会话验证 console error 为 0。

### 7.6 长会话中的一次旧页面跳转

一条长期 Playwright 会话出现过旧 Project 导航，但 trace 没有发现产品触发的跳转，
隔离新会话也无法复现。因此它被记录为测试会话状态污染，不在没有证据时伪装成产品
Bug。

## 8. Playwright 证据

本地 trace 被 `.gitignore` 排除，不上传 GitHub：

- `.playwright-cli/traces/trace-1785751214432.trace`：Source Starter 与最终 Run 检查，
  包含修复前的一次可选产物 409；
- `.playwright-cli/traces/trace-1785751879610.trace`：修复后的隔离 UI 能力门控验证，
  最终 Revision 只显示存在的三项导出，console error 为 0。

这两份 trace 只用于本机复盘；可提交证据是合成 fixture、自动测试和本文指标。

## 9. 费用解释

- 成功主链三条 Run：13 次调用，**¥0.256188**；
- Source Starter 两种模式：3 次调用，**¥0.007691**；
- 主播项目中包含早期失败实验在内的所有历史调用：34 次，**¥0.511260**。

后一个数字不能被误写成“一次正常用户旅程的成本”。项目账本是基于配置价格的估算，
应与 DeepSeek Dashboard 做数量级核对；官方结算可能因价格版本、缓存和时间延迟不同。

## 10. 自动回归

真实付费 E2E 之后，使用强制 Fake Provider 跑全量回归，避免测试误读 `.env`：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
env EPIPHANY_MODEL_PROVIDER=fake ./.venv/bin/pytest -q
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .

cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm test -- --run
npm run build
```

Fake 测试负责稳定复现状态机、重试、校验和 UI 行为；真实 E2E 负责暴露长中文输出、
模型漂移和用户体验问题。两者不能互相替代。

## 11. 剩余债务与下一步

1. `add_supplemental_material` 仍返回完整 Draft，本轮第一次结构失败后靠 bounded repair
   恢复；这是可靠性债务，但不是数据丢失。
2. 质量报告 UI 当前偏原始，部分内容仍像 JSON 调试面板；应做可读维度卡片和证据展开。
3. 平行对照和语义重复已被检测，但还没有“一键根据 Reviewer 建议创建显式 Revision”
   的精细编辑体验。
4. Source 编辑区仍是纯文本；这是当前阶段的有意边界，不阻塞 Source Starter，后续可
   增加可视化编辑而不改变后端契约。
5. 真正发布前仍需真人给出 voice match、recordability 和 observed duration。

因此，本实验的准确结论是：**真实浏览器 + 真实 DeepSeek 的工程生产链已经闭环；
内容系统能诚实停在“建议修订”，而不是为了展示成功把候选稿冒充成终稿。**
