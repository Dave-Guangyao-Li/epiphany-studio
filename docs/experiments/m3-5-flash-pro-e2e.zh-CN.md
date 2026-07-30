# M3.5 实验：真实 Workflow 与 Flash / Pro 冻结稿 Reviewer 对照

日期：2026-07-30

状态：完成一条真实 Workflow、两轮 Reviewer 对照与当前规则 Fake 回归

## 1. 实验问题

本实验回答四个问题：

1. 一份包含完整 audience、scenario、tone、目标时长、初始 Source 和补充口述的
   合成素材，能否跑通 M3.5 预发布开发快照的真实 Workflow？
2. 当真实模型生成稿只达到目标时长约一半，系统是否仍会被模型高分误导？
3. 在完全相同的冻结 Draft 上，DeepSeek V4 Flash 与 Pro 的 Reviewer 结果是否
   一样？
4. 中文启发式修复后，历史 Run 能否在不修改旧 Artifact 的前提下按当前规则
   重新实验？

## 2. 假设

- Pro 可能更细致，但不预设它一定更严格；
- 同一家族不同档位不能视为独立第三方裁判；
- 模型原始评分会波动，代码 cap 和 decision 应保持稳定；
- 真实内容即使语义覆盖 `must_include`，字面字符串规则仍可能误报；
- 比较必须冻结同一输入，不能重新调用 Editor。

## 3. 合成 fixture

文件：

```text
backend/fixtures/e2e/m3-5-chinese-quality-calibration.zh-CN.json
```

主题：

> 第一次真正独居：自由、孤独与自我照顾

Creative Brief：

| 字段 | 值 |
| --- | --- |
| 目标时长 | 15 分钟 |
| 估算速度 | 280 字符 / 分钟 |
| 场景 | reflective_solo |
| audience | 23–32 岁，第一次独居或换城市、对自由与孤独感到矛盾的人 |
| goal | 用三个生活场景讲清楚认知变化，不提供成功模板 |
| tone | 自然口语、具体、克制而允许自嘲 |
| must_include | 热水器、凌晨发烧、周日切菜晾衣、自由变成自我照顾 |
| avoid_patterns | 机械列举、空泛升华、模板结尾和无场景结论 |

素材量：

| 阶段 | Source | 原始字符 | Readiness |
| --- | ---: | ---: | --- |
| 初始 | 4 | 2,867 | needs_more_material |
| 补充口述 | 1 | 3,519 | — |
| 合计 | 5 | 6,346 | ready |

所有人名、日期和事件均为合成测试数据，`contains_personal_data=false`。

## 4. 第一次 Fake 失败为什么保留

最初补充材料较短，Run 在 Resume 后仍只有 3,056 个 grounded 可用字符，
距离门槛还差 514。这个失败说明 Readiness 使用的是实际进入 Scaffold /
Source 链路的素材，不是看 fixture 文件“显得很长”就放行。

随后把同一主题的补充口述扩成 3,519 字、21 个具体片段；没有降低门槛，也没有
伪造 ready 状态。

## 5. 真实 DeepSeek Workflow（预发布 v6 快照）

这次付费实验发生在 M3.5 合同最终定版以前，因此持久化的
`workflow_version` 是 `v6`。M3.5 最终把新 Run 升级到 `v7`，原因是
Reviewer Task 输入、Prompt、确定性规则和 Quality Report 的持久化语义已经
变化。实验数字不会被事后改写成 v7，也没有必要仅为了版本号再次产生模型费用。

命令：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

python -m epiphany.draft_quality_e2e \
  --provider deepseek \
  --execute \
  --editor-model deepseek-v4-flash \
  --reviewer-model deepseek-v4-pro \
  --fixture fixtures/e2e/m3-5-chinese-quality-calibration.zh-CN.json \
  --database data/m3-5-quality-calibration-deepseek.db \
  --output-dir artifacts/m3-5-quality-calibration-deepseek
```

结果：

```text
run_id: run_0af27a7596474a92ba79e298e912e35e
status: succeeded / complete
Tasks: 6 succeeded
Workflow Artifacts: 11
ModelCalls: 5 succeeded
input tokens: 34,701
output tokens: 12,174
Provider duration: 90,377 ms
local estimated cost: CNY 0.089433
```

五次调用分别是 Timeline Researcher、Theme Researcher、Interviewer、Flash
Editor 和 Pro Reviewer。系统确实经历：

```text
初始研究
  -> Interview Scaffold
  -> awaiting_more_material
  -> App restart
  -> 补充 Source
  -> Resume
  -> Flash Editor
  -> App restart
  -> Pro Reviewer
  -> Quality Report
  -> synthetic feedback
```

## 6. 真实 Draft 与当前规则复核

Draft 的 spoken-text 指标：

| 指标 | 结果 |
| --- | ---: |
| 正文字符 | 2,055 |
| 目标 / 估算分钟 | 15 / 7.34 |
| 时长覆盖 | 48.9% |
| 段落引用覆盖 | 100% |
| 来源 | 5 Sources / 15 Segments |
| 完全重复段落 | 0 |
| 8 字窗口重复率 | 0.0061 |
| parallel contrast | 4 |

开发中的旧规则快照为 43 分、1 blocker、3 warnings。人工内容复核发现四个
`must_include` 场景已经以同义改写出现；旧代码却按逐字缺失扣 16 分，而且
`not_but` 与 `parallel_contrast` 对相同句式重复扣分。

当前规则对**同一持久 Draft**离线重算：

```text
deterministic score: 62
blocker: 1
warning: 1
info: 2
```

变化来源：

- `must_include` 逐字未命中改为 info；
- legacy `not_but` 改为 info；
- canonical `parallel_contrast` 保留 warning；
- 时长严重不足仍是 blocker。

最终 cap 和 decision 没有被“修高”：

```text
code-owned cap: 39
decision: blocked
```

## 7. 旧 E2E harness 的误报

真实 Workflow 已成功，但当时的 E2E 检查器有两个旧假设：

- 五个 ModelCall 都应使用 Editor 模型；
- Reviewer relation 只能是 `same_model`。

因此原 `report.json` 的
`model_calls_match_provider` 和 `quality_report_contract_valid` 为 false。
这是测试器误报，不是产品 Run 失败。

修复为按 Task 路由验证 Flash Editor / Pro Reviewer 后，对原数据库离线复核：

```json
{
  "model_calls_match_routed_providers": true,
  "quality_report_contract_valid": true,
  "expected_reviewer_relation": "cross_tier_same_family"
}
```

离线复核不发模型请求，也不改写旧 `report.json`。

## 8. 冻结输入协议

比较工具固定：

- `podcast_draft`；
- `creative_brief`；
- `quality_config`；
- cited Source Segments；
- deterministic facts；
- Prompt version / hash。

它记录三个不同 hash：

- frozen Reviewer task input；
- deterministic result；
- entire comparison bundle。

默认 dry-run，不联网。`--recompute-current-rules` 会从旧 Run 的持久 Draft 派生
新的实验快照，但不修改旧 Metrics Artifact。`--execute` 才调用两个模型。
已有输出文件默认拒绝覆盖。

比较工具直接调用 Provider，不进入 Workflow 的 `model_calls` 表，也没有
durable retry。其 Token、耗时和费用只保存在本地 comparison JSON；这是实验
账本，不是生产账本。若 Provider 已返回计费用量、但严格 Schema / evidence
随后失败，失败行仍保存 Token、币种与估算费用；模型正文和无效原始输出不会
写入实验结果。

## 9. 对照 A：开发中旧 Metrics 快照

共同输入：

```text
frozen input sha256:
ec388b01716c832641cc15207984191e8dccc4723ba2a9b242a8a65e9546d40e

prompt sha256:
d794a7240aa1f89f484875ff3d61b00a2e5f93ab0c188414b82b844d07c0929f
```

结果：

| 指标 | Flash | Pro |
| --- | ---: | ---: |
| brief adherence | 2 | 2 |
| source faithfulness | 5 | 5 |
| coverage / specificity | 4 | 4 |
| structure | 4 | 4 |
| oral naturalness | 4 | 5 |
| conciseness | 4 | 4 |
| 模型局部维度平均 | 76.67 | 80.00 |
| 未封顶实验分 | 56.47 | 57.80 |
| cap / 最终分 | 39 / 39 | 39 / 39 |
| decision | blocked | blocked |
| input / output tokens | 9,990 / 2,128 | 9,990 / 2,141 |
| duration | 13,453 ms | 28,297 ms |
| local estimated CNY | 0.014246 | 0.042816 |

结论：

- Pro 没有更严格，反而在口播自然度多给 1 分；
- Pro 总模型分比 Flash 高 3.33；
- Pro 约三倍价格、两倍耗时；
- 最终分差为 0，证明 cap 没有被 Reviewer 档位绕过。

这轮只用于记录旧快照，不代表当前确定性规则。

## 10. 对照 B：当前规则重算快照

dry-run 标识：

```text
deterministic_origin: recomputed_current_rules
frozen input sha256:
342a691f6e1a914b38cd842985b682883ae3786e725a944052d6338a3712e217

prompt sha256:
5271214140d4bcf92cd347be361bc7227d24b755e1fc95009000fa1513795532
```

确定性快照：

```text
score: 62
blocker: 1
warning: 1
target / estimated: 15 / 7.34 minutes
```

结果：

| 指标 | Flash | Pro |
| --- | ---: | ---: |
| Provider HTTP / JSON 返回 | 成功 | 成功 |
| 严格 Reviewer Schema | 失败 | 通过 |
| 六维评分 | 不可用 | 2 / 5 / 3 / 4 / 4 / 3 |
| 模型局部维度平均 | 不可用 | 70.00 |
| 未封顶实验分 | 不可用 | 65.20 |
| cap / 最终分 | 不可用 | 39 / 39 |
| decision | 不可用 | blocked |
| input / output tokens | 9,990 / 1,980 | 9,990 / 2,657 |
| duration | 12,422 ms | 32,519 ms |
| local estimated CNY | 0.013950 | 0.044008 |

Flash 的网络请求和模型生成都成功，但返回对象没有通过严格
`ModelSelfReviewOutput` 合同，所以实验没有把它的非合规文本硬转成分数。
这属于产品必须能观察到的 Reviewer 可靠性问题。

## 11. 正式 v7 的当前规则 Fake 全流程回归

```text
run_id: run_f41eac8520cd4b47b97cc1181acb3d63
workflow_version: v7
provider/model: fake / fake-v1
passed: true
Tasks: 6
ModelCalls: 5
estimated cost: 0
hard blockers: 1
warnings: 1
model conflicts: 1
```

该 Run 验证最终 v7 合同下的暂停、两次进程重启、补充、Resume、Editor、
可信 Metrics、Reviewer、cap、Markdown、feedback 幂等和日志。它还与自动化
测试中的 legacy v6 恢复用例共同证明：新 Run 使用 v7，升级前已经持久化的
v6 Task 仍按旧合同完成。Fake 内容短导致 blocked 是预期产品结果，不影响
工程 E2E 通过。

## 12. 结论

1. 目标 audience、scenario 和 tone 已经完整存在，仍不能防止模型 Reviewer
   对其他局部维度给高分；缺少 Brief 不是主要原因。
2. 换 Pro 不能解决安全问题。该样本中 Pro 更贵、更慢，首轮还更宽松。
3. 代码确定性事实、严格 Schema、非补偿 cap 和人工反馈才是可靠边界。
4. `must_include` 与抽象 avoid 要求不能被简单字符串匹配冒充语义判断。
5. 单个 fixture 不能得出模型排名；需要积累至少 6–10 个冻结主题，并记录真实
   用户的 voice match / recordability / observed duration。
6. 用户反馈下一步应进入显式 Revision Run，而不是让模型自动循环追分。

## 13. 后续实验计划

- 独居 / 关系 / 职业 / 城市 / 旅行 / 知识解释等不同主题；
- 10 / 15 / 30 分钟各至少两个样本；
- 同一 Draft 的重复 Reviewer 稳定性；
- 真实录制分钟数对 280 字符 / 分钟的校准；
- 用户声线样本对 voice fit 的帮助；
- Flash、Pro 与不同家族 Reviewer 的对照；
- 反馈驱动 Revision 前后，而不是单看绝对分。
