# M3.8：基于现有证据恢复口播时长

这一章解决一个看起来简单、实际很容易做坏的问题：

> 用户选择了 15 分钟，但第一版口播稿明显太短，系统接下来应该怎么办？

最危险的答案是“让模型继续写长一点”。如果没有明确的事实边界，模型很容易：

- 把同一个结论换几种说法；
- 增加排比、总结句和模板化转折；
- 用看似完整但没有新信息的段落凑字数；
- 为了让故事连贯而补出 Source 中没有发生过的细节。

M3.8 的目标不是让任何 Draft 都机械达到规定字数，而是建立一条有边界的恢复
路径：

1. 只计算真正会被说出口的正文；
2. 先盘点已有 Source 中尚未进入正文的事实；
3. 素材足够时，由用户显式发起一次有依据的 Revision；
4. Revision 后重新检查时长、引用、重复和语言质量；
5. 如果继续扩写只能制造废话，就停止并请用户补充素材或降低目标时长。

## 1. 用一盒彩色采访卡片理解这个问题

可以把每个 Source Segment 想成一张采访卡片。卡片上可能写着：

- 搬家当天把钥匙交给房东时发生了什么；
- 朋友打来电话时说了哪句话；
- 新旧住址分别带来了什么感受；
- 用户后来怎样理解当时的选择。

第一版 Editor 可能只拿了其中一部分卡片。稿子太短时，不应该马上要求用户把
已经讲过的故事再讲一遍，也不应该把一张卡片拉长成五段空话。更合理的顺序是：

```text
先数清哪些卡片已经真正进入口播正文
    ↓
从剩余卡片中找有新事实、新场景或新思考的内容
    ↓
告诉 Revision Editor 当前长度、最低长度和优先卡片
    ↓
模型返回一次受限 patch，由服务端合并成一份完整新候选稿
    ↓
重新做硬指标检查和模型审阅
```

“剩余卡片”只是优先候选，不是必须全部用完的 KPI。重复、偏题、只包含写作
指令，或者不适合公开表达的内容，都不应进入最终稿。不过当前 Segment 还没有
结构化 `material_kind`，代码无法在生成前可靠区分事实和编辑指令；M3.8 会在
成稿侧报告明显泄漏，最终仍需人工判断。

## 2. 为什么不能把“达到目标时长”当成唯一目标

口播时长是一个产品目标，不是可以压倒事实和表达质量的命令。一个健康的优先级
应该是：

```text
来源真实与用户隐私
    >
信息增量与表达自然
    >
Creative Brief 和目标时长
    >
形式上的完整
```

因此，15 分钟的目标不等于“无论如何写到 15 分钟”。如果现有材料只能支持
10 分钟，诚实地告诉用户比生成 15 分钟车轱辘话更有价值。

M3.8 允许模型参与编辑，因为模型擅长在多段材料之间做取舍、重组和过渡。但模型
不能自己决定事实是否存在，也不能通过重复调用来无限追分。代码负责提供事实
边界、长度边界和可重复计算的质量信号；模型负责在边界内写出更好的候选稿。

## 3. 只计算真正会被说出口的内容

### 3.1 哪些文字计入口播时长

当前系统只统计：

- `opening.text`；
- 每个 Section 中各个 Paragraph 的 `text`；
- `closing.text`。

字符数采用“非空白字符数”，空格、换行和 Markdown 排版符号不计入。

以下内容不计入口播时长：

- 节目标题与小节标题；
- `source_refs` 内部引用；
- Markdown 中显示的 `[S1]` 和来源索引；
- Show Notes 的摘要、关键点与其他元数据。

这很重要。听众不会把来源索引念出来，系统也不能通过生成更多引用或更长的
Show Notes，假装口播已经达到目标时长。

### 3.2 哪些引用算作“正文已经使用”

“某个 Source Segment 已经使用”也只看口播单元上的引用：

- 开场；
- 正文 Paragraph；
- 收束。

如果某个引用只出现在 Section 元数据或 Show Notes 中，它仍然属于正文尚未使用
的事实材料。否则，Editor 只要把所有 Source ID 挂到 Show Notes，就会让系统
误以为没有可恢复的素材。

这里的 `unused` 有一个必须明确的技术限制：

> 当前它只表示“这个 Source Segment 的引用从未出现在 opening、正文
> Paragraph 或 closing 中”，不能识别“已经引用过，但只用了一句话、仍有很多
> 细节没有展开”。

也就是说，只要一个 Segment 在任意口播单元中被引用过，当前算法就把整段视为
`used`。它不会分析该 Segment 中哪些事实已经表达、哪些事实只是轻轻带过。识别
“已引用但展开不足”需要更细的 claim-level 对齐或人工编辑判断，不属于 M3.8
当前的确定性能力。

数据库仍然保存原始 `source_id` 与 `source_segment_id`，用于追踪事实来自哪里；
用户看到的 Markdown 可以继续使用 `[S1]` 这样的可读引用。内部 ID 不计入口播
正文字符数。

## 4. 15 分钟为什么不是只有一个精确数字

文字阶段使用：

```text
目标字符数 = 目标分钟数 × 每分钟字符数
```

例如 Creative Brief 选择：

```text
目标时长：15 分钟
语速：280 个非空白字符 / 分钟
```

那么目标字符数是：

```text
15 × 280 = 4,200
```

真实录音会受到停顿、笑声、英文比例、临场发挥和个人语速影响，所以系统使用
上下 15% 的可接受区间，而不是要求精确等于 4,200：

| 边界 | 计算 | 结果 |
| --- | --- | ---: |
| 最低可接受长度 | `4200 × 85%` | 3,570 |
| 编辑目标 | `15 × 280` | 4,200 |
| 最高可接受长度 | `4200 × 115%` | 4,830 |

85% 是**文字阶段的产品容差**，不是“12.75 分钟一定合格”的科学定律。用户真正
录制以后，仍应把 `observed_duration_minutes` 作为独立反馈保存，不能用文字
估算覆盖真实录音结果。

## 5. 确定性的 Length Recovery Plan

当父 Draft 低于最低长度时，普通代码会从 Improvement Plan 投影出一份严格的
`length_recovery_plan`。它不调用模型，也不产生 API 费用。

计划包含：

| 字段 | 普通话解释 |
| --- | --- |
| `actual_script_character_count` | 父稿真正口播正文有多少字符 |
| `minimum_script_character_count` | 本轮至少应达到多少字符 |
| `target_script_character_count` | 理想编辑目标 |
| `maximum_script_character_count` | 不应超过的上限 |
| `missing_to_minimum_character_count` | 距离最低要求还差多少 |
| `missing_to_target_character_count` | 距离理想目标还差多少 |
| `available_unused_character_count` | 尚未用于正文的事实片段共有多少字符 |
| `priority_unused_source_refs` | 本轮优先检查的未使用事实片段 |
| `readiness` | 现有材料是否足以恢复长度 |

完整 unused inventory 继续保留用于追踪，但一次 Revision 最多接收 12 个候选。
普通代码先去除完全重复、已经原样复制进口播正文的片段，再按缺失
must-include、Scaffold material gap、补充口述、数字/标点/长度等可解释信号
排序。这个排序不是语义相关性模型，Source 顺序只负责最后的稳定 tie-break。

`readiness` 有四种：

| 值 | 意义 |
| --- | --- |
| `not_needed` | 父稿已经达到最低长度，不需要恢复 |
| `existing_material_sufficient` | 未使用事实的数量足以覆盖最低缺口 |
| `existing_material_partial` | 有可用事实，但数量不足以覆盖最低缺口 |
| `additional_material_required` | 没有可用于扩写的未使用事实 |

这里判断的是“数量上是否有机会”，不是保证这些文字都适合写入节目。
`available_unused_character_count` 只是候选片段的原始非空白字符总量：

- 它没有扣除片段之间重复的内容；
- 它不判断内容是否偏题、敏感或适合当前听众；
- 它不代表模型能够把每个字符无损转换成有价值的口播正文；
- 它更不保证扩写后一定达到最低时长。

模型仍要判断片段是否具体、相关且能够带来信息增量，Revision 后还必须重新检测
质量。即使候选字符数大于长度缺口，系统也只能把 readiness 标记为“有条件尝试”，
不能提前宣称新稿必然合格。

Improvement Plan v2 还保存 `prior_length_recovery_attempted`。第一次生成 Plan
时它为 `false`；一旦完成过一次有来源的恢复，新稿仍短时它变为 `true`。此后
系统不再推荐连续复用同一批候选，而是推荐补充具体材料或降低目标时长。

## 6. 完整决策流程

```text
Quality Report 发现口播正文过短
    ↓
确定性盘点尚未进入正文的事实片段
    ├─ 数量足够
    │    ↓
    │  向用户提供 reuse_unused_material
    │    ↓
    │  用户显式创建一次 Revision 子 Run
    │    ↓
    │  Revision Editor 优先展开有价值的事实与场景
    │    ↓
    │  重新运行硬指标检测和模型 Reviewer
    │    ↓
    │  若仍短：记录已经尝试过恢复，停止连续复用
    │    ↓
    │  推荐补充有锚点的材料，或降低目标时长
    │
    ├─ 只有一部分
    │    ↓
    │  可以先复用，但同时提示还缺哪些具体材料
    │
    └─ 没有可用事实
         ↓
       提示补充具体素材，或降低目标时长
```

用户通过以下接口查看计划：

```text
GET /runs/{parent_run_id}/improvement-plan
```

只有用户明确选择后，才会创建 Revision：

```text
POST /runs/{parent_run_id}/revisions
```

一个最小请求示意如下：

```json
{
  "submission_id": "length-recovery-1",
  "selected_actions": ["reuse_unused_material"],
  "selected_feedback_artifact_ids": [],
  "selected_gap_codes": [],
  "source_ids": [],
  "target_duration_minutes": null,
  "revision_instruction": "优先展开未充分使用的具体场景；不重复、不虚构，也不要求用完全部素材。"
}
```

系统会为子 Run 提供父稿、Creative Brief、允许使用的事实片段和精确的长度恢复
计划。模型被明确要求：

- 优先选择能增加事件、场景、感受或认知变化的片段；
- 首先尝试达到最低长度，再把目标长度当作编辑方向；
- 每一处新增内容都必须来自允许的 Source；
- 不得只在元数据或 Show Notes 中挂引用来假装使用素材；
- 不得为覆盖全部引用而破坏信息密度；
- 素材实际不足时宁可保持短稿，不得重复、灌水或虚构。

### 6.1 为什么长度恢复改用小型 patch 合同

早期实现要求 Revision Editor 每次都重新返回完整 Podcast Draft。真实长稿中，模型
即使已经写出了有价值的新段落，也可能因为重建整棵 JSON 时漏掉 Show Notes 字段、
改变父稿结构或输出被截断而整体失败。让第二次调用再复制一次完整树，会把 Token 和
失败面都浪费在本轮没有要求改变的内容上。

当前只有同时满足以下两个条件的请求改用
`podcast_revision_patch_v1`：

- `selected_actions` 包含 `reuse_unused_material`；
- Task input 带有服务端重新验证过的 `length_recovery_plan`。

模型只能返回：

- 向现有 section 追加的受来源约束 paragraph；
- 少量完整的新 section。

它不能通过 patch 改写父稿标题、开场、收束或 Show Notes。服务端从不可变父稿的深
拷贝开始应用 patch，再把合并后的**完整候选稿**送入原有全部校验：Podcast Draft
结构、topic、初始/补充引用、允许来源范围、Writing Sample 泄漏、新稿确实改变，
以及至少一个 priority recovery ref 真正进入新口播单元。patch schema 或合并结果
不合法时，仍只允许一次同合同的 bounded repair；不会退回“随便给一段文字也接受”。

其他 Revision——例如采用用户反馈、调整语气，或融合新一轮回答——仍返回完整
Podcast Draft。patch 是针对“保留父稿，只用未使用事实增加口播正文”这一窄问题的
可靠性合同，不是通用编辑接口。

## 7. 为什么必须是“一次显式 Revision”

系统没有设置“Reviewer 不满意就自动让 Editor 重写，直到分数够高”的循环。

这样设计有四个原因：

1. 每次模型调用都会增加耗时和费用；
2. 同一个模型反复改自己的稿子，可能越来越迎合评分规则；
3. 自动循环会掩盖“真实素材不足”这个产品事实；
4. 用户应该知道哪一版用了哪些材料，并决定是否继续修改。

父 Run 保持不可变，子 Run 有自己的 Task、Artifact、Event、模型调用账本和
预算。重复提交同一个 `submission_id` 会安全返回原子 Run；同 ID 却提交不同
请求会被拒绝。这让网络重试不会意外创建多份付费 Revision。

## 8. Revision 后怎样判断“真的变好了”

字数增长本身不等于质量提升。新候选稿必须重新经过两层证据。

### 8.1 普通代码负责的硬检查

代码会稳定检查：

- **时长**：正文是否进入 85%—115% 区间；
- **引用**：口播段落引用是否完整，新增引用是否来自允许的事实 Source；
- **信息使用**：是否真正增加了此前未进入正文的事实引用；
- **重复**：完全相同段落和局部重复是否恶化；
- **filler**：固定口语填充词是否过密；
- **中文模板风险**：排比、模板化转折、“不是……而是……”等可观察模式；
- **编辑指令泄漏**：是否把“这句话如果要用，前面应先……”之类元编辑文字
  当成真正口播；
- **Brief 约束**：明确要求出现或避免的表达是否被满足。

这些规则不判断“是不是 AI 写的”，也不判断故事是否动人。它们只报告可以被
重复计算的风险。时长、引用和严重重复等硬事实不能被模型给出的高分抵消。
比较父子稿 warning 时，时长 warning 单独处理：从 blocker 降为 warning 是
进步，不能被误判成“其他质量 warning 增加”。列举规则也要求“最后，”这样的
列举标记，普通“最后一个空行李箱”不再误报。这次语义变化分别使用
`draft_quality_rules_v3_editorial_instruction`、
`zh_podcast_style_v2_enumeration_precision` 和
`deterministic_quality_facts_v2_editorial_instruction` 标识；旧 v1/v2 Artifact
仍可按原版本读回，不会被新规则静默重算。

### 8.2 模型 Reviewer 负责的编辑判断

模型 Reviewer 更适合判断：

- 是否符合目标听众、场景、语气和沟通目标；
- 是否忠实于 Source，没有把计划、愿望或推测改写成已发生事实；
- 是否选中了最有价值的材料，而不是机械覆盖全部材料；
- 是否增加了具体性、结构层次和口播自然度；
- 是否仍然冗余或出现明显套话；
- 有合格 Writing Sample 时，是否更接近用户的个人表达。

Reviewer 的结论仍是 advisory。即使它把六维或七维都打得很高，只要正文还低于
硬性下限、引用不完整或重复严重，系统仍应显示需要修订，而不是“可以直接发布”。

为了减少“模型看对了，却在复制逐字证据时少一个标点”造成的伪失败，当前代码先为
Draft block 生成 bounded `D001`、`D002`……证据目录；Writing Sample 可用时，再
生成 style-only 的 `W001`、`W002`……目录。模型只选择 opaque ID，Provider 在服务端
把它 hydration 成代码保存的 location、短逐字 quote 和 Source reference，然后才
执行原有严格 Reviewer validator。未知、重复或越界 ID 不会被猜测，而会变成无效
证据并触发失败。

Reviewer 输出失败最多获得一次 repair-specific 调用。第二次必须重新选择目录中已有
ID，且仍经过完全相同的 Schema、逐字 quote、引用范围和 style-only 证据校验；规则
不会因为是 repair 就放宽。如果第二次仍失败，系统保留确定性报告和可导出的 Draft，
把自动审阅标记为不完整。这一设计提升的是可满足性，不是把 Reviewer 变成安全真相。

### 8.3 用户负责最后的真实判断

只有用户能回答：

- 这像不像我；
- 我愿不愿意真的开麦录；
- 某个细节是否太私人；
- 真实录制用了多长时间；
- 新增内容是否有意义，而不是为了数字存在。

因此，最终稿仍需要保存 `voice_match_rating`、`recordability_rating`、
`observed_duration_minutes` 和文字反馈。

## 9. 如果 Revision 后仍然不够长

系统不会自动再跑第二轮。

### 情况 A：第一次恢复后仍有具体材料

质量报告应指出：

- 还差多少字符或估算分钟；
- 哪些高价值片段仍未进入正文；
- 哪些段落只给了结论，缺少场景或过程；
- 当前重复、模板化和编辑指令泄漏风险有没有上升。

剩余片段仍可以让用户人工查看，但系统不再把连续
`reuse_unused_material` 标成推荐动作。下一步默认是补充新的具体事实，或者降低
目标时长；这避免模型为了长度不断翻炒同一批材料。

### 情况 B：只剩少量或低价值材料

系统应提出有锚点的补充问题，例如：

- “交接钥匙那天，你进入房间后第一眼看到了什么？”
- “朋友打来电话前后，你对搬家的理解发生了什么变化？”
- “旧地址和新地址分别让你最不舍、最期待的是什么？”

用户的回答先作为新的事实 Source 导入，再创建新的 Revision。问题必须说明
为什么要补，并尽量锚定已有 Scaffold 或 Source，不能只说“请再多讲一点”。

### 情况 C：用户不想继续补充

可以把 15 分钟改成系统实际提供的更低预设，例如 10 分钟。降低目标比强行拉长
一篇已经完整的短稿更合理。

## 10. Fake 验证了什么

本地 Fake Provider 是免费的、确定性的、可重复的。最终 Fake v8 中：

| 指标 | 结果 |
| --- | ---: |
| 父稿口播正文 | 456 字符 |
| 15 分钟目标 | 4,200 字符 |
| 85% 最低下限 | 3,570 字符 |
| Fake Revision 后正文 | 2,083 字符 |
| 优先引用使用 | 12 / 12 |
| 工作流 / 内容验收 | pass / fail |
| 下一步 | `add_supplemental_material` |

这个结果证明：

- 时长只按 spoken-only 正文计算；
- Improvement Plan 找到了未使用事实；
- 精确的 Recovery Plan 进入了 Revision Task；
- Fake Revision 确实新增事实，但使用完 12 个候选仍低于 3,570 下限；
- 明显进入口播的编辑指令会被
  `style.editorial_instruction_leakage` 检出；
- 一次恢复后不再推荐连续复用，而是转补材料或降时长；
- 父 Run 没被覆盖，子 Run 可以继续接受 Quality Report 和 Reviewer；
- 工程状态机、引用合同和长度边界可以稳定复现。

它**不能**证明：

- 2,083 个字符就是一篇自然、动人的口播稿；
- DeepSeek 一定会选择同样的材料；
- 文稿真的像某个用户；
- 用户愿意直接录制；
- 真实录音一定达到 12.75 分钟。

因此 Fake 的 `workflow_passed=true` 和 `content_acceptance_passed=false` 并不
冲突：前者证明编排和刹车正确，后者诚实说明 15 分钟内容没有完成。

## 11. 怎样在本地验证

### 11.1 运行零费用自动测试

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_draft_improvement.py \
       tests/test_draft_quality_provider.py \
       tests/test_draft_quality_workflow.py \
       tests/test_revision_schemas.py \
       tests/test_revision_workflow.py \
       tests/test_length_recovery_e2e.py -vv
```

重点观察以下测试语义：

- Show Notes 或 Section 元数据的引用不会被误算为正文已经使用；
- Recovery Plan 的实际、最低、目标、最高和缺口数字相互一致；
- 优先引用必须解析到真实事实 Segment，且父稿正文尚未使用；
- 选择 `reuse_unused_material` 时必须同时带 Recovery Plan；
- 只有 `reuse_unused_material + length_recovery_plan` 使用 patch，其他 Revision
  继续要求完整 Draft；
- patch 必须由服务端合并父稿，并通过完整 Draft 与来源合同校验；
- Fake Revision 增加正文长度和新的事实引用；
- 新稿引用仍然不能越过允许的事实集合；
- Reviewer 只返回 `Dxxx` / `Wxxx`，服务端 hydration 后仍执行相同严格校验；
- Reviewer 输出不合格只允许一次 bounded repair，第二次失败走可审计降级。

### 11.2 用 Swagger 手动走一次 Fake 流程

保持：

```text
EPIPHANY_MODEL_PROVIDER=fake
```

重启 Uvicorn 后：

1. 用足够完整的合成材料创建一个带 15 分钟 Creative Brief 的新 Run；
2. 让 Run 完成 Editor、Reviewer 和 Quality Report；
3. 调用 `GET /runs/{run_id}/improvement-plan`；
4. 确认 `duration_resolution` 是 `reuse_unused_material`，并查看未使用事实；
5. 调用 `POST /runs/{run_id}/revisions`，显式选择一次恢复；
6. 轮询子 Run，直到 `succeeded`；
7. 分别查看子 Draft、Quality Report 与
   `GET /runs/{child_run_id}/revision-comparison`；
8. 重新读取父 Run，确认父稿和父模型调用数没有变化。

如果 Improvement Plan 直接要求补材料，通常意味着当前测试 Source 没有留下足够
的未使用事实，不代表状态机失败。要验证本章路径，需要准备“第一稿没有用完，但
剩余内容足以填补最低缺口”的测试材料。

### 11.3 用固定长素材运行受保护的 Fake E2E

M3.8 提供一个固定真实量级合成人设的驱动器。默认命令只打印脱敏预检，不创建
数据库或产物，也不会联网：

```bash
python -m epiphany.length_recovery_e2e
```

确认预检中的 Creative Brief、3,570 / 4,200 / 4,830 边界、模型和最多七次调用
都正确后，再显式运行零费用 Fake：

```bash
python -m epiphany.length_recovery_e2e --provider fake --execute
```

这条命令自动模拟了用户的显式决定，但报告仍保存：

- 一个父质量 Run 和一个 Revision 子 Run；
- `automatic_revision_count=0` 与 `explicit_revision_count=1`；
- 五次父调用与两次子调用的硬上限；
- 父子 Draft、Quality Report、Improvement Plan 与 Comparison；
- 新使用和尚未使用的优先引用数量；
- 重复、filler、中文风格风险与 Reviewer 分数；
- 脱敏 JSONL 日志和按币种分组的零费用摘要。

输出位于 Git 忽略的本地数据库和 Artifact 目录，不应提交仓库。

## 12. 受控 DeepSeek 验收结果

Fake 通过以后，才有必要花费真实模型调用。真实验收应使用一组固定且足够完整的
合成 Persona、事实 Sources、补充口述、Creative Brief 和 Writing Sample，从父稿
一直跑到显式 Revision 后的新报告。

本地 `.env` 至少明确：

```text
EPIPHANY_MODEL_PROVIDER=deepseek
EPIPHANY_DEEPSEEK_API_KEY=只保存在本地的真实Key
EPIPHANY_DEEPSEEK_MODEL=deepseek-v4-flash
EPIPHANY_DEEPSEEK_REVIEWER_MODEL=deepseek-v4-pro
EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY
```

这里可以让 Flash 承担 Editor、Pro 承担 Reviewer，观察不同档位的编辑与审阅
表现。但它们仍属于同一家族，不能称为完全独立的裁判。是否使用 Pro 是一次受控
实验选择，不是安全机制；硬指标仍由代码掌握。

真实运行前应先做 dry-run 或手工核对：

- API Key 已加载，但不会出现在输出和日志；
- Provider、Editor Model、Reviewer Model 和币种符合预期；
- 单 Run 调用上限足够覆盖本次流程，但没有无限额度；
- 输入 Source、Writing Sample 和目标时长已经冻结；
- 只创建一个父 Run 和一个显式 Revision 子 Run。

受保护驱动器的显式付费形式是：

```bash
python -m epiphany.length_recovery_e2e \
  --provider deepseek \
  --editor-model deepseek-v4-flash \
  --reviewer-model deepseek-v4-pro \
  --execute
```

### 12.1 历史 v2：工程成功、内容仍短

固定合成人设的 DeepSeek v2 曾按这条命令执行固定五次父 Run 调用和两次子 Run
调用；该历史驱动器没有额外输出修复调用。结果是：

| 指标 | 父稿 | 子稿 |
| --- | ---: | ---: |
| 口播字符 | 1,310 | 2,371 |
| 估算分钟 | 4.68 | 8.47 |
| 新使用优先引用 | - | 6 / 12 |
| 确定性分数 | 61 | 88 |

七次调用合计 86,497 input tokens、17,496 output tokens、159,228 ms Provider
耗时，本地估算费用为 ¥0.201153 CNY。`workflow_passed=true`，但子稿仍低于
3,570 下限，所以 `content_acceptance_passed=false`。

原始 v2 规则曾把 duration blocker 降为 duration warning 也计入 warning 总数，
并把普通“最后”误判为列举。当前代码已把时长 warning 分开比较，并要求列举
标记；无需重新调用模型。当前规则还能从持久子稿中识别出一句
editorial instruction leakage。修正规则没有产生新付费调用。

真实运行保存了：

- 父稿与子稿 Markdown；
- Improvement Plan 和 Recovery Plan；
- 父子 Draft Metrics、Quality Report 与 Comparison；
- 新增事实引用和仍未使用的事实引用；
- 每次 ModelCall 的 provider、model、状态、输入/输出 Token、耗时；
- `estimated_cost_micros` 与 `cost_currency`；
- Run、Task、Artifact 和 Event 的 ID；
- 用户或人工复核对“像不像本人、愿不愿意录”的结论。

最后一项仍待真人完成；自动指标和同家族 Reviewer 不能代替“我愿不愿意录”。
完整失败、费用、父子稿定性审阅和安全证据见
[M3.8 实验报告](../experiments/m3-8-grounded-length-recovery-e2e.zh-CN.md)。

这个失败结果必须保留。它证明“把 Recovery Plan 传给模型”本身还不等于模型会
充分利用素材，也推动了后来的 patch 输出合同、Reviewer 证据目录和定向补充采访。

### 12.2 真实浏览器闭环：patch 恢复后，再用四个回答达标

2026-08-03，另一组固定合成 Persona 通过真实页面和 DeepSeek 走完了三条不可变
Run。它不是把历史失败覆盖掉，而是验证修复后的完整决策链：

| Run | 操作 | 口播字符 | 估算时长 | 关键结果 |
| --- | --- | ---: | ---: | --- |
| `run_c41c726fdcca4136bd1e317dbcbce21a` | 初始父稿 | 2,831 | 10.11 分钟 | 低于 12.75 分钟下限 |
| `run_c344c19e9cb844c29c4daac81434cb00` | `reuse_unused_material` grounded recovery | 3,530 | 12.61 分钟 | patch 合并成功，但仍差 40 字符，不能冒充达标 |
| `run_2fec917404234405b9ec7c2c9ab16802` | 四个定向回答后的显式 Revision | 4,086 | 14.59 分钟 | 时长进入区间，段落引用覆盖 100% |

第二条 Run 证明了 `podcast_revision_patch_v1` 的价值：模型只提交新段落，服务端把
它们合并到父稿，再运行完整校验；Reviewer 首次证据输出不合法时，bounded repair
按相同严格合同成功。由于 12.61 仍小于精确的 12.75 下限，系统没有按显示分钟数
四舍五入，而是基于最新稿生成有原句 Anchor 的补充问题。

第三条 Run 使用用户明确回答的四个问题作为新 Source，再由显式 child Revision
融合。最终稿没有完全重复段落，引用覆盖 100%，但综合分仍因可观察的并列/对照表达
warning 被非补偿上限封到 79，decision 为 `revision_recommended`。这正是期望边界：
时长达标只解决一个约束，不会覆盖口播自然度风险，也不会自动发布。

三条 Run 中没有“Reviewer 不满意就继续写”的隐藏循环。第一次 child 来自显式已有
素材恢复；第二次 child 来自四个新增回答和另一份显式请求。若回答后仍短或表达风险
继续恶化，系统仍应停在候选稿和报告，交给用户选择继续补材料、做一次明确修订或降低
目标，而不是自动无限扩写。

## 13. 日志和数据库应该看什么

一次完整恢复可以通过这些事件串起来：

```text
workflow.draft_metrics.evaluated
workflow.draft_quality.completed
workflow.draft_improvement.planned
workflow.draft_revision.requested
workflow.draft_revision.queued
model.call.started
model.call.completed
workflow.draft_revision.compared
```

发生失败时还要检查：

- ModelCall 是否为 `failed` 或超时；
- Task 的 `attempt_count`、租约和错误代码；
- 父子 Run 的 `parent_run_id` 是否正确；
- Revision Request 与 Plan Artifact 是否属于同一个父 Run；
- `submission_id` 是否因为网络重试被幂等复用；
- 新增引用是否能解析到本轮允许的 Source Segment。

日志应记录 ID、状态、计数、耗时和费用，不应打印：

- API Key；
- 完整 Prompt；
- 完整 Source 或 Writing Sample；
- 用户私人反馈全文；
- 完整生成稿。

这些正文和结构化产物可以保存在本地 SQLite 与 Artifact 中，但仍属于敏感数据，
不应随日志上传或提交 Git。

## 14. 当前边界与仍需学习的地方

M3.8 解决了“先用已有证据，再决定是否补材料”的编排漏洞，但仍有明确边界：

- “某引用已使用”只能证明它进入某个口播段落，不能证明片段中的每个细节都被
  充分展开；
- 未使用字符数是数量近似，不代表所有字符都有相同信息价值；
- Source Segment 没有结构化 `material_kind`，当前只能在成稿侧检测部分明显
  编辑指令，不能可靠预先分类；
- 280 字符/分钟只是可配置估算，不同用户需要用真实录音校准；
- 中文启发式规则只能发现可观察模式，不能输出可靠的“AI 概率”；
- 同模型或同家族 Reviewer 可能偏宽松，不能替代硬规则和真人审稿；
- 达到 85% 下限只是进入合理区间，不代表稿件已经可以发布。

这一阶段真正完成的能力可以概括为：

> 系统不会在稿子偏短时立刻要求用户重复提供材料，也不会偷偷让模型无限扩写。
> 它先计算正文里还缺多少，再列出现有而未使用的事实，由用户触发一次有来源、
> 有预算、有日志、可复核的 Revision；如果仍然不足，就停止连续复用，诚实地
> 补材料或降时长。

历史 v2 只证明模型增加了真实信息、却没有越过下限；后续真实浏览器闭环又证明：
小型 patch 能可靠利用已有材料，仍短时可以用最新稿定向收集四个新事实，并在下一条
显式 Revision 中达到时长区间。最终 79 分和 `revision_recommended` 同时提醒我们：
完整工程闭环不等于内容已经可以发布，真人仍要判断“像不像我、愿不愿意录”。
