# M3.7a：写作样本 A/B 的冻结输入与零费用预检

状态：已完成

日期：2026-07-30

## 1. 这一步要回答什么

M3.6 已经允许用户提供明确授权的 Sample Writing，Editor 可以参考它的节奏、
句长和口语感。但“系统支持 Sample”不等于“Sample 真的让稿子更像用户”。

要验证效果，最直观的实验是生成两份稿：

- A：Editor 看不到 Sample；
- B：Editor 看到 Sample。

难点不是多调用一次模型，而是保证两组之间**只有这一个变量**。如果 A 和 B
同时使用了不同素材、Brief、采访回答或模型，最后即使一份更好，也无法知道
究竟是哪项差异造成的。

M3.7a 因此只解决一个很窄的问题：

> 能不能从同一个已完成 Run 冻结原始 Editor 输入，并可靠地派生出只在
> 写作样本上下文上不同的两个实验 Arm？

这一阶段不生成新稿、不调用 DeepSeek，也不做盲评。

## 2. 非技术类比

把实验想成比较一道汤里“是否加入某种香料”。

可靠做法是先煮好同一锅汤，再分成完全相同的两碗，只给其中一碗加香料。
如果两碗连食材、火候和盐量都不同，就不能把味道差异归因于香料。

在这个项目里：

- 同一锅汤：冻结的 Editor Task 输入；
- 香料：`writing_style_profile` 和 `writing_style_segments`；
- 两碗汤：`without_sample` 与 `with_sample` 两个 Arm；
- 检查两碗是否相同：canonical JSON + SHA-256 hash。

## 3. 为什么先单独做预检

完整 A/B 最少会包含：

1. 无 Sample 的 Editor 调用；
2. 有 Sample 的 Editor 调用；
3. 对无 Sample 稿的 Reviewer 调用；
4. 对有 Sample 稿的 Reviewer 调用；
5. 候选稿匿名化；
6. 用户盲评；
7. 最后揭示 A/B 映射。

如果一次加入全部功能，代码、测试、日志和文档会一起膨胀，失败时也很难判断
是输入不受控、模型输出不合规，还是盲评数据有问题。M3.7a 先把最底层的实验
可信度独立验证，后续切片只需要在这个已经证明的输入上继续工作。

## 4. 本次完成了什么

新增命令：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
python -m epiphany.writing_style_ab \
  --run-id run_xxx \
  --database data/epiphany.db
```

命令会：

1. 读取指定 SQLite 数据库；
2. 找到一个成功完成的 workflow-v8 `episode-research` Run；
3. 要求它恰好有一个成功的原始 Editor Task；
4. 重新校验该 Editor 的输入和最终 Draft；
5. 校验 Run 明确授权了写作样本；
6. 校验持久化 style profile 与 Editor 实际收到的一致；
7. 从同一输入派生两个 Arm；
8. 证明去掉风格字段后的共同输入 hash 完全相同；
9. 输出不含正文的安全预检报告。

这个命令故意没有 `--execute` 参数。M3.7a 永远：

- 不联网；
- 不调用 Provider；
- 不产生费用；
- 不创建实验产物，也不修改 Run 业务记录；
- 不修改原 Run；
- 不打印素材、Sample、Prompt 或 API Key。

SQLite 在打开数据库时仍可能维护自己的 WAL/SHM 连接辅助文件；这里的“不写”
指应用不会新增或修改 Run、Task、Artifact、Event 和实验结果文件。

## 5. 两个 Arm 到底差在哪里

`with_sample` 完整保留原 Editor 输入。

`without_sample` 只执行：

```text
writing_style_profile = null
writing_style_segments = null
```

以下内容必须保持完全相同：

- topic；
- 初始事实 Source 和 Segment；
- 补充口述 Source 和 Segment；
- Interview Scaffold；
- Creative Brief；
- 目标时长；
- audience、tone 和 scene；
- Draft Quality 配置；
- Editor 模型计划。

代码会把风格字段排除后，对两个 Arm 的 JSON 做稳定排序和序列化，再计算
SHA-256。两个 hash 不一致时，预检直接失败，不允许进入后续实验。

## 6. 为什么还要计算 Prompt hash

共同输入 hash 证明业务数据一致；Prompt hash 则证明最终发给模型的请求确实
因 Sample 有无而发生了变化。

预检输出两个 Prompt 的 hash，不输出 Prompt 正文：

```json
{
  "arm_prompt_sha256": {
    "without_sample": "...",
    "with_sample": "..."
  }
}
```

理想结果是：

- common input hash 相同；
- 两个 Prompt hash 不同；
- `only_variable_is_writing_sample` 为 `true`。

系统还会对质量配置、Editor/Reviewer 模型、temperature、token/bundle 上限
和 Reviewer 共用的 style context 计算一个更宽的
`common_experiment_contract_sha256`。这样后续 M3.7b 不会只冻结 Editor，
却悄悄给两边使用不同 Reviewer 条件。`style_context_sha256` 仍是私人样本的
可关联指纹，只适合留在本地实验记录，不应复制到公开 Issue 或 PR。

## 7. 模块地图

| 文件 | 作用 |
| --- | --- |
| `backend/src/epiphany/writing_style_ab_schemas.py` | 定义冻结输入和两个 Arm 的严格类型 |
| `backend/src/epiphany/db.py` | 为预检提供 SQLite `mode=ro + query_only` 只读连接 |
| `backend/src/epiphany/writing_style_ab.py` | 从 SQLite 加载、校验、派生 Arm、计算 hash 和输出预检 |
| `backend/tests/test_writing_style_ab.py` | 验证零网络、单一变量、有效加载和失败边界 |

本阶段复用已有模块：

- `PodcastDraftTaskInput`：原始 Editor 输入合同；
- `EpisodeResearchPayload`：Run 创建时的授权与配置；
- `validate_podcast_draft_output`：确认冻结的是合法 Draft；
- `build_editor_prompt`：计算两个 Arm 的 Prompt hash；
- `Database / Run / Task / Artifact`：从持久化状态恢复实验输入。

没有新增数据库表，也没有 Alembic migration。

## 8. 自动化测试

运行：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_writing_style_ab.py -q
```

本阶段十个 focused tests 覆盖：

1. 预检不触发网络、不泄漏正文或 Key；
2. 两个 Arm 除风格字段外完全相同；
3. Sample 必须真的改变 Editor Prompt，否则阻断；
4. 一个成功、授权且 profile ready 的 v8 Run 可以被加载；
5. Run output、topic、Brief 或质量配置被篡改时拒绝冻结；
6. SQLite 连接使用只读模式，写语句被数据库拒绝；
7. 数据库路径不存在时不创建目录或空数据库；
8. Run 不存在时安全阻断，且数据库文件内容 hash 不变。

当前结果：

```text
10 passed
```

完整后端回归共 308 项测试全部通过；全仓 Ruff lint/format、从空库升级到
`0004_run_lineage` 和 `alembic check` 也全部通过。M3.7a 本身没有修改数据库
schema。

## 9. 一次接近真实流程的手动验证

先用 Fake Provider 创建一个完整的 v8 父子 Revision 流程：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
python -m epiphany.guided_revision_e2e \
  --execute \
  --database data/m3-7a-preflight-demo.db \
  --output-dir artifacts/m3-7a-preflight-demo
```

再把成功父 Run 作为冻结输入：

```bash
python -m epiphany.writing_style_ab \
  --run-id run_1bbe5ae81b0e4f118331461ab61dd656 \
  --database data/m3-7a-preflight-demo.db
```

观察到的关键结果：

| 项目 | 结果 |
| --- | --- |
| source Run | `run_1bbe5ae81b0e4f118331461ab61dd656` |
| Provider 调用 | 0 |
| 是否联网 | 否 |
| 共同 Editor 输入是否相同 | 是 |
| style readiness | `ready` |
| style Source / Segment | 1 / 7 |
| style 非空白字符 | 846 |
| 是否输出 Source 正文 | 否 |
| 是否输出 Sample 正文 | 否 |
| 是否输出 Prompt / API Key | 否 |

这次 Fake E2E 只用来准备一个结构真实、可恢复的 v8 Run。它没有验证真实模型
是否更像用户。

## 10. 日志和排错

成功时寻找：

```text
"event": "writing_style_ab.preflight"
"only_variable_is_writing_sample": true
"provider_calls_executed": 0
```

失败时命令退出码为 2，并输出：

```text
"event": "writing_style_ab.blocked"
```

常见原因：

- `writing_style_ab_run_not_found`：Run ID 或数据库路径错误；
- `writing_style_ab_source_invalid`：不是成功的 v8 Run、Editor 不唯一、
  Draft 已不符合合同、没有明确授权 Sample，或 profile 尚未达到 `ready`。

预检看到 `.env` 中存在 Key 时只显示 `api_key_status=present`，仍然不会读取后
发送请求。是否有 Key 不改变零网络边界。

## 11. 这一步学到的技术点

### 冻结输入

实验输入来自已经持久化并完成校验的 Task，而不是重新跑 Researchers 和
Interviewer。这样上游模型随机性不会污染 A/B。

### 单一变量原则

不能只“看起来差不多”。代码必须把允许变化的字段明确列出，并对其余结构
生成可重复 hash。

### Canonical JSON

普通 JSON 对象键的显示顺序可能不同。先稳定排序、使用固定分隔符，再计算
hash，才能让同一结构在不同运行中得到同一结果。

### 安全预检

可能产生费用或处理私人内容的操作，先提供默认零网络模式。预检确认边界后，
付费执行才在后续独立切片中显式加入。

### 数据库只读

普通应用连接会启用 SQLite WAL；预检改用 URI `mode=ro` 并设置
`PRAGMA query_only=ON`。路径不存在时直接失败，写 SQL 会被 SQLite 拒绝。
读取 WAL-mode 数据库时 SQLite 仍可能维护 `-wal/-shm` 辅助文件，但不会改变
Run、Task、Artifact、Event 等业务记录。

## 12. 仍未完成什么

M3.7a 不能回答“Sample 是否有效”。它只证明将来的比较可以公平进行。

后续严格拆成：

- M3.7b：最多四次真实调用——两个 Flash Editor、两个 Pro Reviewer；
- M3.7b 在第一次付费调用前必须重新计算共同实验合同 hash，避免预检后到
  执行前数据库或配置发生变化；
- M3.7c：把两份口播正文匿名化为 Candidate A / B，先保存用户
  `voice_match_rating`、`recordability_rating` 等盲评，再揭示映射；
- 实验验收：同一主题先做一组方向性验证；若要声称效果稳定，应至少扩展到
  三个不同主题，而不是从单个样本下结论。

两个 Reviewer 都必须看到同一份授权 Sample。否则无 Sample 稿没有共同风格
参照物，`personal_style_match` 分数不可比较。模型评分只是辅助证据；“是否
真的像本人”仍以用户盲评为主。

## 13. 下一步

先把 M3.7a 作为独立 commit 和 PR 审核、合并。之后再开始 M3.7b，不把模型
执行器和盲评 UI/数据合同塞回同一批改动。
