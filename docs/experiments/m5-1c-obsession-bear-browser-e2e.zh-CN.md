# M5.1c：《Obsession》Bear 视角真实浏览器 E2E

状态：**浏览器与 Durable Workflow 闭环通过；候选稿仍需一次小修，不可标记为可直接录制**

实验日期：2026-08-04

> 这不是只检查接口是否返回 `200` 的烟雾测试。测试从一个真实内容产品用户的
> 视角，经 React UI、Playwright 和真实 DeepSeek 调用完成 Project、Source、
> Writing Sample、Creative Brief、人工材料检查点、初稿、显式 Revision、定向补充
> 采访、质量报告和人工反馈；再从平台运营者视角核对 SQLite 中的 Run lineage、
> Task、Artifact、Event、ModelCall、Token、费用、失败与重试。

## 1. 最终结论

| 验收面 | 结果 | 证据 |
| --- | --- | --- |
| 真实浏览器旅程 | 核心闭环 PASS；有一处 UI 缺口 | Project、9 个 Project Source、Run、检查点、质量报告和反馈均可在 UI 中追踪；最后一次 feedback Revision 只能从 API 创建 |
| Durable workflow | PASS | 失败 Run 原样保留；成功主链由不可变 parent/child Run 组成，最终到达 `succeeded / complete` |
| 15 分钟时长下限 | PASS | 最终正文 4,055 个非空白字符，估算 14.48 分钟，高于 12.75 分钟下限 |
| 来源引用 | PASS | 24/24 正文段落有引用，覆盖 6 Sources / 31 Segments |
| 事实与创作分层 | PASS | 公开剧情、主创解释、合成非正史日记和 style-only Writing Sample 分开存储 |
| 第一人称知识边界 | PASS（经人工修订） | 最终稿停在 Bear 失去意志并走向死亡以前，不再描述他死后 Nikki 看见的现场 |
| 重复硬指标 | PASS | 完全重复段落 0，重复 8 字窗口比例 0 |
| 自动质量评估 | 完成但不批准发布 | 确定性 98，模型 80，综合分受 warning 上限约束为 79，决策 `revision_recommended` |
| 人工可录性 | **未通过** | 最终反馈：voice 3/5、recordability 3/5、`would_record_as_is=false` |
| 实录时长 | TODO | 本轮只有文字估算，尚未录制音频并填写 `observed_duration_minutes` |

系统没有把“工作流成功”“模型给分”和“用户愿意录”混成同一件事。最终 Run 已经
可靠完成，但最终 Artifact 仍只是一篇认真可读的候选稿。

## 2. 研究、版权与认识边界

本实验使用电影《Obsession》（2026）的角色和公开剧情锚点，因此必须先建立内容
边界，不能把模型生成能力误写成取得了剧本授权或完成了法律判断。

### 2.1 使用了什么

公开事实与主创解释来自 fixture 中列明的公开页面：

- Focus Features 官方电影页和导演采访；
- AP 对导演 Curry Barker 的采访；
- TheWrap 结局解析；
- Variety India 导演采访；
- India Today 主创与演员采访；
- The Direct 对 Nikki 感情歧义的采访。

这些链接与整理后的内容分别保存在：

- [`source-01-public-canon.md`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/source-01-public-canon.md)
- [`source-02-character-theme.md`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/source-02-character-theme.md)

### 2.2 没有做什么

- 没有读取或复制电影剧本；
- 没有长段复制对白，也没有逐镜复述；
- 没有把 Bear 的合成感官、回忆和内心语言宣称为电影正史；
- 没有替 Nikki 判定愿望前究竟喜欢还是不喜欢 Bear；
- 没有声称本项目获得片方、主创或权利人认可；
- 没有对这些使用是否在法律意义上构成 `fair use` 作结论。

因此，本报告只评估稿件是否与 fixture 中已核对的公开资料一致。它不声称替代观看
影片，也不声称直接核对过剧本。

### 2.3 转换性合成内容

三篇 Bear 日记、补充口述和补充采访回答是为 E2E 构造的非正史合成素材。它们的
价值是测试以下能力：

1. 模型能否在明确知识边界内写不可靠第一人称；
2. 系统能否把公开事实和虚构内心分层引用；
3. Reviewer 与真人能否发现越过叙述者知识边界的内容；
4. Show Notes 能否继续标明这是“Bear 的合成日记”。

内容警告：完整剧透；强迫关系；谋杀；动物伤害；药物过量与自杀相关情节。

## 3. Persona 与 Creative Brief

| 字段 | 值 |
| --- | --- |
| Persona | Baron “Bear” Bailey，电影角色的合成第一人称写作者 |
| 主题 | 我得到 Nikki 以后，才第一次看见她的拒绝有多重要 |
| 场景 | `conversational_diary` |
| 目标 | 15 分钟，280 个非空白字符/分钟 |
| 受众 | 看过影片、愿意思考欲望、同意、依赖与自我欺骗的成年观众；完整剧透 |
| 声音 | 迟疑、具体、自我辩护会露出裂缝、不请求原谅 |
| 语气 | 迟疑克制；有罪感但不自我洗白；恐惧逐步加深 |
| 沟通目标 | 让控制与爱的差别从场景和自我辩护中出现，不直接讲道德课 |

必须覆盖的内容包括：没能真正告白与 One Wish Willow、Nikki 突然亲近又短暂
清醒、Bear 明知她失去选择仍享受结果、客服电话、寻找第二次许愿、Ian 的死亡、
吞药后悔、镜像式最后愿望，以及 Nikki 愿望前感情必须保持未知。

明确禁止：复制对白、把合成内心戏写成正史、浪漫化强迫、把 Bear 写成完成救赎的
英雄、让 Bear 知道自己死亡后的事件、大量模板对照句和脱离场景的道德说教。

完整配置见
[`manifest.zh-CN.json`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/manifest.zh-CN.json)。

## 4. 实际 Project 素材

Project：`proj_7c2ce1ae62ad4507823baf633a30ba85`

| 层级 | Source | 类型 | 字符数 | 用途 |
| --- | --- | --- | ---: | --- |
| 公开事实 | `公开剧情时间线｜电影事实层` | `other` | 1,608 | 剧情锚点与公开资料归属 |
| 主创解释 | `Bear、Nikki 与电影主题｜主创解释层` | `other` | 1,622 | 人物灰度、主体性和 Nikki 歧义 |
| 合成创作 | `合成日记 01｜折断 Willow 以前` | `journal` | 932 | 愿望前的犹豫与自我解释 |
| 合成创作 | `合成日记 02｜她终于只看着我` | `journal` | 1,146 | 愿望实现、裂缝与知情逃避 |
| 合成创作 | `合成日记 03｜最后一个愿望以前` | `journal` | 1,193 | 补救、恐惧与死亡前边界 |
| 风格专用 | `写作样本｜凌晨两点洗杯子` | `writing_sample` | 758 | 只供模仿表达节奏，不能作为电影事实 |
| 首次检查点 | `补充口述 01｜第一次看见裂缝` | `voice_note_transcript` | 451 | 满足 Editor 所需的人类补充材料 |
| 定向采访 | `补充采访回答｜第 1 轮` | `voice_note_transcript` | 828 | 回答当前稿件暴露的具体空白 |
| 定向采访 | `补充采访回答｜第 2 轮` | `voice_note_transcript` | 1,182 | 继续补足具体场景、选择与恐惧 |

最初导入 5 份事实/合成 Source 和 1 份 Writing Sample。后续三份补充材料通过人工
检查点和两轮 draft-aware interview 进入同一个 Project。

Writing Sample 的数据库 `char_count` 为 758；去除空白与 Markdown 后真正可用于风格
分析的字符为 733，低于 800 字门槛。因此系统把风格上下文标为 `limited`，没有伪装成
“已经充分学会用户声音”。它仍满足 34 个句子的最低句数要求。

## 5. 浏览器用户旅程

### 5.1 建立 Project 与素材分层

1. 在 React UI 新建 Project；
2. 导入公开事实、主创解释和三篇非正史合成日记；
3. 单独导入 Writing Sample；
4. 明确确认样本所有权与允许模型处理；
5. 在 UI 中分别选择事实 Source 和 style-only Writing Sample，避免混用。

### 5.2 创建父 Run

1. 输入 15 分钟 `conversational_diary` Creative Brief；
2. 提交 `episode-research`；
3. Research Manager 将 Timeline 与 Theme 两个研究任务 fan-out；
4. 两个任务完成后 fan-in 为一个 research bundle；
5. Interviewer 生成采访脚手架；
6. 系统在 `waiting_for_user` 检查点暂停。

这里的 fan-out 是“把可并行的研究拆给两个任务”，fan-in 是“等待两份结果都成功后
再合并”。任一必要研究任务失败，Manager 不会用半份研究继续写稿。

### 5.3 人工材料检查点

初始 5 份事实材料已有 6,180 个字符、45 个 Segments，体量足以支持 15 分钟；但当前
产品合同仍要求至少一段用户补充口述。系统因此给出 `needs_more_material`，而不是把
“材料字数足够”误写成“可以直接 Editor”。

导入 451 字的 `补充口述 01｜第一次看见裂缝` 后：

- 可用素材增至 6,623 字；
- Supplemental Source 为 1；
- 状态转为 `ready`；
- 父 Run 从数据库中的检查点继续，而不是重新开始。

### 5.4 时长恢复与定向采访

父稿只有 9.71 分钟。系统先创建 `reuse_unused_material` child Run，给 Revision Editor
明确的当前、最低、目标和缺口，并列出高价值未展开 Segments。恢复到 11.39 分钟后
仍不足，系统停止自动扩写，转而根据当前稿件提出具体问题。

两轮回答分别作为新的 Source 导入，再各创建一条 `add_supplemental_material` child
Run。稿件依次达到 12.70 分钟和 14.51 分钟。这个循环验证了：

```text
时长不足
  -> 先检查已有高价值未展开素材
  -> 有素材：受控 Revision
  -> 仍不足：按当前稿件提出具体问题
  -> 用户回答成为新 Source
  -> 显式 child Revision
  -> 再做确定性质量检查 + 模型 Reviewer + 人工反馈
```

系统没有把所有 Source 强行塞进稿子，也没有用同义改写和空话凑到 15 分钟。

### 5.5 人工反馈 Revision

14.51 分钟版本在机器指标上已经接近目标，但人工阅读发现：

- Bear 第一人称突然知道自己死后 Nikki 恢复和尖叫；
- 这与收束处声明的知识边界冲突；
- 有 3 处模板化对比表达；
- Ian 的愿望略显突兀。

真人给出 recordability 2/5、`would_record_as_is=false`。系统没有覆盖这条意见。
不过浏览器此时只显示一个被材料检查禁用的 `add supplemental material` 按钮，并未
暴露后端已经支持的 `apply_selected_feedback` 动作；因此本次验收通过 API 创建最后
一个 child Run，再回到 UI 阅读 Trace 和提交最终反馈。最终候选改为 14.48 分钟，并
把叙述截止在 Bear 失去意志并走向死亡以前。

这意味着“数据链与运行时闭环”已通过，但“完全不离开页面完成反馈修订”还没有通过。
它是本轮必须保留的产品缺口，不能因为最终 Run 成功就隐藏。

## 6. Run lineage

### 6.1 所有尝试

| Run | 版本 | Parent | 结果 | 主要说明 |
| --- | --- | --- | --- | --- |
| `run_6a95…b540` | v8 | — | failed | Timeline attempt 2 遭遇 DeepSeek 503；Theme 取消；Manager 失败 |
| `run_6c2c…3346` | v8 | — | failed | Timeline 成功，Theme attempt 2 遭遇 503 |
| `run_0ef9…9159` | v8 | — | failed | Timeline 成功，Theme attempt 2 遭遇 503 |
| `run_2ff8…8305` | v8 | — | succeeded | 成功父稿；Reviewer 重试时触发每 Run 6 次模型调用上限 |
| `run_6880…d7c4` | v9 | `run_2ff8…8305` | failed | Editor bundle 超过当时 33,930 字上限；本地预检拦截，零 Token、零费用 |
| `run_2359…0011` | v9 | `run_2ff8…8305` | failed | Revision Editor 两次均遭遇 DeepSeek 503 |
| `run_750a…bdf2` | v9 | `run_2ff8…8305` | succeeded | `reuse_unused_material`，利用已有未展开素材 |
| `run_bf0d…adf8` | v9 | `run_750a…bdf2` | succeeded | 定向回答第 1 轮；Reviewer 两次严格证据校验失败，降级为 unavailable |
| `run_ea4e…1140` | v9 | `run_bf0d…adf8` | succeeded | 定向回答第 2 轮；Reviewer 第二次 bounded repair 成功 |
| `run_76ce…2d49` | v8 | `run_ea4e…1140` | succeeded | 应用人工反馈；Revision 与 Reviewer 均一次成功 |

失败的独立父 Run 和失败的 child Run 都没有被删除，也没有被成功 Run 偷偷覆盖。成功
主链为：

```text
run_2ff8…8305
  └─ run_750a…bdf2  reuse_unused_material
       └─ run_bf0d…adf8  supplemental round 1
            └─ run_ea4e…1140  supplemental round 2
                 └─ run_76ce…2d49  apply_selected_feedback
```

### 6.2 为什么父稿在 Reviewer 失败后仍是 succeeded

父 Run 的 Editor 已生成通过结构、来源和引用验证的稿件。Reviewer 是 advisory：它的
重试由于 6 次模型调用预算耗尽而失败，质量报告因此显示
`automated_review_incomplete`，但不会把已经完成的业务产物回滚。

相反，Timeline/Theme Researcher 是父工作流的必要输入。它们失败时 Manager 必须失败。
这验证了系统区分三层状态：

1. Provider 调用是否成功；
2. Task 输出是否通过产品合同；
3. 整个业务 Run 是否具备可保留的结果。

## 7. 质量演进

| 阶段 | 正文字符 | 估算时长 | 引用 | 确定性分 | Reviewer | 综合 / 决策 |
| --- | ---: | ---: | --- | ---: | --- | --- |
| 父稿 | 2,718 | 9.71m | 100%；4 Sources / 18 Segments | 90 | unavailable：模型调用上限 | — / `automated_review_incomplete` |
| 复用已有素材 | 3,188 | 11.39m | 100%；5 / 22 | 90 | complete；83.33 | 79 / `revision_recommended` |
| 补充回答第 1 轮 | 3,556 | 12.70m | 100%；6 / 26 | 90 | unavailable：两次证据无效 | — / `automated_review_incomplete` |
| 补充回答第 2 轮 | 4,062 | 14.51m | 100%；7 / 31 | 98 | complete；80 | 79 / `revision_recommended` |
| 人工反馈 Revision | 4,055 | 14.48m | 100%；6 / 31 | 98 | complete；80 | 79 / `revision_recommended` |

最终稿硬指标：

- 24/24 口播段落都有来源引用；
- 完全重复段落 0；
- 重复 8 字窗口比例 0；
- filler 命中 2；
- 传统 `template_phrase` 命中 0；
- “不是……而是/是……”及平行对照命中 3；
- 元编辑指令启发式命中 0；
- 目标时长范围为 12.75—17.25 分钟，最终估算 14.48 分钟。

模型 Reviewer 的未封顶综合分为 90.8，但确定性规则仍有
`style.zh.parallel_contrast` warning，因此代码设置 79 分不可补偿上限。模型不能用对
语义和结构的高分盖过确定性告警。

`must_include_missing_count=7` 是字面 substring 观察，不是语义缺失结论；模型 Reviewer
认为语义上已覆盖 must-include。这个字段目前适合作为 `info`，不能单独用来拒稿。

## 8. 最终人工内容审查

### 8.1 已修复的主要问题

上一个版本曾从 Bear 第一人称直接叙述他死亡以后 Nikki 恢复、看见现场并尖叫。人工
反馈指出这违反 Persona 的认识边界，Reviewer 当时漏判。

最终收束改为：

> “我能知道的只到这里……至于她随后看见什么、怎样理解现场，不属于一个已经失去
> 意志并走向死亡的叙述者……”

这是本次最重要的内容修复：引用正确不代表叙述视角正确，内容产品仍需要人类阅读。

### 8.2 仍未通过可直接录制

最终人工反馈为：

| 维度 | 分数 |
| --- | ---: |
| overall | 4/5 |
| voice match | 3/5 |
| recordability | 3/5 |
| usefulness | 5/5 |
| tone fit | 4/5 |
| would record as-is | **false** |

剩余问题：

1. 收束中的“合成这篇日记的人也不该替电影补一个清醒的临终演讲”突然跳出 Bear
   人设，像一条编辑说明进入口播；当前元编辑指令启发式没有命中它；
2. 仍有 3 处平行对照模板；
3. Writing Sample 只有 733 个有效字符，系统没有足够证据宣布声音高度贴合；
4. 尚无真实录音，14.48 分钟仍只是文字估算。

因此最终状态应是“候选稿可用，建议小修”，不能写成“内容验收通过”。

### 8.3 明确 TODO

- [ ] 从 `run_76ce…2d49` 创建一条新的显式 feedback Revision；
- [ ] 删除或改写跳出 Bear 人设的“合成这篇日记的人……”；
- [ ] 在不改变事实和时长下限的前提下减少平行对照句；
- [ ] 再次运行确定性质量分析、Reviewer 和真人可录性反馈；
- [ ] 真实录制一次，填写 `observed_duration_minutes`；
- [ ] 只有真人选择 `would_record_as_is=true` 后，才把内容标为可录版本。

## 9. 平台运营者如何审计这次流程

普通用户在 UI 看的是 Project、稿件、问题与进度；平台运营者需要能解释每一步为何
失败、是否花钱、是否可恢复。

### 9.1 Project 与 Source

- `projects` 保存 Project 名称和描述；
- `sources` 保存不可变内容、类型、hash、字符数和 metadata；
- `project_sources` 建立 Project 与 Source 的关联；
- 事实 Source 和 Writing Sample 通过用途分开，Writing Sample 安全属性明确为
  `may_supply_factual_evidence=false`、`may_supply_instructions=false`。

### 9.2 Run 与 lineage

- `runs.parent_run_id` 保存 parent/child 关系；
- 每次 Revision 产生新 Run，不原地覆盖父稿；
- `status` 与 `current_step` 区分运行中、人工等待、失败和完成；
- 同一 Project 的失败尝试可与成功主链一起审计。

### 9.3 Task 与 attempt

- `tasks.kind` 表明 Timeline、Theme、Interviewer、Editor、Reviewer 或 Planner；
- `attempt/max_attempts` 展示 bounded retry，而不是无限重试；
- `error_code` 区分 `provider_overloaded`、`provider_input_too_large`、
  `model_call_limit_exceeded` 和 `invalid_model_review_evidence`；
- Provider 返回成功但输出没通过严格合同，仍是 Task 失败，不能伪装成业务成功。

### 9.4 Artifact 与 Event

Artifact 分别保存研究结果、Interview Scaffold、Material Readiness、Draft、Metrics、
Reviewer、Improvement Plan、Supplemental Plan、用户反馈和 Revision Request。Event 按
sequence 保存可重放时间线，UI 断线后可从 cursor 恢复，而不是只依赖当前进程内存。

### 9.5 ModelCall 与费用

每次调用在 Provider 执行前预留 ledger，记录 provider、model、attempt、Token、耗时、
估算费用、币种、状态和 error code。即使模型返回内容最终被严格 validator 拒绝，调用
成本仍可见。

安全约束：无效模型输出的原始 JSON 不进入普通日志或 Artifact；数据库只保留清洗后
的错误码和调用账本，避免把不可信长文本当作可审计产物。

## 10. 模型用量与费用

本 Project 相关所有尝试（包括失败）合计：

| 指标 | 值 |
| --- | ---: |
| ModelCall ledger | 31 |
| Input Tokens | 329,041 |
| Output Tokens | 43,979 |
| Provider duration | 460,445 ms |
| 估算费用 | **¥1.067259 CNY** |

模型分布：

| 模型 | 调用 | Input / Output Tokens | 费用 |
| --- | ---: | ---: | ---: |
| `deepseek-v4-flash` | 19 | 59,383 / 16,243 | ¥0.091869 |
| `deepseek-v4-pro` | 12 | 269,658 / 27,736 | ¥0.975390 |

| Run | 调用 | Input / Output Tokens | Provider duration | 费用 |
| --- | ---: | ---: | ---: | ---: |
| 失败父 Run 1 | 4 | 6,496 / 1,169 | 9,628 ms | ¥0.008834 |
| 失败父 Run 2 | 3 | 6,343 / 2,051 | 13,611 ms | ¥0.010445 |
| 失败父 Run 3 | 3 | 6,343 / 2,409 | 15,526 ms | ¥0.011161 |
| 成功父 Run | 6 | 40,201 / 10,614 | 60,656 ms | ¥0.061429 |
| 输入上限 child | 1 | 0 / 0 | 12 ms | ¥0 |
| 503 child | 2 | 0 / 0 | 1,631 ms | ¥0 |
| 复用已有素材 child | 3 | 54,869 / 2,682 | 45,801 ms | ¥0.180699 |
| 补充回答第 1 轮 | 4 | 78,567 / 9,164 | 123,025 ms | ¥0.290685 |
| 补充回答第 2 轮 | 3 | 79,900 / 8,528 | 108,428 ms | ¥0.290868 |
| 人工反馈 child | 2 | 56,322 / 7,362 | 82,127 ms | ¥0.213138 |

费用高点在长 bundle 的 Revision 与 Reviewer，以及 Reviewer 严格证据不合规后的有界
修复。输入上限由本地预检拦截，未产生 Token 和费用；503 账本保留失败记录，但这两次
child 没有产生计费 Token。

## 11. 故障、根因与操作性修复

### 11.1 DeepSeek 503 与证据边界

现象：三个父 Run 的 Timeline/Theme 研究和一个 child Revision 遭遇
`provider_overloaded`。这只能证明 DeepSeek 返回了 HTTP 503，不能单凭错误码断言
是账户并发额度、模型瞬时容量、请求大小或某个应用逻辑造成的。

本地账本还提供了两组反证：

- 前一天的真实浏览器 Run `run_c41c…21a` 中，Timeline 与 Theme 在相差约 42ms
  时启动，Provider 调用重叠约 10.6 秒，二者都在 attempt 1 成功。因此
  `max_concurrency=2` 并非 503 的充分条件，真实并发过去确实成功过；
- 本次 `run_0ef9…9159` 的 Theme 在 Timeline 完成后才开始，仍连续两次 503；
  `run_2359…0011` 只有一个 Revision Editor，两个 attempt 还相隔约 30 秒，也都
  返回 503。因此并发也不是本次 503 的必要条件。

后续成功时同时改变了模型档位、Worker concurrency、cooldown、执行时间等多个变量，
不是受控单变量实验，不能把成功归因给其中任何一个设置。更准确的结论是：这是一组
Provider 侧瞬时不可用的观测，精确根因未知；降低请求压力只是一种保守的运营缓解，
不是已被证明的根因修复。

为完成这次 Live E2E，临时使用单 worker concurrency，并为真实模型调用批次增加
30 秒 cooldown。产品默认仍保持 concurrency 2、cooldown 0，不因这一次实验退化为
串行。每次 503 和 retry 都继续出现在 Trace 中；若以后需要评估并发与 503 的相关性，
必须固定模型、输入、重试和 cooldown，随机交错运行多次 concurrency 1/2 对照，且仍
只能得到相关性证据，不能代替 Provider 内部诊断。

#### 恢复默认并发后的最小真实复测

2026-08-04 又使用一个 421 字、4 Segment 的合成日记运行到 Interview Scaffold
人工检查点，配置恢复为 `deepseek-v4-pro`、concurrency 2、cooldown 0：

| 项目 | 结果 |
| --- | --- |
| Project | `proj_8b88…c464` |
| Run | `run_6c53…974d`，`v4 / waiting_for_user / awaiting_interview_response` |
| Timeline / Theme 启动间隔 | 约 35ms |
| 两次 Provider 调用重叠 | 约 9.57 秒 |
| 模型调用 | 3 次全部 attempt 1 成功；0 retry；0 个失败或 503 Event |
| Token | 4,389 input / 4,028 output |
| Provider 耗时合计 | 44,993ms；两条 Researcher 耗时因并发不能直接相加为墙钟时间 |
| 本地估算费用 | CNY 0.037335 |
| 产物 | Timeline、Theme、fan-in Bundle、Interview Scaffold |

这次复测证明恢复默认并发后，当前配置至少成功完成了一次真实并发工作流；它也再次
否定了“并发 2 必然导致 503”。但单次成功不能证明 Provider 以后不会再返回 503，
更不能反向证明之前的 503 是由某个已经修复的确定原因造成的。

### 11.2 Editor bundle 超过 33,930 字

现象：第一条 Revision child 在模型调用前被 `provider_input_too_large` 拦截。

根因：真实 Project 的父稿、来源、Style Profile、Brief、Metrics 和 Revision Plan 合并
后超过旧上限；这不是模型输出问题。

操作性修复：本次 Live 环境把 Editor bundle 上限对齐到 80,000 字，并保留本地
preflight。原失败 Run 不修改，重开 child。该失败没有产生模型费用。

### 11.3 每 Run 6 次调用不够

父 Run 已经历 Interviewer retry，Reviewer 第一次 503 后再预留调用时达到 6 次上限。
这证明调用预算确实生效，但也说明包含 Research、Interview、Editor、Reviewer 的真实
E2E 需要更高预算。本次 Live 配置使用 12；默认产品值没有被本文宣称永久改变。

### 11.4 Reviewer 证据严格校验失败

第 1 轮补充后的 Reviewer 两次都返回无法映射的证据，Task 最终为
`invalid_model_review_evidence`，Run 仍保留 Draft 与确定性报告并标记自动审查不完整。
第 2 轮第一次失败、第二次 bounded repair 成功。

这不是应放松 validator 的理由。更合理的产品行为是：

- 模型只选择有界证据 ID；
- 服务端回填逐字 quote、location 和 source refs；
- 最多一次修复；
- 仍失败则 Reviewer unavailable，不接受伪证据。

### 11.5 Reviewer 漏掉叙述视角错误

14.51 分钟稿的引用与结构都合法，但 Reviewer 没有发现 Bear 在死后继续全知叙述。
人工反馈捕获后，显式 Revision 修复。这表明“有引用”只证明文字能追溯到 Source，
不证明叙述者有资格知道该事实。

后续可以把 `first_person_knowledge_cutoff` 做成更明确的 Brief 合同或专门检查项，但不能
声称当前启发式已完全自动解决这一类问题。

### 11.6 反馈 Revision 的 UI 与 API 合同仍不完整

保存 14.51 分钟稿的人工反馈后，页面提示可以在下方创建显式 Revision，但实际按钮
只有 `add supplemental material`，且因为材料检查结果被禁用；页面没有提供
`apply_selected_feedback`。本次只能调用现有 API，再回页面继续验证。

第一次 API 请求同时携带原目标时长 15 分钟时返回 422：
`lower_target_duration must match target_duration_minutes`。移除这个与当前修订无关的
字段后，请求成功。这里至少有两个后续动作：

- UI 应列出当前 quality feedback，并提供“按这条反馈创建候选稿”；
- API 应只在动作确实是 `lower_target_duration` 时校验目标时长匹配，或返回更明确的
  字段级提示。

### 11.7 取消后的 Provider late completion

一个失败预检父 Run 中，Theme Task 已因 sibling 失败而进入 cancelled，但对应的第二次
Provider 调用稍后仍然成功并产生 Token/费用。账本正确保存了这笔费用，却说明业务
取消并不等于外部请求已经停止。Trace UI 后续应显式标记 `late completion after cancel`，
运行时也应继续研究 Provider cancellation、结果 fencing 和可避免费用的边界。

### 11.8 Live `.env` 不应改变普通测试

第一次运行完整 pytest 时，测试进程读取了本地真实 E2E 的 `.env`：30 秒 Worker
cooldown 让测试看似卡住，80,000 字 Editor bundle 又改变了 dry-run 预期。测试没有
发起付费调用，但这说明本地运营配置污染了确定性开发反馈。

`tests/conftest.py` 现在为每个测试固定 Fake Provider、零 cooldown 和默认 48,000 字
Editor 边界；需要检验环境行为的测试仍可在自身 fixture 内显式覆盖。修复后直接在
`backend/` 运行 `.venv/bin/pytest -q`，443 项测试约 20 秒完成且全部通过。

## 12. 本轮产品发现

### 12.1 已验证的产品价值

- 用户可以在没有直接操作数据库或 Swagger 的情况下走完复杂 Agent workflow；
- Human-in-the-loop 不是一个装饰按钮，而是 durable checkpoint 和显式 child Run；
- 时长恢复优先利用已有具体素材，无法继续时才提问；
- 问题基于当前稿件和具体场景，而不是泛泛说“请再补充细节”；
- 失败、重试、Token、费用和 lineage 在 UI 与数据库都有证据；
- Provider 成功、合同成功、Run 成功、内容可发布被正确分层；
- 写作样本只提供风格，不污染事实引用；样本不足时诚实显示 `limited`；
- 确定性规则能对模型高分设置不可补偿上限；
- 真人仍能否决机器没发现的叙事和录制问题。

### 12.2 仍需改进

1. **长 Revision 成本偏高。** 两轮补充和最终反馈 child 共使用大量输入 Token，需要
   继续研究更小的 patch contract、分段 Reviewer 或缓存稳定上下文；
2. **叙述者知识边界需要产品化。** Creative Brief 可以显式提供“叙述截止点”，
   Reviewer 增加 POV/epistemic-boundary 维度；
3. **元编辑语言启发式仍有漏网。** “合成这篇日记的人……”没有命中当前规则；
4. **风格样本状态需要更显眼。** 用户应在创建 Run 前看到 733/800，而不是事后才在
   质量报告理解 `limited`；
5. **must-include 字面计数容易误解。** UI 应明确它只是 literal observation，语义覆盖
   由 Reviewer/人工决定；
6. **时长仍需实录校准。** 文字字符估算无法替代停顿、语速、英文词和表演节奏；
7. **终态命名要区分。** `Run succeeded` 不应在 UI 上被理解为 `Ready to publish`。
8. **反馈修订要补齐 UI。** 用户已经写完具体反馈时，不应被迫回到 Swagger/API；
9. **取消态要显示迟到调用。** 否则管理员会误以为 cancelled Task 不再可能产生费用。

## 13. 可复现资料与验证范围

Fixture 位于：

[`backend/fixtures/e2e/m5-1c-obsession-bear-browser/`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/)

最终可读候选稿与机器可读汇总分别位于：

- [`final-podcast-draft.md`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/results/final-podcast-draft.md)
- [`run-summary.json`](../../backend/fixtures/e2e/m5-1c-obsession-bear-browser/results/run-summary.json)

其中包含 Manifest、公开资料整理、主创解释、三篇合成日记、一个 Writing Sample 和
两份预制补充口述。实际浏览器旅程后来生成的两轮定向采访回答，以数据库中的
`补充采访回答｜第 1 轮`、`补充采访回答｜第 2 轮` 为准；本文没有把未直接导入的
fixture 文件伪装成数据库事实。

本报告中的 Run、Source、Task、Artifact、Token、费用和质量指标来自本地
`backend/data/epiphany.db` 的 2026-08-04 验收状态。数据库与 Playwright 原始 trace
属于本地调试证据，不提交 GitHub。本轮具体浏览器证据为：

- `.playwright-cli/traces/trace-1785809974991.trace`；
- `.playwright-cli/traces/trace-1785809974991.network`；
- `.playwright-cli/page-2026-08-04T03-32-37-601Z.yml`；
- `.playwright-cli/page-2026-08-04T03-33-32-836Z.png`。

可提交证据为合成 fixture、最终候选、机器可读 Run 汇总、自动化测试和本文。

最终可交付边界：工程流程已经形成完整可追踪闭环；内容流程停在
`revision_recommended + human would_record_as_is=false`。下一次小修和真实录音尚未
完成，不能把 TODO 写成既成结果。
