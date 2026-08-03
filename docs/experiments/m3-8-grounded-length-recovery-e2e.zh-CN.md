# M3.8 实验报告：基于现有证据恢复口播时长

状态：**实验已完成**

日期：2026-07-31

## 1. 这次实验要回答什么

M3.7d 暴露了一个很具体的产品问题：

> 用户选择了 15 分钟，已有素材看起来不少，但 Editor 第一稿仍然只有六分钟左右。
> 系统应该立即要求用户补材料，还是先尝试使用尚未进入正文的现有事实？

最危险的修复是让模型反复“写长一点”。这样很容易得到同义反复、模板句、无来源
细节，或者一篇字数达标但用户不愿意录的稿子。

M3.8 因此只验证一条受限路径：

1. 时长只计算真正会说出口的 opening、正文 Paragraph 和 closing；
2. 普通代码盘点尚未被口播正文引用的事实片段；
3. 用户显式创建**一次** `reuse_unused_material` Revision 子 Run；
4. Revision Editor 收到当前、最低、目标、最高字符数和一组有界候选；
5. 新稿重新经过引用、时长、重复、中文风格规则和 Reviewer；
6. 如果仍然不足，系统停止，不自动发起第二轮模型调用，并给出补材料或降时长选项。

本报告分开记录两种结论：

- **工作流结论**：状态机、预算、引用、日志和停止规则是否可靠；
- **内容结论**：新稿是否真的达到 15 分钟目标、是否值得录制。

前者通过不代表后者通过。

## 2. 冻结实验输入

为了避免用几句临时 filler 测试，实验复用了 M3.7d 的完整虚构用户。所有人物、
经历、地址与写作内容均为合成数据，不包含真实个人隐私。

### 2.1 Persona

| 字段 | 冻结值 |
| --- | --- |
| 姓名 | 林澄 |
| 年龄 | 30 |
| 城市 | 上海 |
| 职业 | 内容运营 |
| 生活背景 | 工作与居住方式同时变化，习惯用日记和语音备忘录整理感受 |

她的表达习惯是从具体时间、物件或动作进入，先还原场景，再承认矛盾；长句后常用
短句收住，允许改口、括号和轻微自嘲，不把个人经验包装成普遍答案。她明确避免
整齐排比、连续对仗、密集“不是……而是……”、空泛鼓励和强行完成成长闭环。

### 2.2 Topic 与 Creative Brief

主题是：

> 退掉住了六年的出租屋后，我为什么在楼下坐了四十分钟

| Brief 字段 | 冻结值 |
| --- | --- |
| 目标时长 | 15 分钟 |
| 估算语速 | 280 个非空白字符/分钟 |
| 最低边界 | 3,570 个口播字符，即目标的 85% |
| 理想目标 | 4,200 个口播字符 |
| 最高边界 | 4,830 个口播字符，即目标的 115% |
| 场景 | reflective solo |
| 听众 | 25—35 岁，正在搬家、换城市、换工作或结束长期生活的听众 |
| 目标 | 通过具体场景解释“主动离开”与“情绪尚未同步”，提供陪伴而不是建议 |
| 语气 | 克制、具体、亲密，允许矛盾和轻微自嘲 |

Brief 明确要求出现退租交接、墙上浅色方块、透明钥匙袋、楼下四十分钟、主动选择
与情绪不同步，以及对过渡期听众的陪伴。它同时禁止“离开不是结束而是新的开始”
等套话、无场景升华和成功叙事。

### 2.3 事实 Source

冻结输入共有三份初始事实 Source 和一份补充口述：

1. 入住、六年时间线与退租当天；
2. 房间里的物件和生活痕迹；
3. 为什么离开，以及不想把这段经历讲成什么；
4. 按采访脚手架补充的长语音转写。

素材包含搬家公司在楼梯转角拆桌腿、空房回声、墙面方块、白瓷碗、褪色窗帘、
早餐店、馄饨配送地址、花坛边等车、朋友电话、导航仍指向旧地址等具体场景。

### 2.4 Writing Sample

四份写作样本只用于风格参考，不能作为事实引用：

1. 《凌晨一点，冰箱里只剩半颗柠檬》；
2. 《我妈把视频电话对准阳台上的葱》；
3. 《下雨天去修一把伞》；
4. 《语音备忘录：我为什么一直没回那条消息》。

前三份是书面随笔，第四份是口述转写。它们共同提供具体物件进入、轻微自嘲、
自我修正和不强行收束的风格证据。

冻结输入位于：

```text
backend/fixtures/e2e/m3-7-realistic-persona/
```

## 3. 实验工作流与调用边界

父 Run 最多五次模型调用：

```text
Timeline Researcher
Theme Researcher
Interviewer
Editor
Reviewer
```

用户显式提交一次 Revision 后，子 Run 最多两次：

```text
Revision Editor
Reviewer
```

所以完整实验上限是七次调用。读取 Improvement Plan、计算时长、选择候选、
计算质量指标、生成 Comparison 和再次读取子稿 Improvement Plan 都是普通代码，
不会偷偷调用模型。

本实验没有自动 Editor—Reviewer 循环，也没有隐藏 retry。子稿仍短时，E2E 只读取
下一步建议，不自动创建第二个 Revision。

## 4. Fake v8：验证工程刹车，而不是证明内容质量

最终确定性 Fake 运行保存于：

```text
backend/data/m3-8-length-recovery-fake-20260731-v8.db
backend/artifacts/m3-8-length-recovery-fake-20260731-v8/
```

结果如下：

| 指标 | 父稿 | 子稿 |
| --- | ---: | ---: |
| 口播字符 | 456 | 2,083 |
| 估算分钟 | 1.63 | 7.44 |
| 确定性分数 | 55 | 53 |
| 时长状态 | blocker | blocker |

额外证据：

- `workflow_passed=true`；
- `content_acceptance_passed=false`；
- 12/12 个优先事实引用在子稿中首次使用；
- 完全重复段落为 0；
- 七次 Fake 调用均为零 Token、零费用；
- 子稿命中 3 处编辑指令式短语，并触发
  `style.editorial_instruction_leakage` warning；
- 子稿计划返回 `add_supplemental_material`；`reuse_unused_material` 仍可查看
  但不再推荐，同时允许降低到 10 分钟；
- 没有自动排队下一次 Revision。

早期 fixture 曾出现 `456 → 3,623` 并跨过 3,570 下限，但那不是最终 v8 结果，
不能继续作为当前验收数字。最终 v8 的意义恰恰是：即使 Fake 使用了全部 12 个
候选，系统也不会为了让测试变绿而伪造已经达到 15 分钟；它会把工作流通过和
内容未通过分开报告，并在一次恢复后停止推荐连续扩写。

## 5. 第一次真实 DeepSeek：no-change 暴露了两个问题

第一次真实运行保存在：

```text
backend/data/m3-8-length-recovery-live-20260731-v1.db
backend/artifacts/m3-8-length-recovery-live-20260731-v1/
```

父 Run：

```text
run_9ca1cd1d391f4b0a807b15ea62a90920
```

Revision 子 Run：

```text
run_d9231ff1630947c6b3214e971e4746fb
```

父稿约 1,817 个口播字符，仍明显短于 3,570 下限。系统盘点出 22 个未引用片段，
原始字符合计 2,789；人工回看后，真正具体且值得展开的内容约 1,966 字符，和父稿
相加只比最低边界多约两百字符。这说明“原始候选字符足够”只能表示值得尝试一次，
不能承诺模型一定写到下限。

这次 Flash Revision 返回了与父稿相同的正文。Provider HTTP 调用本身成功并产生
费用，但应用层正确拒绝相同候选，子 Run 失败。底层真实错误是：

```text
podcast_revision_no_change
```

旧版 E2E harness 随后仍尝试读取不存在的成功 Artifact，最终把报告错误遮蔽为：

```text
artifact_not_unique
```

因此 v1 同时暴露了两个问题：

1. Revision Prompt 对长度缺口和必须增加的信息不够明确；
2. 失败 E2E 没有保留最接近根因的错误证据。

v1 实际费用证据：

| 指标 | 结果 |
| --- | ---: |
| 模型调用 | 6 |
| input tokens | 73,969 |
| output tokens | 15,842 |
| Provider 耗时 | 133,631 ms |
| 本地估算费用 | ¥0.147167 CNY |

六次调用是父 Run 五次加失败子 Run 的一次 Revision Editor；失败发生在 Reviewer
之前。失败不会退回已经产生的模型费用，这也是必须限制自动循环的现实原因。

## 6. v1 后做了哪些修复

没有通过无限增加 Prompt 或再调用几次模型“碰运气”，而是做了以下有界修复：

1. Revision Editor 收到父稿当前字符、最低、目标、最高和精确缺口；
2. Prompt 明确禁止原样返回、同义改写和无来源灌水；
3. 子稿必须增加此前未进入正文的事实引用，否则内容门槛失败；
4. 失败报告保留真正的 Run、Task、ModelCall 和错误代码，不再用缺失 Artifact
   错误遮蔽根因；
5. 完整 unused inventory 继续保留用于审计，但交给一次 Revision 的候选最多
   12 个，避免把几十个片段全部塞给模型；
6. 候选会过滤完全重复文本和已复制到口播正文的文本，再按缺失 must-include、
   Scaffold material gap、补充口述、数字细节、标点、长度和稳定顺序排序；
7. 子 Run 完成后重新读取 Improvement Plan；如果已经做过一次时长恢复，就不再
   推荐连续复用，而是推荐补材料或降低目标时长；
8. 质量检测新增编辑指令泄漏 warning；中文列举规则要求“最后”等词后存在列举
   标点，普通“最后一个空行李箱”不再误报；
9. 父子 warning 比较排除时长类 warning，避免“blocker 降级为 warning”被误判
   成内容质量恶化。读取这些结果不会创建新 Run。

`existing_material_sufficient` 仍作为兼容 wire value 保留，但它现在只表示：

> 被筛选候选的原始字符数量允许做一次受控尝试。

它不是对达到 15 分钟或内容质量的预测。

## 7. 第二次真实 DeepSeek：工作流通过，内容仍未通过

第二次真实运行只执行一次，没有在结果不理想后继续付费重跑：

```bash
python -m epiphany.length_recovery_e2e \
  --provider deepseek \
  --editor-model deepseek-v4-flash \
  --reviewer-model deepseek-v4-pro \
  --execute \
  --database data/m3-8-length-recovery-live-20260731-v2.db \
  --output-dir artifacts/m3-8-length-recovery-live-20260731-v2
```

本地证据位于：

```text
backend/data/m3-8-length-recovery-live-20260731-v2.db
backend/artifacts/m3-8-length-recovery-live-20260731-v2/
```

父 Run：

```text
run_56d5105db3754b4c8d15e476e7c1d153
```

Revision 子 Run：

```text
run_80b78a0113e8438389a5ff12769cfc4e
```

### 7.1 时长与质量指标

| 指标 | 父稿 | 子稿 | 变化 |
| --- | ---: | ---: | ---: |
| 口播字符 | 1,310 | 2,371 | +1,061 |
| 估算分钟 | 4.68 | 8.47 | +3.79 |
| 目标覆盖率 | 31.20% | 56.47% | +25.27 个百分点 |
| 确定性分数 | 61 | 88 | +27 |
| 实验综合分 | 39 | 59 | +20 |
| blocker | 1 | 0 | -1 |
| warning | 1 | 2 | +1 |
| 口播引用片段 | 11 | 19 | +8 |

运行结论：

```text
workflow_passed=true
content_acceptance_passed=false
```

原始 v2 Artifact 生成时的内容失败项是：

```text
content.child_reaches_duration_range
content.warning_count_not_higher
```

子稿仍只有 2,371 字符 / 8.47 分钟，低于 3,570 字符 / 12.75 分钟的最低边界。
这里不是“略微超过上限”，而是仍然明显偏短。

其中第二项暴露的是当时比较逻辑的问题，不是需要再付费重跑模型才能验证的内容
失败。当前代码已改为比较**非时长 warning**，并修正普通“最后”的列举误报；
持久化稿件中的编辑备注则会被新规则识别为
`style.editorial_instruction_leakage`。真实的时长失败保持不变。

### 7.2 信息增量与重复

Revision 收到 12 个优先引用：

- 6 个在子稿中首次使用；
- 6 个仍未使用；
- 子稿还使用了 2 个不在优先短名单中的其他允许事实片段；
- 系统没有要求把所有 Source 都塞进稿件。

子稿的确定性指标：

| 指标 | 结果 |
| --- | ---: |
| 段落引用覆盖 | 100% |
| 完全重复段落 | 0 |
| 重复八字符窗口比例 | 0.0036 |
| filler 密度 | 1.27 / 1,000 字符 |
| 模板短语密度 | 0 |
| “不是……而是……”密度 | 0 |
| 中文风格模式密度 | 1.6871 / 1,000 字符 |

因此，子稿确实增加了有来源的新内容，没有靠明显的复制段落、filler 或模板转折
把字数撑起来。这是一次有效的信息恢复，但还不足以满足 15 分钟 Brief。

### 7.3 为什么旧报告里的 warning 数反而从 1 变 2

不能只看到 warning 总数就判断新稿“整体更差”。两版 warning 的构成不同：

父稿：

- `style.paragraph_length_cv`：段落长度过于整齐；
- 时长是更严重的 `duration.severe_deviation` blocker，不计入 warning 数。

子稿：

- 时长从 blocker 降为 `duration.outside_target_range` warning；
- 段落长度变化通过；
- `style.zh.enumeration` 从 2 次变成 3 次，刚好达到 warning 阈值。

所以旧报告中 warning 从 1 增加到 2，一部分来自严重时长 blocker 被降级为
warning，另一部分来自把“最后一个空行李箱”误判为列举式表达。直接比较 warning
总数会把两类问题混在一起。

当前实现只比较非时长 warning，并要求“首先、其次、最后”等词后出现冒号或逗号
等列举标记才计数。修正后不会因为 blocker 降级或普通“最后”误报而拒绝一篇有
信息增量的稿件；但真正的编辑备注泄漏仍会产生 warning。

### 7.4 Reviewer 证据

Flash 负责 Editor，Pro 负责 Reviewer。它们仍属于同一家族，不是完全独立裁判。

| Reviewer 维度 | 父稿 | 子稿 |
| --- | ---: | ---: |
| Brief adherence | 2 | 3 |
| Source faithfulness | 5 | 5 |
| Coverage and specificity | 5 | 5 |
| Structure and coherence | 5 | 5 |
| Oral naturalness and voice fit | 5 | 5 |
| Conciseness and non-redundancy | 4 | 4 |
| Personal style match | 4 | 4 |

Reviewer 认为两稿的来源忠实、具体性和结构较好，但子稿对 15 分钟 Brief 的遵从
仍只有 3/5。代码拥有时长硬事实，因此模型的高分不能把短稿标成可发布。

### 7.5 Token、耗时和费用

| 范围 | 调用数 | input tokens | output tokens | Provider 耗时 | 本地估算费用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 父 Run | 5 | 45,201 | 11,068 | 104,126 ms | ¥0.103843 |
| Revision 子 Run | 2 | 41,296 | 6,428 | 55,102 ms | ¥0.097310 |
| 合计 | 7 | 86,497 | 17,496 | 159,228 ms | ¥0.201153 |

Revision Editor 单次输出 4,228 tokens 不等于 4,228 个口播字符。结构化 JSON、
标题、引用、Show Notes 和其他协议字段都会占用输出 Token；时长只计算最终真正
说出口的 2,371 个非空白字符。

费用是根据本地价格配置得到的 CNY 估算，不是 DeepSeek 官方账单。不同币种仍需
分组保存，不能直接相加。

## 8. 父子稿的定性审阅

### 8.1 父稿

父稿的优点是克制、聚焦并有生活细节。它使用了花坛水泥边、浅色方块、白瓷碗、
泡泡机、麻雀和裤子灰等场景，已经有可以直接口播的基本节奏。收束也没有强行说
“学会告别”。

它的问题不是明显重复，而是选择过少。住了六年的时间线、钥匙交接、朋友电话、
新旧地址和适应过程大量留在 Source 中，导致稿子更像一篇五分钟短稿，不能完成
15 分钟 Brief。

### 8.2 子稿

子稿增加了几个真正有信息的新场景：

- 搬空以后重新看见天花板水渍；
- 馄饨店不配送新地址，损失一张三元优惠券；
- 把搬家当成有勾选框的项目；
- 第三十分钟给朋友打电话；
- 旧房也有疫情焦虑、失恋和噪音，不把过去滤镜化；
- 新通勤只用三十二分钟；
- 导航仍把“家”指向旧地址；
- 通过垃圾位置、夜里去卫生间和杯子位置逐渐适应新家。

这些内容来自明确 Source，确实让主题从“退租当天”延伸到“身体怎样重新建立
坐标”。与父稿相比，它更完整，也没有明显车轱辘话。

但它还不是 15 分钟可录终稿：

1. 仍缺至少 1,199 个字符才到最低边界；
2. 透明钥匙袋等 Brief 要求没有得到充分展开；
3. 第四节出现较多并列解释，触发 `style.zh.enumeration`；
4. “这句话有点抽象，如果要用，前面一定要……”原本是素材中的编辑备注，却被
   带进正文，听起来像作者在现场评论写作过程；
5. 新增段落提高了覆盖，但整体仍有“把未使用片段依次接上”的感觉，需要真人再
   判断节奏和隐私。

定性结论与机器指标一致：Revision 是实质改进，但不是可以宣称达到 15 分钟的
成品。

## 9. Post-Revision 下一步是否合理

真实 v2 当时使用旧版 Improvement Plan schema，原始 Artifact 曾再次返回
`reuse_unused_material`。这暴露了最后一个停止条件：已有时长恢复子 Run 不能
只因为数据库里还剩若干 Segment，就继续推荐同一种扩写。

当前 Improvement Plan v2 会持久化
`prior_length_recovery_attempted=true`。同样的子稿在当前规则下会得到：

- `duration_resolution=add_supplemental_material`；
- `reuse_unused_material` 保留为可审计选项，但 `recommended=false`；
- `add_supplemental_material=true`，并提供有 Source 锚点的具体问题；
- `lower_target_duration=true`，建议降到更接近现有内容的预设时长；
- 不创建新 Run，也不产生第八次模型调用。

这项停止逻辑已由 Fake v8 持久化验证。没有为了更新真实报告再发起付费调用：
真实 v2 的父子稿、Token 和费用证据保持原样，确定性规则由本地测试覆盖。

## 10. `material_kind` 是当前最重要的素材边界

当前 `ResearchSourceSegment` 没有结构化的 `material_kind`，无法明确区分：

- 已发生的事实和场景；
- 用户的反思；
- 对 Editor 的写作指令；
- 隐私边界或“不应写入正文”的说明。

M3.8 因此刻意没有在生成前使用“不要、结尾、正文、如果要用”等中文关键词过滤。
这种规则很容易误删真实口述。当前可安全完成的包括：

- 去除完全相同的片段；
- 去除已原样复制到口播正文的长文本；
- 使用 must-include、material gap、补充口述、数字、标点和长度做可追踪排序；
- 最多向 Revision 提供 12 个候选；
- 在 Revision 后检测一组明显的编辑指令式短语，并继续交给真人审稿。

子稿把“这句话有点抽象，如果要用……”带入口播，正是这个限制的真实证据。
当前规则会把它标成 `style.editorial_instruction_leakage`，但不会假装仅靠关键词
就能正确分类所有素材。
未来可以在 Source 导入或切段时增加受验证的 `material_kind`，例如
`factual_scene`、`reflection`、`editorial_instruction`、`privacy_boundary`。
在契约升级以前，文档不能声称系统已经能可靠排除写作指令。

## 11. 日志、数据库与安全检查

本轮使用独立 SQLite 数据库和 Artifact 目录，均位于 `.gitignore` 覆盖路径，
不会随代码提交：

```text
backend/data/m3-8-length-recovery-*.db
backend/artifacts/m3-8-length-recovery-*/
```

验收确认：

- 父 Run 保持不可变；
- 子 Run 使用独立 Task、Artifact、Event、ModelCall 和预算；
- 同一提交可以幂等重放，不重复产生费用；
- 总调用数没有超过 5 + 2；
- 每次调用保存 provider、model、状态、Token、耗时、估算费用和币种；
- 日志保存 Run/Task/Call ID、状态和计数，不打印 API Key；
- 日志不输出完整 Prompt、Source、Writing Sample 或生成稿；
- Writing Sample 没有被当作事实引用；
- 所有子稿引用都能解析到允许的事实 Source。

最终代码验证不沿用早期快照中的 `334 passed`。本报告收口时实际完成：

```text
341 backend tests passed
Ruff lint passed
102-file Ruff format check passed
git diff --check passed
Fake v8 workflow_passed=true / content_acceptance_passed=false
```

## 12. 最终结论

### 机制结论：通过

M3.8 已经证明系统能够：

- 用 spoken-only 规则计算时长；
- 先寻找现有未使用事实，而不是立即让用户重复提供材料；
- 把精确缺口和有界候选交给一次显式 Revision；
- 拒绝原样返回；
- 检查新事实、引用、重复、filler、中文风格和 Reviewer；
- 检测明显的编辑指令泄漏，并避免普通“最后”的列举误报；
- 在模型仍写不够时诚实失败；
- 一次恢复后停止推荐连续复用，不自动循环、不隐藏费用；
- 返回有锚点的补材料问题或降低目标时长建议。

### 内容结论：15 分钟验收未通过

真实 v2 从 4.68 分钟提升到 8.47 分钟，并增加了有来源的新场景，但仍低于
12.75 分钟最低边界。它是一篇更好的候选稿，不是一篇达到 15 分钟要求的终稿。

这不是系统“做坏了”，而是质量刹车发挥作用。若必须做成 15 分钟，合理的下一步
是由用户选择并回答若干具体问题，再创建新的显式 Revision；如果不想继续补充，
则把目标改成 10 分钟。系统不会继续拿同一批素材生成第二次自动扩写，也不应用
空话补齐剩余字符。

### 尚未完成

- 真人对 voice match、recordability、usefulness 和隐私边界的反馈；
- 实际录音时长与 280 字符/分钟估算的校准；
- 结构化 `material_kind`；
- 可视化展示父子 Run、质量指标、引用和下一步选项。

M3.8 至此应收口。下一阶段进入 M4 的可回放 Trace，随后进入 M5.1 最小
Run Trace UI。
真人可录性反馈可以在 UI 出现后补充，但不应继续用自动 Revision 扩大 M3。
