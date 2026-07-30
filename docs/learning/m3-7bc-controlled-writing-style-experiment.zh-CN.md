# M3.7b/c：受控写作样本 A/B 与匿名盲评

状态：实现与真实单组实验完成，等待真人盲评后揭盲

日期：2026-07-30

## 1. 这一步要回答什么

M3.6 已经允许用户选择自己以前写过的文章或日记，供 Editor 参考表达习惯。
但“接入了 Sample”不等于“生成结果真的更像本人”。

M3.7 用一个很小的受控实验回答：

> 在素材、主题、受众、Creative Brief、采访结果、模型和质量规则都相同的
> 情况下，仅让一份 Draft 的 Editor 看到写作 Sample，它是否更像用户，也
> 更愿意被用户亲自录出来？

这是一次个人产品验证，不是模型排行榜，也不是统计学意义上的普遍结论。

## 2. 为什么需要两稿、两次 Reviewer

可以把实验想成试喝两杯咖啡：

- 两杯使用同一批豆子、同一台机器；
- A 杯不采用用户 Sample；
- B 杯采用用户 Sample；
- 最后先遮住标签试喝，再揭晓配方。

对应到系统：

1. 同一个 Flash Editor 生成 `without_sample`；
2. 同一个 Flash Editor 生成 `with_sample`；
3. 同一个 Pro Reviewer 评价第一稿；
4. 同一个 Pro Reviewer 评价第二稿。

两个 Reviewer 都能看到同一份 ready Sample。否则第一份稿子的
`personal_style_match` 没有参照物，两边的分数不可比较。

“2 次 Editor + 2 次 Reviewer”不是四个 Agent 长期协作，而是一次实验最多
四次、失败即停止的模型调用协议。

## 3. 为什么还要匿名盲评

如果用户提前知道哪一稿用了自己的 Sample，容易产生心理暗示：

> 既然它参考了我的文章，那它应该更像我。

因此 M3.7c 把两稿随机映射成 Candidate A / B。揭盲前只展示口播正文，不展示：

- `with_sample` / `without_sample`；
- Reviewer 分数；
- 模型名称；
- 内部 Source / Segment ID；
- 私有映射。

用户先给 A/B 分别填写：

- `voice_match_rating`：像不像本人，1—5；
- `recordability_rating`：愿不愿意开麦录，1—5；
- 可选评论；
- 必填的“哪稿更像我”二选一；
- 可选的选择理由。

保存真人判断以后，系统才允许揭盲。模型评价只是辅助，真人判断是主要证据。

## 4. 交付了什么

### M3.7b 执行器

`backend/src/epiphany/writing_style_ab_execute.py`

- 读取 M3.7a 冻结的 v8 Run；
- 在付费前重新计算实验合同 hash；
- 默认随机化 Editor 和 Reviewer 的 arm 顺序，并把实际顺序写入 manifest；
- 严格限制为两次 Editor、两次 Reviewer，不自动 retry；
- 每个 Editor 输出继续经过来源引用与 Sample 泄漏校验；
- 两份 Draft 都执行确定性质量分析；
- 两个 Reviewer 使用同一 Pro 模型和同一 ready Sample；
- 成功生成两份 Draft、两份质量结果和一份 manifest；
- 任一步失败立即停止，不继续消耗后续调用。

### M3.7c 盲评器

`backend/src/epiphany/writing_style_ab_blind.py`

- 验证成功 manifest 和四个结果文件的 hash；
- 随机生成 Candidate A / B；
- 把映射和 salt 单独保存在 `private/mapping.json`；
- 对公开候选保存 commitment，揭盲时重新验证；
- 候选被修改后，评分与揭盲都会被阻断；
- 相同评分可以安全重放，不同评分不能覆盖；
- 未评分不能揭盲；
- 揭盲只展示映射与证据，不自动宣布 winner。

这两部分都是离线实验工具，没有新增生产 API、数据库表、migration 或 workflow
版本。

## 5. 付费实验为什么需要单独的本地账本

四次调用没有写回原 Run，也不会出现在现有 SQLite `model_calls` 表里。这样可以
保证受控实验不篡改已经完成的业务 Run，但也意味着它不是生产工作流 Trace。

执行器使用本地私有 `manifest.json` 作为实验账本：

- 输出目录用独占创建，两个进程不能同时占用同一目录；
- 目录权限为 `0700`，文件权限为 `0600`；
- 第一次调用前先写 `running` manifest；
- 每次请求前写 `started`，返回后写 `succeeded` 或 `failed`；
- JSON 使用同目录临时文件、`fsync` 和原子替换；
- 成功 manifest 只在四个结果文件都落盘后写入。

如果进程刚好在网络请求中被强制终止，manifest 会停在 `started`。这时不能假设
“没有扣费”，也不能自动重跑；需要先和 DeepSeek Dashboard 对照。

Draft 文件包含生成正文，Quality 文件可能包含来源短句和写作 Sample 的短引文。
它们是私有实验产物，受 `.gitignore` 保护，绝不能提交 Git。

## 6. 合同 hash 冻结了什么

执行前必须提供 M3.7a dry-run 输出的
`common_experiment_contract_sha256`。现在合同包含：

- 两个 arm 共同的 Editor 输入；
- 两个 arm 实际渲染后的 Editor Prompt hash；
- 质量配置；
- Editor / Reviewer 模型、temperature、Token 与字符上限；
- Reviewer 共用 Sample 的 hash；
- Reviewer contract、Prompt 与评分公式版本；
- API base URL、计费币种和请求 timeout。

只要 dry-run 后这些内容发生变化，执行阶段就会在零调用时阻断，要求重新预检。

## 7. 本地操作

先进入后端并激活环境：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
```

### 7.1 零费用预检

```bash
python -m epiphany.writing_style_ab_execute \
  --run-id run_xxx \
  --database data/m3-7a-preflight-demo.db
```

复制输出中的 `common_experiment_contract_sha256`。这一步不联网。

### 7.2 明确执行一次真实实验

```bash
python -m epiphany.writing_style_ab_execute \
  --run-id run_xxx \
  --database data/m3-7a-preflight-demo.db \
  --output-dir artifacts/m3-7-live-pair \
  --execute \
  --expected-contract-sha256 <刚才确认的 hash>
```

只有带 `--execute`、本地 Key 可用、hash 匹配且输出目录不存在时才会调用模型。

### 7.3 准备匿名候选

```bash
python -m epiphany.writing_style_ab_blind prepare \
  --experiment-dir artifacts/m3-7-live-pair \
  --blind-dir artifacts/m3-7-live-blind
```

此时只打开：

- `candidate-A.md`
- `candidate-B.md`
- `rating-template.json`

不要打开 `private/mapping.json` 或原始 arm 文件。

### 7.4 保存评分

先复制并填写模板，再提交：

```bash
cp artifacts/m3-7-live-blind/rating-template.json \
  artifacts/m3-7-live-blind/my-rating.json

python -m epiphany.writing_style_ab_blind rate \
  --blind-dir artifacts/m3-7-live-blind \
  --input artifacts/m3-7-live-blind/my-rating.json
```

### 7.5 评分后揭盲

```bash
python -m epiphany.writing_style_ab_blind reveal \
  --experiment-dir artifacts/m3-7-live-pair \
  --blind-dir artifacts/m3-7-live-blind
```

## 8. 自动测试

Focused：

```bash
pytest \
  tests/test_writing_style_ab.py \
  tests/test_writing_style_ab_execute.py \
  tests/test_writing_style_ab_blind.py -q
```

覆盖：

- 两个 Editor 输入只差 Sample；
- 两个 Reviewer 共用同一 Sample；
- 合同漂移时零调用；
- Editor / Reviewer 输出失败后停止并保留费用数据；
- 已存在的输出目录在调用前阻断；
- manifest 与私有文件权限；
- A/B 匿名文件不泄漏 arm、模型分数、内部 ID 或注入的 Markdown/HTML；
- 候选篡改、映射篡改和评分冲突；
- 未评分不能揭盲；
- 相同提交与相同揭盲可幂等重放。

运行日志只包含稳定事件名、Run ID、arm、调用顺序、Token、费用、耗时和错误类型，
不记录 Source、Sample、Prompt、模型正文或 Key。

## 9. M3 的退出标准

M3.7 完成一次真实单 pair，并保存一次真人盲评后即停止扩展。

这里不继续做：

- 通用 benchmark 平台；
- 多用户统计显著性；
- 自动选 winner；
- 盲评 Web UI；
- 新数据库表；
- 把实验 runner 变成第二套生产 orchestration runtime。

一个 pair 只能说明“这一次有没有方向性提升”，不能证明写作 Sample 在所有主题上
都稳定有效。更真实的个人风格验证，可以在最小 Web UI 上线后，由用户用自己的
Sample 继续完成。

下一步进入 M4.1：审计已有可靠性能力并补可回放 SSE；随后 M5.1 做最小 Run Trace
页面，让 Task、Event、模型调用、费用和 Artifact 有可视反馈。

## 10. 2026-07-30 真实单 pair 证据

源 Run：

`run_1bbe5ae81b0e4f118331461ab61dd656`

该 Run 来自 M3.7a 的完整合成 fixture：模型调用是真实 DeepSeek，素材与写作
Sample 不是用户私密原文。因此这次能验证四调用协议、质量校验、费用账本、
匿名化和评分流程，也能让用户方向性判断哪稿更自然、是否愿意录；但它不能证明
“用户自己的真实写作 Sample 已经稳定提升个人声音匹配”。真实私密 Sample 的
产品效果验证留到最小 UI 提供清晰的授权上传与删除入口以后进行。

合同 hash：

`18d658b8daef1cafcc98adf85226e99e97251ab263b37694d46812d4d87ddc07`

运行结果：

| 项目 | 结果 |
| --- | --- |
| Editor | Flash，2/2 成功 |
| Reviewer | Pro，2/2 成功 |
| Retry | 0 |
| Input Tokens | 42,503 |
| Output Tokens | 9,579 |
| Provider 模型耗时 | 81,387 ms |
| 本地估算费用 | CNY 0.117905 |
| 两稿估算时长范围 | 5.64—6.02 分钟 |
| 两稿确定性分数 | 均为 58 |
| 两稿模型分数 | 均为 84.67 |
| 两稿综合分数 | 均为 39.0 |
| 两稿 decision | 均为 `blocked` |

结果再次说明模型高分不能补偿硬性问题。两稿都结构完整、Reviewer 评价也高，
但冻结素材不足以支持目标时长，因此系统没有显示“可以发布”。写作 Sample 能改变
表达方式，不能凭空增加事实和经历。

匿名候选已经生成：

- Candidate A hash：
  `9851c0f175c0cb2b1fa7a230bcbda48a021b6502f2c77388e3ed3fdcff2d58a9`
- Candidate B hash：
  `dcfb2b832a79cb8ebf79d9185c4ebdf1d43b5731abf48e255529008a113c3027`
- Mapping commitment：
  `a8f9d7031f1dec62acf9ac71c8a8eb679aa31db88875ec943072b999b66185f6`

为避免暗示，本章在真人评分前不记录哪个 Candidate 对应哪个 arm，也不逐稿展示
Reviewer 分数。当前最后一步是用户先阅读 A/B 并保存盲评；揭盲结果再补入本章。
