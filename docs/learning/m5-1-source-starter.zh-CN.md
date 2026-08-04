# M5.1：AI 起步助手与可见的四步进度

## 基本信息

- 阶段：M5.1
- 日期：2026-08-03
- Commit：`a5837cf`（backend）、`a8a418c`（frontend）；M5.1b 加固随当前 PR 提交
- 状态：Completed；backend 全量 442 项测试通过，frontend 43 项测试与
  production build 通过；Fake 与真实 DeepSeek 浏览器 E2E 均已完成

## 1. 为什么做这一步

Project 可以从零创建，但“素材库是空的”往往正是用户最难继续的时候。用户可能
只知道自己想探索“潜水学习”，还不知道第一段应该写个人动机、担忧、知识问题，
还是未来计划。单纯提供一个更漂亮的空白编辑器，并不能解决“第一笔”问题。

这一小步增加 **AI 起步助手**：它根据当前 Project 的标题、说明，以及用户选择
的素材标题、类型、起步方式和可选意图，生成一份可以继续改写的候选文字与具体
问题。

这里的正文编辑仍然是普通 `textarea`，但它不阻塞这项功能：

- Source 本来就是纯文本，不需要富文本才能编辑；
- AI 结果会追加进同一个文本框，用户可以逐字修改、删除或继续书写；
- 当前未完成的“可视化 Scaffold 编辑器”针对后续结构化采访脚手架和 Draft，
  不是生成 Source 起步文字的前置条件。

## 2. 生活化类比

可以把 AI 起步助手想成书店里的一张试写卡。

卡片会给你一个开头和几个问题，但它还不是你正式交给编辑的稿件。你可以划掉、
补充、重写，确认内容真的属于自己以后，再把它收进素材夹。

这个类比和真实实现的区别是：系统还会把“拿卡片、生成、校验、确认”的过程保存
成 Run、Task、ModelCall、Artifact 和 Event，因此刷新页面或排查失败时仍然有
可追踪记录。

## 3. 完成了什么

实现完成后，可以观察到以下行为：

1. 在 Project 的 Source 导入区选择“日记 / 随想”“播客旧稿”或“其他”。
2. 选择“探索提纲”或“示例草稿”，可选填写一个特别想探索的问题。
3. 点击“帮我起个头”，服务端创建独立、持久化的 `source-starter` Run。
4. 页面显示四步真实进度：
   - 准备上下文；
   - 模型生成；
   - 校验结果；
   - 等待用户编辑确认。
5. 通过严格 Schema 的候选结果保存为 `source_starter_candidate` Artifact，并
   让 Run 持久化停在
   `waiting_for_user / awaiting_source_confirmation`；候选会安全进入正文编辑区，
   已有正文不会被静默覆盖。
6. 用户可以继续编辑候选文字，查看追问和不确定项，也可以清除一份尚未改动的
   AI 草稿。
7. 用户勾选事实确认并点击“确认并导入 Source”后，服务端才创建正式 Source，
   并在同一事务中完成 Project 关联、`source_starter_confirmation` Artifact、
   confirmation Event 和 Run 成功状态。
8. Source metadata 标记 `origin=ai_assisted`、`user_confirmed=true`，并保留
   起步 Run 与候选 Artifact 的 lineage。

### 候选稿不是 Source

`source_starter_candidate` 只是候选 Artifact，不是事实证据。仅仅生成成功时，
Project 的 `source_count` 不会增加。模型也不能自行创建确认 Artifact。

只有用户编辑、勾选确认，并调用确认 endpoint 后，服务端才会：

```text
候选 Artifact
  -> 用户核对并提交正文
  -> 一个数据库事务：
       导入 Source / SourceSegment
       + 关联 ProjectSource
       + 保存 confirmation Artifact
       + 追加 workflow.source_starter.confirmed / run.succeeded Event
       + Run waiting_for_user -> succeeded
```

事务中任一步失败，以上写入会一起回滚。Run 仍停在原来的确认检查点，用户重试
不会面对“Source 已创建但 Trace 没成功”之类的半完成状态。

### 为什么禁用两种类型

- `writing_sample` 必须来自用户本人，否则会污染未来的个人风格判断；
- `voice_note_transcript` 必须来自真实口述转写，否则会把 AI 文字伪装成录音。

所以 AI 起步只允许 `journal`、`podcast_draft` 和 `other`。前端不展示按钮，
后端 Schema 也会拒绝绕过页面提交的禁用类型。

## 4. 代码模块地图

| 文件或目录 | 作用 | 为什么放在这里 |
| --- | --- | --- |
| `backend/src/epiphany/source_starter_schemas.py` | 请求、Task input、候选、确认响应和严格校验 | 把产品合同集中在 Provider 之外 |
| `backend/src/epiphany/runtime/source_starter_prompts.py` | 中文 Prompt 与防编造规则 | 模型指令和业务状态分离 |
| `backend/src/epiphany/project_api.py` | 创建起步 Run、确认候选的 HTTP endpoint | 起步动作属于一个 Project |
| `backend/src/epiphany/services.py` | Project 快照、请求 fingerprint 和 Run 幂等创建 | 复用既有持久化 Run 边界 |
| `backend/src/epiphany/project_service.py` | 用户确认的原子事务、lineage、幂等重放和 confirmation Artifact/Event | 确认是 Project/Source 写操作，不交给模型 |
| `backend/src/epiphany/source_service.py` | 在调用方事务中导入或去重 Source/Segments | 让确认事务不会在中途提前 commit |
| `backend/src/epiphany/main.py` | Run/确认共享 mutation lock | 防止确认、取消等写操作同时越过状态边界 |
| `backend/src/epiphany/runtime/orchestrator.py` | 排队唯一 Task，并在候选成功后进入 durable confirmation checkpoint | 调度与模型生成分离 |
| `backend/src/epiphany/runtime/worker.py` | 调用 Provider、校验、有限修复并保存候选 Artifact | 复用 retry、lease、fencing 和 ModelCall 账本；失败时不会无限调用模型 |
| `backend/src/epiphany/runtime/providers/fake.py` | 可重复、零费用的起步候选 | 本地开发和自动测试不依赖网络 |
| `backend/src/epiphany/runtime/providers/deepseek.py` | 将同一 Task 接到可选 DeepSeek Provider | 保持 Provider 可替换 |
| `frontend/src/features/sources/SourceImporter.tsx` | 起步设置、生成、编辑、确认和导入 | 用户就在这里创建 Source |
| `frontend/src/features/runs/CreateRunForm.tsx` | 过滤并拒绝 AI-assisted Writing Sample | 候选来源不能反过来伪装成用户个人文风 |
| `frontend/src/features/runs/TracePanels.tsx` | 在完整 Trace 中展示候选、确认和等待状态 | 页面步骤可以下钻到持久化证据 |
| `frontend/src/lib/sourceStarter.ts` | 解析候选、推导四步进度、追加/安全移除文字 | 把纯函数从 React 组件中拆出，便于测试 |
| `frontend/src/api/epiphany.ts` | 两个 Project-scoped API 调用 | 统一保留请求错误和 request ID |

## 5. 背后的技术点

### 5.1 一个独立、持久化的短 Workflow

起步助手不是浏览器直接请求模型。它使用单独的 workflow：

```text
source-starter Run v1
  -> build_source_starter Task
  -> reserve ModelCall
  -> Fake 或 DeepSeek Provider
  -> strict SourceStarterCandidate validation
  -> source_starter_candidate Artifact
  -> Run waiting_for_user / awaiting_source_confirmation
  -> user confirmation transaction
  -> Run succeeded / complete
```

因此它自动继承现有 Runtime 的单 Run 调用预算、重试、timeout、lease、fencing、
取消、恢复、费用记录和 Trace。首版只有一个模型 Task，不引入新的 Agent 框架，
也不需要数据库 migration。

### 5.2 Project 使用创建时快照

创建 Run 时，服务端从数据库读取 Project 的 ID、标题和说明，写入 Run input。
模型看到的是这次 Run 的固定快照，不是执行到一半时浏览器里的临时变量。

素材标题、类型、mode 和 intent 同样进入 fingerprint。相同
`submission_id + payload` 可以安全重放；同一个 ID 搭配不同 payload 返回
409，避免网络重试产生重复调用或混淆意图。

### 5.3 防编造不是靠一句提示

Prompt 要求：

- 不编造第一人称经历、对话、日期、感受和成果；
- 外部事实写成 `[待核实：……]`；
- 个人经历写成 `[待补充：……]` 或具体问题；
- Project 文本和 intent 都按不可信输入处理；
- 输出必须满足 strict JSON Schema。

live DeepSeek 验收说明，Prompt 本身仍可能被模型宽松理解：模型可能把输入中
没有提供的常见担忧写成第一人称事实。修复因此增加两层约束：

- Prompt 明确要求检查正文、问题和不确定项中的个人叙事；只允许输入中已有的
  第一人称原话、真正的问句或 `[待补充]` / `[待核实]` 占位；
- 确定性输出 guard 在 Provider 返回后检查 `starter_text`、`questions` 和
  `uncertainties`，拒绝无输入依据的第一人称断言、明显预设用户经历的问题、
  探索提纲中的省略主语个人史，以及未标记待核实的明显数字/法规/研究类事实。
  这些是有界启发式规则，不冒充通用事实判定器；命中时统一以
  `task_output_invalid` 拒绝。

guard 的错误只暴露 fragment 编号，不保存被拒绝的具体句子。为了避免“整个候选
只因一句越界就全部退化成车轱辘占位符”，当前失败处理是有界的三级链路：

```text
首次模型输出不合规
  -> 同一 Task 最多一次 repair retry（第二笔 ModelCall 可在 Trace 中看到）
  -> 第二次形状正确但仍有内容越界：server_line_grounding
       保留逐行验证通过的主题内容，只把危险行改成 [待补充] / [待核实]
  -> 行级修复后仍无法通过完整合同，或 JSON 形状本身不可用：server_safe_template
```

`server_line_grounding` 之后仍会再次执行同一套完整 Schema 与安全校验；它不是绕过
校验。Provider/网络本身失败也不会被伪装成一份“成功模板”。即使三级链路通过，
系统仍不能证明模型每句话都真实，因此还保留最后一道产品边界：候选不是 Source，
必须由用户显式编辑并确认。

### 5.4 四步进度不是装饰动画

页面不是用计时器假装“正在思考”。四步状态来自持久对象和 Event：

| 页面步骤 | 主要证据 |
| --- | --- |
| 准备上下文 | Run 已创建即完成；Task/ModelCall 属于后续步骤 |
| 模型生成 | `model.call.started/completed`、Task 状态 |
| 校验结果 | Task 成功、候选 Artifact、`workflow.source_starter.completed` |
| 等待编辑确认 | Run checkpoint、候选 Artifact；完成只认服务端 confirmation Artifact/Event |

前三步属于自动化执行。第四步是 Run 的真实 durable checkpoint：候选通过校验后，
Run 停在 `waiting_for_user / awaiting_source_confirmation`。页面 checkbox 只代表
本地意图，不能单独把第四步标成完成；只有服务端 confirmation Artifact 或 Event
才能证明候选已经成为 Source。确认事务完成后，Run 才进入 `succeeded / complete`。

### 5.5 普通文本框也能安全协作

生成前，前端保存当前正文作为 base text。候选只会：

- 正文为空：直接写入；
- 正文已有内容：用清晰分隔符追加；
- 刷新页面且正文仍为空：从服务器 Candidate Artifact 恢复候选；
- 刷新后用户已经输入正文：只预览恢复的候选，必须由用户显式“追加候选到正文”；
- 检测到候选已经被用户编辑：禁止自动清除或重新生成，避免把两版文字混在一起；
- 用户显式放弃未改动的当前 Run 后，才能开始一次新的生成调用。

这比依赖富文本编辑器的内部文档模型更容易验证。未来升级为可视化编辑器时，
仍可继续复用同一个后端候选合同。

浏览器中的未提交编辑目前只存在于 React 状态，不会实时保存到服务端。因此刷新
可以恢复服务器保存的原始候选，却不能恢复用户尚未导入的本地改写；页面会明确
提示这一限制，而不会假装已经恢复。

### 5.6 确认也必须幂等

确认请求包含独立 `submission_id`。服务端只对标题、类型和最终正文计算**语义
fingerprint**，`submission_id` 不参与内容身份；Artifact 另行审计所有已接受的
`submission_ids`。确认路径在共享 mutation lock 与单一数据库事务中完成：

- 相同语义内容即使换了新的 `submission_id`：返回原 Source 和 confirmation
  Artifact，并把新 ID 追加到审计列表；
- 已确认后改变正文再重放：返回 409；
- 若相同正文已作为 `writing_sample` 或无 AI provenance 的普通 Source 存在，确认
  返回 409，不复用并污染旧 Source 的 provenance；
- Run 不在确认检查点、候选丢失或类型改变：拒绝确认；
- Source、ProjectSource、confirmation Artifact、Events 和 Run 成功状态任一步
  出错：整个事务回滚，仍可从原检查点重试。

这样浏览器超时后重试，不会创建两份 Source；模型也没有机会伪造用户确认。

### 5.7 网络重试不等于重新调用模型

轮询中断后的“重试”只执行 `GET /runs/{id}` 与 `GET /runs/{id}/events`，读取同一
Run 的持久状态，不会再次发送创建 Run 的 POST，也不会新增 ModelCall。只有用户
明确放弃当前 Run 并点击重新生成，才会创建新的调用。严格模型输出校验失败时，
Task 只保存通用错误码和通用错误文本，不把模型的无效原始字段写进错误信息。
取消请求若发生 response loss，页面同样 GET 同一 Run 做 reconciliation；mutation
guard 防止重复越过状态边界，不会重复调用模型。

### 5.8 AI 辅助素材不能冒充个人 Writing Sample

即使用户把 AI 候选确认成了普通 `journal` 或 `podcast_draft`，它仍保留
`origin=ai_assisted` provenance。系统在多层阻止它成为风格身份样本：

- 前端创建 Run 时不让用户选择，并在提交前再次过滤；
- 后端创建 Project Run 时验证 style source；
- Editor/Reviewer hydrate Writing Style Profile 时再次验证；
- Revision 继承或添加风格上下文时再次验证。

它仍可以作为用户确认过的事实/头脑风暴素材使用，但不能被系统当成“这就是用户
本人原有文风”的证据。

## 6. 自动化测试

### 后端定向测试

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_source_starter.py -q
```

该文件覆盖：

- Run 创建、Project 快照和 workflow v1；
- 相同请求幂等重放与不同请求 409；
- Fake Provider 生成、strict validation 和一个 ModelCall；
- 候选生成后进入 durable waiting checkpoint，且不会自动增加 Source；
- 用户确认的原子事务、AI-assisted metadata、confirmation Artifact/Event 与
  `waiting -> succeeded`；
- 相同语义、不同 submission ID 的重放与审计列表；不同正文冲突；
- 与既有 Writing Sample 或普通 Source 的相同正文碰撞返回 409，不吞掉
  AI-assisted provenance；
- 确认中途崩溃时整笔事务回滚，随后可恢复；
- Provider retry、waiting 后取消，以及无效模型输出的错误脱敏；
- `writing_sample` / `voice_note_transcript` 后端拒绝；
- Prompt 保留未知项并禁止编造。

### 前端定向测试

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm test -- tests/sourceStarter.test.ts tests/sourceImporter.test.tsx
```

前端覆盖：

- 已有正文只追加、不覆盖；刷新恢复也不会覆盖页面打开后新输入的正文；
- 只有完全未编辑的候选可以自动清除；编辑后不能叠加一份重新生成的候选；
- 进度由 Run / Task / ModelCall / Artifact 推导；
- 第四步只由持久 confirmation Artifact/Event 完成；
- 网络失败后的重试只 GET 当前 Run，不重复模型调用；
- 取消请求响应丢失后 GET 同一个 Run 对账；快速双击不会创建第二个替换 Run；
- 刷新可恢复服务器 Candidate Artifact，失败/取消并放弃的 Run 不会复活；
- 刷新不仅恢复候选正文，也会从 Run input 恢复当时选择的 `mode` 与 `intent`，
  不会把“示例草稿”悄悄显示成默认“探索提纲”；
- 未确认时不能导入；
- 禁用两种身份/采集来源类型；
- 请求失败后保留标题和正文。

本阶段自动化验收使用：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
ruff check .
ruff format --check .
pytest -q

cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm test
npm run build
```

2026-08-03 的结果：

- backend 共收集并通过 442 项 `pytest`；`ruff check .` 与
  `ruff format --check .` 通过；
- frontend 共 43 项测试通过；`npm run build` 通过；
- Fake 浏览器 E2E 通过；
- Playwright 驱动的真实 `deepseek-v4-flash` Source Starter 与主口播链路通过。

## 7. 浏览器与 live Provider 验收证据

### 7.1 Fake 浏览器闭环

本地浏览器使用 Fake Provider 完成了从空 Project 到正式 Source 的完整路径：

1. 创建起步 Run，生成 Candidate Artifact；
2. 刷新页面后，从服务端 Artifact 恢复尚未确认的候选；
3. 确认前，四步 DOM 状态是
   `complete / complete / complete / active`，Project Source 数没有增加；
4. 用户编辑并确认后，Source、Project 关联、confirmation Artifact/Event 与 Run
   成功状态一起落库；
5. 确认后四步全部为 `complete`；Run Trace 依次显示
   `workflow.source_starter.completed`、`run.waiting_for_user`、
   `workflow.source_starter.confirmed` 和 `run.succeeded`。

该浏览器闭环使用的确认 Run 为
`run_d833323a39fd4a59a8f2838e8ad86b78`。浏览器验证证明页面能操作真实持久状态；
注入崩溃后的全事务回滚仍由 backend 自动测试证明。

### 7.2 M5.1b：同一个零素材 Project 的两种真实模式

Playwright 在一个“潜水入门”合成 Project 上像真实用户一样点击页面，而不是绕过
UI 调接口：

| 模式 | Run | 结果 |
| --- | --- | --- |
| 探索提纲 | `run_3c637233a17843119f546c1521ec0024` | 一次真实调用即通过严格校验；问题地图可用，但用户为比较模式而放弃，Source 数保持 0 |
| 示例草稿 | `run_8eda93938e4b4a1881eb47453bd5073f` | 有界 repair 后仍含未经提供的第一人称内容；`server_line_grounding` 保留主题线索、把危险部分显式标为待补充 |

第二条 Run 没有把模型文字直接升级成事实。Playwright 模拟用户在普通文本框中补入
自己的动机、耳压与失控担忧、需要查证的问题和下一步计划，勾选确认后才导入一份
516 字 Source。刷新/恢复时页面还从 Run input 还原了 `starter_draft` 和原 `intent`，
没有回到默认模式。可复现输入在
[`backend/fixtures/e2e/m5-1b-real-browser/`](../../backend/fixtures/e2e/m5-1b-real-browser/)。

这次对比说明：零个人事实时，“探索提纲”天然比“示例草稿”更稳；示例草稿可以
帮助迈出第一笔，但必须清楚显示候选性质，并让用户补事实后再确认。

### 7.3 同一浏览器里的完整口播主链

M5.1b 还使用完整合成 persona、事实 Source、Writing Sample 与补充口述，从页面
跑通三段不可变 Run：

| Run | 阶段 | 结果 |
| --- | --- | --- |
| `run_c41c726fdcca4136bd1e317dbcbce21a` | 初稿 | 10.11 分钟，100% 段落引用 |
| `run_c344c19e9cb844c29c4daac81434cb00` | 有证据的时长恢复 | 12.61 分钟，100% 引用；仍低于内容目标 |
| `run_2fec917404234405b9ec7c2c9ab16802` | 回答四个稿件定向问题后的 Revision | 14.59 分钟，26/26 段落引用，5 Sources / 31 Segments |

最终稿达到 15 分钟目标的 85% 接受下限，但质量系统仍检测到 7 处平行对照句式，
把综合分限制为 79，并给出 `revision_recommended`。这不是自相矛盾：时长与引用
通过只表示证据和篇幅合格，不等于语言已经值得发布。

完整步骤、费用、Trace 证据、踩坑与剩余问题记录在
[`M5.1b 真实浏览器 E2E`](../experiments/m5-1b-real-browser-e2e.zh-CN.md)。

## 8. 本地手动验证

### 8.1 启动

后端终端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
alembic upgrade head
EPIPHANY_MODEL_PROVIDER=fake uvicorn epiphany.main:app --reload
```

前端终端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm run dev
```

打开 Vite 输出的本地地址，通常是 <http://127.0.0.1:5173>；若端口被占用，
以终端实际显示的地址为准。

### 8.2 页面操作

1. 创建 Project：标题“潜水学习”，说明“想探索一个完全陌生的新领域”。
2. 打开“添加素材”，保留类型“日记 / 随想”。
3. 标题可填“我为什么想了解潜水”，也可以先留空。
4. 选择“探索提纲”，可选填写“我为什么会被潜水吸引？”。
5. 点击“帮我起个头”。
6. 观察四步状态依次变化，并可点击“查看完整 Run trace”。
7. 成功时，正文出现带 `[待补充：…]` / `[待核实：…]` 的候选；Project 的
   Source 数仍未增加；Run 应显示
   `waiting_for_user / awaiting_source_confirmation`。
8. 修改正文，补入一处属于自己的真实动机；确认标题完整。
9. 勾选事实确认，点击“确认并导入 Source”。
10. Source 数加一，Run 进入 `succeeded / complete`；打开 Source 可看到最终
    确认后的正文，而不是模型原始候选。
11. 打开起步 Run Trace，确认存在候选 Artifact、一个 ModelCall，以及最后的
    `run.waiting_for_user`、confirmation Artifact/Event，以及最后的
    `workflow.source_starter.confirmed -> run.succeeded`。

可额外验证恢复边界：候选出现后刷新页面，应该从 Artifact 恢复原始候选；先在
空白正文里输入一句话再等待恢复结果，系统不应覆盖，而应提供显式追加按钮。断网
或 GET 失败后点击重试，Trace 中的 ModelCall 数必须保持不变。未导入的本地改写
刷新后无法恢复，这是当前已明确展示的限制。

再切换类型为“写作样本”和“口述转写”，页面应隐藏生成按钮并解释原因。

## 9. 日志与排错

重要 durable Event：

- `run.created`
- `task.queued`
- `run.started`
- `task.started`
- `model.call.started`
- `model.call.completed` / `model.call.failed`
- `task.succeeded` / `task.failed` / `task.retry_scheduled`
- `workflow.source_starter.completed`
- `workflow.user_input.requested`
- `run.waiting_for_user`
- `workflow.source_starter.confirmed`
- `run.succeeded`

重要 stdout event：

- `worker.task.claimed`
- `worker.task.completed`
- `worker.task.failed`
- `project.source_starter.confirmed`
- `project.source_starter.confirmation_replayed`
- `project.source.linked` / `project.source.link_replayed`

建议排查顺序：

1. 看页面四步停在哪一步；
2. 复制页面错误保留的 `X-Request-ID`；
3. 打开完整 Run Trace，查看失败 Task、attempt、error code 和 ModelCall；
4. 用 `run_id / task_id / request_id` 搜索 Uvicorn JSON 日志；
5. 刷新页面，确认持久状态能否恢复；
6. 最后再只读查询 SQLite。

日志和 Event 只能记录 ID、状态、次数、耗时、Provider/model 和错误码。不要记录
Project 说明、素材标题之外的正文、Prompt、模型响应、最终 Source 或 API Key。

## 10. 这一步学到了什么

- “能编辑”不等于必须先有富文本编辑器；先固定数据合同，普通文本框已经能完成
  一个真实闭环。
- AI 输出和用户证据必须是两个对象。Artifact 可以保存候选，Source 必须经过
  人工确认。
- 进度 UI 最有价值的部分不是动画，而是把 Run、Task、ModelCall、Artifact、
  Event 的真实状态翻译成普通人看得懂的步骤。
- 人工 checkbox 只是界面动作；真正可审计的确认需要由服务端写入幂等 Artifact。
- 禁止一种危险类型不能只隐藏按钮，还要在后端 Schema 再拒绝一次。
- “重试读取状态”和“重新生成”必须是两种动作；前者不能产生第二笔模型费用。
- 多对象确认操作需要一个事务，否则崩溃可能留下 Source、Trace 和 Run 状态互相
  矛盾的半成品。
- Schema 只能约束输出形状，不能单独约束叙事真实性；关键内容边界需要 Prompt、
  确定性 guard 和人工确认共同承担。

## 11. 限制与下一步

当前没有解决：

- 可视化 Scaffold / Draft 编辑与恢复；
- Source 起步候选的本地自动草稿保存；刷新只能恢复服务器 Artifact，不能恢复
  尚未导入的编辑；
- AI 候选的多版本并排比较；
- 外部事实检索、网页引用和潜水安全知识校验；
- 真实录音、STT、TTS 或 voice cloning；
- 真实用户私有素材与真人可录性验收；当前只有完整合成 persona 的浏览器证据；
- Docker、单机部署和备份。

M5.1 已经完成。下一步回到 M5 的可视化 Scaffold/Draft 编辑与恢复。Source
Starter 使用普通 textarea 已经足以完成“生成候选—编辑—确认—导入”的闭环，
因此没有被可视化编辑器阻塞。对陌生领域的事实研究应作为未来独立的“带来源
Research”能力，不能把无引用的模型文字悄悄提升为 Source。

## 完成检查

- [x] 正常路径测试通过
- [x] 失败路径测试通过
- [x] Fake 本地浏览器 E2E 通过
- [x] 修复后的受控 live DeepSeek 验收通过
- [x] 无效模型输出不会泄漏到持久错误信息
- [x] README / Roadmap / Devlog 已同步
- [x] 学习手册已同步
- [x] 已创建 focused commit
