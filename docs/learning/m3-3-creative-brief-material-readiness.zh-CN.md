# M3.3：Creative Brief、目标时长与素材充足度

## 基本信息

- 阶段：M3.3
- 日期：2026-07-29
- Commit：本章节与实现处于同一个 focused commit
- 状态：已完成并通过自动化验证

## 1. 为什么做这一步

M3.2 已经能把初始素材、采访脚手架和补充口述整理成播客候选稿，但系统还
不知道用户到底想要：

- 10、15 还是 30 分钟；
- 写给谁听；
- 是个人反思、叙事独白、知识讲解，还是聊天式日记；
- 希望听众听完理解什么；
- 哪些表达必须保留，哪些套路应该避免。

如果只在 Prompt 里说“扩写到 30 分钟”，素材不足时，模型很容易换一种说法
重复同一个观点，或者加入没有证据支持的空泛段落。

M3.3 在 Editor 前增加两层保护：

1. 用 `CreativeBrief` 把创作目标变成严格、可保存的输入合同；
2. 用普通代码计算 `MaterialReadinessReport`。明显不足时不调用 Editor，
   而是进入第二个持久检查点，请求更多材料。

## 2. 生活化类比

可以把它想成做饭前确认人数和食材。

“做一份晚餐”太模糊；“给四个人做三道清淡的菜，其中一位不吃辣”才是可执行
的 Brief。厨师还要先看冰箱：食材只够一人份时，不应该用更多水把汤稀释成
四人份，而应该提醒补买食材或减少菜量。

这个类比的边界是：字符数只能发现“明显不够”，不能判断一段经历是否精彩、
是否真实、是否适合某位听众。M3.3 是一个保守的前置门槛，不是内容质量裁判。

## 3. 完成目标

### 3.1 Creative Brief

新的 Episode Run 保存以下创作条件：

| 字段 | 首版含义 |
| --- | --- |
| `target_duration_minutes` | 10、15 或 30 分钟 |
| `speaking_rate_chars_per_minute` | 用于估算的个人口播速度，首版默认 280 |
| `scenario` | 反思独白、叙事独白、知识讲解或聊天式日记 |
| `target_audience` | 主要听众是谁 |
| `communication_goal` | 希望这一期完成什么表达目标 |
| `tone` | 最多三个语气词，例如真诚、克制、自然口语 |
| `must_include` | 必须保留的内容 |
| `avoid_patterns` | 希望避免的表达套路 |

这些字段会随 Run 保存，而不是只存在于一次 Prompt 中。后续 Editor 与质量
评价都使用同一份合同，避免“生成时一个标准、打分时另一个标准”。

### 3.2 中文口播时长估算

首版使用一个容易解释和复现的近似：

```text
目标稿件字符数 = 目标分钟数 × 每分钟字符数
```

默认按每分钟 280 个非空白字符计算，并允许上下浮动 15%：

| 目标时长 | 中心值 | 首版容差区间 |
| ---: | ---: | ---: |
| 10 分钟 | 2,800 | 2,380–3,220 |
| 15 分钟 | 4,200 | 3,570–4,830 |
| 30 分钟 | 8,400 | 7,140–9,660 |

“非空白字符”只是首版代理值，不等于真实语音中的音节、停顿和语速。用户以后
可以根据自己的实际录音调整 `speaking_rate_chars_per_minute`。只有拿到音频，
才能测出真正的口播时长。

### 3.3 确定性素材充足度

`MaterialReadinessReport` 由普通 Python 代码生成，不调用模型，因此结果
免费、稳定、可重复。首版检查：

- 采访脚手架实际引用的初始片段是否存在；
- 是否有补充口述；
- 是否至少来自两个独立 Source；
- 去重后的非空白素材字符量是否达到目标下限；
- 同一 SourceSegment 或相同规范化正文是否被重复计算。

首版采用保守规则：一个素材字符最多支撑一个稿件字符。报告包含：

- `ready` 或 `needs_more_material`；
- 目标字符区间；
- 当前素材计数和预计可支撑时长；
- 还缺多少素材字符；
- 缺口 code；
- 从采访脚手架保留下来的针对性追问；
- 该算法不能证明什么。

报告可以保存计数、SourceReference 和问题，但不能复制用户的原始素材正文。

这里刻意没有把 Run 选择的所有初始 Source 原文继续发送给 Editor。
Interviewer 完成后，代码只保留脚手架真实引用到的初始 SourceSegment；Readiness
和 Editor 使用同一组可披露片段，再加用户明确提交的补充 Source。这样素材计数
和最终模型可见范围一致，也避免一份与本期无关的私人日记因为“曾被选中”而整份
进入最后一次请求。

### 3.4 第二个持久检查点

预期工作流是：

```text
Interviewer 完成
  -> 确定性 Material Readiness（初始素材）
  -> waiting_for_user / awaiting_more_material
  -> 用户或合成 E2E 提交补充 Source
  -> 再次确定性 Material Readiness（累计素材）
       |- ready
       |    -> 排队 Editor
       `- needs_more_material
            -> waiting_for_user / awaiting_more_material
            -> 再提交 Source 并重新计算
```

素材不足时，Run、提交记录和 Readiness Artifact 都保存在 SQLite。进程关闭
或过一段时间再回来，Run 仍应停在同一个检查点。没有合法 Resume，就没有
Editor Task，也不产生那一次模型费用。

### 3.5 自动合成 E2E 的边界

开发和回归测试不要求本人每次重新口述。E2E 可以自动：

1. 导入三份完整的合成初始 Source；
2. 用 10 分钟 Brief 运行到 `needs_more_material`；
3. 完全关闭 App，再用同一 SQLite 重启并确认没有偷偷继续；
4. 自动导入同一主题的合成补充口述；
5. Resume 后跨过门槛，只排队一次 Editor 并导出最终结果。

Fake Provider 用于免费、稳定地验证编排；显式开启 DeepSeek 时可以验证真实
模型调用、Token、费用和输出合同。

这些材料必须标记为 synthetic fixture。它证明系统流程可运行，不代表真实
用户认为稿件“像自己”，也不是一次真实用户研究。

## 4. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `backend/src/epiphany/quality_contract_schemas.py` | 定义严格的 Creative Brief |
| `backend/src/epiphany/material_readiness.py` | 纯函数计算素材计数、时长区间、缺口和追问 |
| `backend/src/epiphany/research_schemas.py` | 把 Brief 纳入新 Episode Run 输入 |
| `backend/src/epiphany/services.py` | Resume 后收集累计补充 Source，并决定排 Editor 还是继续等待 |
| `backend/src/epiphany/runtime/editor_prompts.py` | 把同一 Brief 转成目标长度约束，并将用户文字隔离为不可信数据 |
| `backend/src/epiphany/human_input_schemas.py` | 区分采访回答与补充材料两个 checkpoint |
| `backend/src/epiphany/quality_contract_e2e.py` | 自动执行暂停、重启、补充、Resume 与 Editor 的合成用户路径 |
| `backend/tests/test_material_readiness.py` | 字符边界、去重、隐私和缺口的快速单元测试 |
| `backend/tests/test_quality_contract_workflow.py` | 多轮不足、累计补充和只排一个 Editor 的工作流测试 |
| `backend/tests/test_quality_contract_e2e.py` | fixture、dry-run 与完整 Fake E2E 测试 |

## 5. 背后的技术点

### 5.1 为什么先用确定性代码

“这段素材够不够 30 分钟”包含主观判断，但“现在只有多少去重字符、离配置的
下限差多少”是代码可以可靠回答的问题。

先做确定性门槛有三个好处：

- 不花一次额外模型费用；
- 同样输入永远得到同样结果；
- 报告能展示公式、观察值和阈值，而不是只给一个神秘分数。

### 5.2 为什么 Readiness 不能直接叫质量分

一万字流水账可能不如一千字具体经历有价值。字符量达到门槛，只说明“没有
明显短缺”，不保证：

- 叙事连贯；
- 事实被来源完整支持；
- 有足够具体例子；
- 语气符合目标听众；
- 文稿自然、没有模板化表达；
- 最终录音一定达到目标时长。

这些属于 M3.4 的 Draft Quality Report。M3.4 才会加入确定性文稿检查和
严格结构化的模型评价；模型自评不能被伪装成人类评价。

### 5.3 为什么使用第二个 checkpoint，而不是让一次 Resume 失败

补充内容已经是有效用户输入。即使数量仍不足，也应该幂等保存它，再明确告诉
用户还缺什么，而不是返回一个模糊错误让输入消失。

因此 `needs_more_material` 是一种可恢复的产品状态，不是系统故障。

## 6. 自动化测试

开发中先运行聚焦测试：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_material_readiness.py -vv
```

完整验证运行：

```bash
pytest tests/test_quality_contract_workflow.py tests/test_quality_contract_e2e.py -vv
ruff check .
ruff format --check .
alembic check
pytest
```

重点验收：

- 10 / 15 / 30 分钟合同和 15% 边界计算正确；
- 重复或初始/补充重叠的 Segment 不会重复计数；
- Report 不复制 Source 原文；
- 30 分钟明显不足时不会排 Editor；
- 重启后仍停在 `awaiting_more_material`；
- 相同补充提交可以幂等重放；
- 初始 Source 或历史补充 Source 换一个 submission ID 重复提交会整批拒绝；
- 累计 500 个补充 Segment 可以接受，第 501 个会在写 Artifact/Event 前返回 409；
- Editor 只接收脚手架引用到的初始片段，而不是所有初始原文；
- 补够材料后只排一个 Editor；
- Events 和日志不含素材、Prompt、模型响应或 Key。

## 7. 本地手动验证

完整接线后，建议先使用 Fake Provider：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
alembic upgrade head
uvicorn epiphany.main:app --reload
```

打开 `http://127.0.0.1:8000/docs`：

1. 导入合成初始 Source；
2. 创建带 `creative_brief` 的 `episode-research` Run；
3. 等待第一个 `waiting_for_user`；
4. 导入合成补充口述 Source 并 Resume；
5. 调用 `GET /runs/{run_id}`，在 `artifacts` 中查看
   `kind="material_readiness_report"`；
6. 选择 30 分钟时应看到 `awaiting_more_material`；
7. 再导入补充 Source，使用新的 checkpoint Resume；
8. 达到门槛后才应出现 Editor Task。

现在可以直接使用 committed synthetic fixture：

```bash
python -m epiphany.quality_contract_e2e --provider fake --execute
```

合成 fixture 的三份原始初始 Source 合计 2,106 个非空白字符，但经过
Interviewer 引用范围收窄后，真正允许 Readiness 和 Editor 使用的是 488 个。
这低于 2,380 门槛；自动补充 2,215 个字符后，可用证据合计 2,703 个，状态
变为 `ready`。暂停时为 4 Tasks / 5 Artifacts /
3 ModelCalls，最终为 5 Tasks / 8 Artifacts / 4 ModelCalls。App 重启前后
状态、事件、Artifact、Task、调用数和费用均未变化。Fake Provider 的 Token
和费用为零。

真实 DeepSeek 只能由显式 `--provider deepseek --execute` 开启。M3.3 没有
重复发起付费验证；待 M3.4 加入质量评价后再用同一 fixture 一次性验证生成与
自评，避免两轮重复费用。

## 8. 日志、数据库与排错

排查顺序：

1. 用 Run ID 查询 `status`、`current_step` 和 `model_call_count`；
2. 查 Events，确认 Readiness 是 `ready` 还是 `needs_more_material`；
3. 查 `artifacts` 中的 Readiness Report，核对计数、阈值和 gap code；
4. 查 `tasks`，素材不足时不应存在 Editor Task；
5. 查 `model_calls`，素材不足不应新增 Editor 调用。

本地只读查询示例：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
sqlite3 data/epiphany.db
```

```sql
.headers on
.mode column

SELECT id, workflow_version, status, current_step, model_call_count
FROM runs
ORDER BY created_at DESC
LIMIT 5;

SELECT kind, json_extract(content_json, '$.status') AS readiness
FROM artifacts
WHERE run_id = 'run_...'
ORDER BY created_at;

SELECT sequence, type, payload
FROM events
WHERE run_id = 'run_...'
ORDER BY sequence;

SELECT kind, status, attempt, error_code
FROM tasks
WHERE run_id = 'run_...'
ORDER BY created_at;
```

日志和 Event 只记录 Run/Task/Artifact ID、状态、计数和 gap code。不要把
Source 原文、追问全文、Prompt、模型响应或 API Key 写入操作日志。

## 9. 这一步学到了什么

- Prompt 中的“写 15 分钟”不等于可靠的产品合同；
- 主观问题可以先拆出一个可解释、可测试的确定性下限；
- “素材不足”是正常业务状态，应该持久等待，而不是报错或偷偷灌水；
- 合成 fixture 能验证工程流程，但不能替代真实用户对个人声音的判断；
- 模型自评和用户评价必须作为不同类型的证据保存。

## 10. 限制与下一步

- 默认 280 字符/分钟和 15% 容差只是可调整的首版假设；
- 非空白字符会把标点、英文和数字一起作为近似单位；
- 素材字符量不能判断语义覆盖、故事质量或事实蕴含；
- 当前“口述”仍是已转成文字的 Source，不包含麦克风、STT 或音频节奏；
- 单个 v5 Run 最多累计 500 个补充 SourceSegment；超出会原子拒绝，用户需要
  精简材料或创建新 Run；
- DeepSeek Editor 输入仍受默认 48,000 字符上限约束；默认输出上限提高到
  20,000 tokens，以免 30 分钟 Brief 与旧的 4,000/6,000 限制冲突。上限只是
  单次响应 ceiling，计费仍按实际 Token，且不能保证模型一定写满目标长度；
- 合成 E2E 不是人类可用性验证；
- M3.3 不做模型自评、AI 检测概率或最终稿质量总分。

下一步 M3.4 将针对已经生成的 Draft 增加：

- 时长偏差、重复、引用覆盖和模板化表达等确定性检查；
- 带逐字段证据的严格模型评价；
- 与模型评价分开保存的真实用户反馈。

## 完成检查

- [x] Creative Brief 合同与 Run 输入接线完成
- [x] `ready` 与 `needs_more_material` 路径测试通过
- [x] 第二个持久检查点、重启和幂等 Resume 测试通过
- [x] 自动合成 Fake E2E 通过
- [x] 结构化日志和 SQLite 检查通过
- [x] README / Roadmap / Devlog / Spec / Architecture 已同步
- [ ] 已创建 focused commit
