# M5.1b 真实浏览器 E2E 合成素材

这组 fixture 用于复现一次接近真实用户行为的中文播客生产流程：用户先创建
Project，导入多份事实素材和一份只用于模仿表达风格的 Writing Sample，再通过
浏览器创建 15 分钟口播 Run、分两次补充口述、执行一次有证据的时长恢复，并回答
候选稿生成后的四个针对性问题。目录还包含一条独立的“潜水入门”Source Starter
旅程，用于验证零素材用户如何在 AI 帮助下写出第一份可核对的 Source。

## 安全边界

- 所有人物、日期、地点和经历均为测试而合成，不对应真实用户；
- 文件不包含 API Key、Prompt、模型原始响应、本地数据库或日志；
- `writing-sample.md` 只能提供句长、节奏和措辞等风格信号，不能作为事实来源；
- 其余文件是事实候选素材，但模型生成的每个事实仍需保留 Source Segment 引用；
- 这些 Markdown 文件是可提交的测试输入，真实用户日记与录音不得照此提交到仓库。

本轮 Playwright 输入 supplemental 时，数据库保存了可见的 `\n\n` 字符串，而不是
真正的换行符。fixture 为便于阅读已把它们规范化成空行，文字内容没有改写。因此这些
文件用于后续正确输入的语义复现，不用于复算本轮数据库的 SHA-256；该输入格式问题也
保留在实验记录中。

## 文件说明

| 文件 | UI 中的类型 | 用途 |
| --- | --- | --- |
| `source-a-timeline.md` | 日记 / 随想 | 搬家后三个月的事件时间线 |
| `source-b-reflection.md` | 日记 / 随想 | 对独处、照顾自己和浅关系的反思 |
| `source-c-voice-note.md` | 口述转写 | 焦掉的番茄炒蛋场景 |
| `writing-sample.md` | Writing Sample | 只提供个人声音与写作节奏 |
| `supplemental-01-neighbor.md` | 口述转写 | 第一次材料不足后的邻居与购物清单补充 |
| `supplemental-02-dinner-details.md` | 口述转写 | 第二次材料不足后的长篇场景补充 |
| `improvement-answers.md` | 口述转写 | 旧实验中的两道回答，作为历史 fixture 保留 |
| `targeted-interview-answers-round-1.md` | 口述转写 | 本轮真实 UI 提交的 q1/q2/q4/q6 四组回答 |
| `source-starter-scenario.zh-CN.json` | — | 潜水 Source Starter 的 Project、意图与安全预期 |
| `source-starter-confirmed-user-edit.md` | 日记 / 随想 | AI 候选经合成用户核对、改写和确认后的 516 字 Source |

## 推荐复现方式

1. 新建 Project，使用 `manifest.zh-CN.json` 中的标题、简介与 Creative Brief。
2. 先导入 Source A/B/C；把 Writing Sample 单独标记为 `style_only` 并确认授权。
3. 创建 15 分钟 `episode-research` Run，在两次人工检查点依次导入 supplemental。
4. 初稿仍不足时，创建一次 `reuse_unused_material` 时长恢复 Revision。模型只返回
   新增口播单元 patch；服务端与不可变父稿合并后执行完整校验。
5. 恢复稿仍不足时，回答 q1/q2/q4/q6，并导入
   `targeted-interview-answers-round-1.md` 创建下一条显式 child Revision。
6. 验证最终正文至少达到 15 分钟目标的 85% 下限（12.75 分钟），同时保持
   100% 引用、无虚构和低重复；达到时长不代表内容已经可发布。
7. 观察 Run trace、Task、Model Call、Artifact、质量报告与人民币费用记录。

Source Starter 独立复现：

1. 按 `source-starter-scenario.zh-CN.json` 新建一个 0 Source Project；
2. 分别运行 `exploration_outline` 与 `starter_draft`；
3. 确认候选稿不会自动成为 Source，刷新后 mode 与 intent 仍能恢复；
4. 把候选稿编辑成 `source-starter-confirmed-user-edit.md`，确认事实后再导入；
5. 验证新 Source 标记为 `origin=ai_assisted`、`user_confirmed=true`。

真实运行的阶段性结果、Run ID 和已知失败见
[`docs/experiments/m5-1b-real-browser-e2e.zh-CN.md`](../../../../docs/experiments/m5-1b-real-browser-e2e.zh-CN.md)。
