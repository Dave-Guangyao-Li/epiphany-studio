# M4/M5：Project 工作区与可重放 Run Trace

日期：2026-07-31

状态：本地 Console 已完成；Scaffold 编辑与部署仍未完成

## 1. 这一阶段完成了什么

此前系统已经能完成 Agent 工作流，但主要入口是 Swagger。它适合确认一个 API
是否正确，不适合建立“我正在完成一期播客”的整体感觉。

这一阶段增加了两个可以直接操作的页面：

- **Project / Source 工作区**：把同一主题的素材、创作配置和历次 Run 放在一起；
- **Run Trace**：实时查看 Agent 的 Event、Task、Artifact、模型调用和错误。

同时补齐可重放 SSE。浏览器断线后不依赖内存里的旧消息，而是继续从 SQLite
保存的 Event sequence 恢复。

完成后的本地路径是：

```text
创建 Project
  -> 导入或读取 TXT / Markdown Source
  -> 选择事实素材与可选风格样本
  -> 填写主题、受众、语气、时长
  -> 创建一个持久化 Run
  -> 在 Trace 页面观察 Agent 执行
  -> 人工检查点补充文字 Source 并 Resume
  -> 查看 Draft、Show Notes 与质量报告
  -> 人工反馈，或显式创建 Revision 子 Run
```

## 2. 非技术类比

可以把 Project 想成一张“节目制作桌”：

- Source 是桌上的日记、旧稿和口述转写；
- Run 是某一次把这些材料交给制作团队的正式委托；
- Task 是研究员、采访者、编辑和审稿人的工作单；
- Artifact 是每张工作单交回来的不可变成果；
- Event 是按时间编号的制作日志；
- ModelCall 是模型调用的费用与耗时小票。

关闭浏览器不会把制作桌清空，因为桌面状态在 SQLite，不在 React 内存里。
重新打开页面只是再次读取这张桌。

## 3. Project、Source、Run 和 Event 的关系

| 对象 | 保存什么 | 为什么单独存在 |
| --- | --- | --- |
| Project | 标题、说明、Source 关联、Run 历史 | 提供一个用户能理解的创作工作区 |
| Source | 规范化文字、hash、类型和稳定片段 | 同一正文只保存一次，可被可靠引用 |
| ProjectSource | Project 与 Source 的关联 | 一个 Source 可关联到不同 Project，不复制正文 |
| Run | 一次不可变工作流请求与当前状态 | 重试、刷新或 Revision 不覆盖过去 |
| Task | 一个 Agent 或确定性步骤的执行状态 | 能看到具体卡在哪个工作单 |
| Artifact | 结构化研究、Scaffold、Draft、报告 | 结果可恢复、可比较、可追踪 |
| Event | Run 内单调递增的 sequence 与事件类型 | 支持历史回放和实时 Trace |
| ModelCall | provider、model、tokens、耗时、费用 | 区分业务失败与厂商调用成本 |

`ProjectSource` 是关联表，不是第二份 Source。相同正文再次导入时，SourceService
先按内容去重；如果该 Source 尚未属于当前 Project，只增加一条关联。

Project 页面创建的 Run 保存 `project_id`。Revision 子 Run 自动继承父 Run 的
Project，因此父稿、子稿和后续回答稿不会散落到不同工作区。

## 4. 为什么创建 Run 还需要 submission_id

浏览器双击按钮、Wi-Fi 断开后自动重试，都可能把同一个 POST 发两次。如果每次
都创建新 Run，就可能花两份模型费用。

前端为同一份表单内容生成稳定的 `submission_id`，后端同时保存请求
fingerprint，并对 `(project_id, submission_id)` 建唯一约束：

- 相同 key + 相同 payload：返回第一次创建的 Run；
- 相同 key + 不同 payload：返回 409，拒绝把两个意思混成一次请求；
- 表单内容改变：前端生成新的 key，允许创建新的 Run。

重放响应包含 `X-Idempotent-Replay: true`。这叫**幂等**：同一个意图重复提交，
最终效果仍然只有一次。

## 5. SSE 为什么能断线恢复

SSE 是 Server-Sent Events：浏览器建立一条由服务端持续向客户端发送文字事件的
HTTP 连接。它比 WebSocket 简单，适合“服务端报告进度，浏览器主要负责查看”的
场景。

本项目的关键不是 SSE 本身，而是 **durable Event 在前，SSE 在后**：

```text
Worker 提交状态
  -> 同一数据库事务追加 Event(sequence=N)
  -> SSE 查询 SQLite 中 sequence > cursor 的 Event
  -> 浏览器按 sequence 排序和去重
```

恢复规则：

1. 首次打开 Trace 时，HTTP API 回放已有 Event；
2. SSE 使用 `after` 或 `Last-Event-ID` 告诉后端最后看到的 sequence；
3. 后端先补发更大的 sequence，再继续等待新 Event；
4. 浏览器合并 HTTP 和 SSE 数据，并按 sequence 去重；
5. 空闲 15 秒发送注释 heartbeat，但不写一条假的领域 Event；
6. Run 到 `succeeded / failed / cancelled` 后，补完事件并关闭连接。

非终态 Trace 页面还每四秒做一次兜底刷新。SSE 短时间内连续到达的 Event 会合并
成一次刷新，已有请求未完成时也不会重复发起；终态 Run 只做一次读取，不再开启
SSE 或持续轮询。SSE 暂时失败只会把连接标签变成 `reconnecting`，不会让任务
重跑，也不会让数据库状态消失。

## 6. 代码模块地图

### 后端

| 文件 | 作用 |
| --- | --- |
| `backend/src/epiphany/models.py` | Project、ProjectSource 与 Run 归属字段 |
| `backend/alembic/versions/0005_project_workspace.py` | 把已有数据库升级到 Project schema |
| `backend/src/epiphany/project_service.py` | Project CRUD 与 Source 关联 |
| `backend/src/epiphany/project_api.py` | Project/Source/Run HTTP 路由 |
| `backend/src/epiphany/services.py` | Project Run 幂等、Source 范围和 Revision lineage |
| `backend/src/epiphany/event_stream.py` | Event replay、heartbeat 与终态关闭 |
| `backend/src/epiphany/api.py` | Run 列表、Trace 与 SSE endpoint |

### 前端

| 文件 | 作用 |
| --- | --- |
| `frontend/src/features/projects/ProjectsPage.tsx` | 创建和列出 Project |
| `frontend/src/features/projects/ProjectWorkspacePage.tsx` | Source 库、Run 配置与历史 |
| `frontend/src/features/sources/SourceImporter.tsx` | 粘贴或读取 TXT/Markdown |
| `frontend/src/features/runs/CreateRunForm.tsx` | Creative Brief 和幂等 Run 创建 |
| `frontend/src/features/runs/RunTracePage.tsx` | Trace、输出和人工动作 |
| `frontend/src/features/runs/TracePanels.tsx` | Event、Task、Artifact、ModelCall 展示 |
| `frontend/src/features/runs/RunActions.tsx` | Resume、反馈、Revision 和补充采访回答 |
| `frontend/src/lib/events.ts` | Event sequence 排序、合并和去重 |
| `frontend/src/api/client.ts` | `/api` 请求、request ID 和错误封装 |

## 7. 第一次迁移与启动

### 7.1 后端

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn epiphany.main:app --reload
```

`alembic upgrade head` 不能跳过。已有数据库需要通过 migration 增加
`projects`、`project_sources` 和 Run 的 Project/幂等字段。应用启动不会偷偷
调用 `create_all()` 修表。

检查：

- <http://127.0.0.1:8000/health>
- <http://127.0.0.1:8000/docs>

### 7.2 前端

另开一个终端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>。Vite 把 `/api` 请求代理到本机 8000 端口，
因此本地不需要另写 CORS 配置。

## 8. 用页面完整走一遍

### 第一步：创建 Project

在 `/projects` 填名称和一句说明。创建后进入 Project 工作区。刷新页面，Project
仍应存在；这证明它来自数据库，不是临时前端状态。

### 第二步：导入 Source

点击“添加素材”，选择类型并粘贴正文；也可以读取 `.txt`、`.md` 或
`.markdown` 文件。文件只是在浏览器里读成文字，确认提交后才导入。

导入后可以打开 Source 详情，查看稳定段落顺序。再次导入相同正文应提示复用，
不应增加第二份副本。

### 第三步：创建 Run

至少选择一份事实素材，填写主题和 10/15/30 分钟目标。展开高级配置后可以填写：

- 表达场景；
- 面向听众；
- 沟通目标；
- 最多三项语气；
- 必须包含和避免模式；
- 可选 style-only 写作样本。

同一 Source 不能同时作为事实与风格样本。点击“启动 Agent Run”后进入 Trace。

### 第四步：看 Trace，而不是只看 loading

页面顶部显示 Run 状态和 SSE 连接状态。三个标签分别展示：

- Trace：按 sequence 排列的 durable Event；
- Tasks：每个工作单的状态、attempt 和错误；
- Artifacts：研究、Scaffold、Draft 和报告的结构化结果。

模型调用表展示 provider、model、tokens、耗时、估算费用、币种与错误码。

### 第五步：处理人工检查点

当 Run 为 `waiting_for_user`，页面出现补充口述表单。这里仍然输入**已经转成
文字**的内容，不会申请麦克风权限。提交会先创建 Project Source，再用它的 ID
Resume Run。

### 第六步：检查输出和下一步

Run 成功后可以查看：

- Interview Scaffold Markdown；
- Podcast Draft Markdown；
- Show Notes Markdown；
- Draft Quality Report。

页面还允许提交真实评分，或显式创建 Revision 子 Run。若存在 draft-aware
补充采访 Plan，可以回答真正唤起记忆的问题，再用回答 Source 创建下一版。

## 9. 日志、数据库与排错

### 9.1 从页面错误开始

API 错误会保留 HTTP status、detail 和后端返回的 `X-Request-ID`。先复制这个
request ID，再去 Uvicorn 终端搜索；不要只根据页面文案猜原因。

继续记录：

- `project_id`
- `source_id`
- `run_id`
- `task_id`
- `artifact_id`
- `model_call_id`
- Event `sequence`

日志只应含 ID、状态、数量和耗时，不应打印 Source 正文、Prompt、模型完整输出
或 API Key。

### 9.2 直接验证 SSE

```bash
curl -N 'http://127.0.0.1:8000/runs/run_替换成真实ID/events/stream?after=0'
```

如果 Run 正在等待用户，约 15 秒会看到 `: heartbeat N`。它只是传输保活，不是
数据库 Event。终态 Run 应在补完事件后自行结束命令。

### 9.3 查看 SQLite

默认数据库：

```text
backend/data/epiphany.db
```

只读打开：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
sqlite3 -readonly data/epiphany.db
```

常用查询：

```sql
SELECT id, title, created_at FROM projects ORDER BY created_at DESC;
SELECT project_id, source_id FROM project_sources;
SELECT id, project_id, parent_run_id, status, current_step FROM runs;
SELECT run_id, sequence, type FROM events ORDER BY run_id, sequence;
SELECT run_id, provider, model, status, input_tokens, output_tokens
FROM model_calls ORDER BY started_at;
```

Source、Artifact 和 Task input 可能含个人文字或模型输出。不要把查询结果贴进
公开 Issue、日志、截图或 Git。数据库细节见
[SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)。

### 9.4 推荐排错顺序

1. 确认 5173 页面和 8000 健康检查都可访问；
2. 看页面 HTTP status、detail、request ID；
3. 刷新页面，确认问题是否来自可恢复的前端临时状态；
4. 查看 Run 的失败 Task 和 `error_code`；
5. 按 sequence 回放 Event，找最后一个成功步骤；
6. 用 ID 搜索 JSON 日志；
7. 必要时只读查询 SQLite；
8. 用最小定向测试复现，再跑完整测试。

## 10. 自动化验证

后端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate
pytest tests/test_projects.py tests/test_event_stream.py tests/test_runtime.py -q
pytest -q
alembic check
```

前端：

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/frontend
npm test
npm run build
```

2026-07-31 的本地证据是：

- 363 个后端测试通过；
- 15 个前端测试通过；
- 前端 production build 通过；
- Alembic clean upgrade、check、降到 `0004_run_lineage` 再升回
  `0005_project_workspace` 通过。

测试覆盖的关键行为：

- Project/Source 创建、去重、重新启动后仍可查询；
- Project Run 双击/重试幂等与冲突；
- 跨 Project 的事实/风格 Source 拒绝；
- Revision 子 Run 继承 Project；
- SSE sequence 顺序、replay、heartbeat、断线与终态关闭；
- 前端 Event 合并去重、checkpoint 判断、Run payload 隔离；
- API request ID 与可恢复错误。

## 11. 真实浏览器 E2E 证据（2026-07-31）

这次不是 Swagger 或测试客户端，而是在 Vite 本地页面从浏览器实际点击完成。
为便于排错，证据只保留缩短后的 Project/Run ID，不记录 Source 正文：

- 通过 UI 创建 Project `proj_4ef9…`；
- 导入一份 587 字、5 Segments 的日记和一份 335 字、3 Segments 的写作样本；
- 同一 Source 的“事实 / 仅参考风格”角色在表单中互斥，避免事实与风格通道混用；
- 选择写作样本后，必须分别确认内容使用权和模型处理同意，Run 按钮才会启用；
  更换样本会清空旧授权，前端不会代替用户静默同意；
- 创建 Run `run_69d3…`，Trace 可见 v8 的两个 Researcher fan-out、确定性
  fan-in、Interviewer 和 material-readiness 人工检查点；
- 在等待点累计提交四份补充 Source；最新 readiness 从
  `available=78 / missing=2302` 变为 `available=2403 / missing=0`；
- 随后 Editor 与 Reviewer 完成，Run 最终 `succeeded`，共 6 Tasks、5 次
  Fake ModelCall、19 Artifacts 和 64 Events；
- 页面可以打开 Draft、Show Notes 和质量报告；
- 确定性质量规则正确阻止一篇约 1.6/10 分钟的 Fake 短稿被当作合格终稿。

这证明 Project 持久化、页面角色选择、Event Trace、多轮 Resume、Editor、
Reviewer 与输出读取可以在真实浏览器中串成一条链。它**不证明内容质量已经
通过**：Fake Provider 用来验证状态、引用、恢复和 UI 合约，不能替代真实模型
内容验收，更不能替代用户判断“像不像我、愿不愿意录”。

第一次 E2E 暴露了两个 UI 问题，并在同一阶段修复后重新用浏览器验证：

1. 人工检查点现在直接展示 readiness 的当前/最低/缺少字符数、估算可支持时长、
   具体 gap 和六个基于 Scaffold 的追问；页面不会显示内部 Source/Segment ID；
2. SSE Event 到达时的刷新会合并并避免 in-flight 重复；终态 Run 不再建立 SSE，
   derived 输出每个 Run 只读取一次，且只有 v9 才读取 supplemental Plan。

最后一次浏览器复验还覆盖了写作样本授权门禁：未授权时按钮禁用，两项授权均
确认后启用，取消并重新选择样本后两项授权恢复为未选中；页面控制台无错误或
警告。

复验 Run `run_dc87…` 在页面停于 `waiting_for_user`，显示
`78 / 2,380 / 2,302` 字、`0.24–0.32` 分钟和具体问题；连接随后进入 `live`。
另一终态 v8 Run 的全新浏览器会话只读取 Run、Events、Quality 和 Improvement，
没有 supplemental 409、favicon 404、持续轮询或控制台错误。

另一个需要保留的产品/研究口径是：当前 initial readiness 只统计 Scaffold
**实际引用到的初始 Segments**，不是用户选中 Source 的完整 587 字。因此一份
被选中的 Source 可能只贡献其中一小部分。这个策略有利于防止“素材很多但与
脚手架无关”时虚报充足度，却也可能因 Scaffold 覆盖不足而低估已有材料；本次
只把它记录为明确 caveat，不宣称已经修复。

## 12. 当前边界

已经完成：

- 本地 Project/Source 页面；
- 本地 Run Trace 页面；
- 可重放 SSE；
- 人工检查点、输出查看、反馈和显式 Revision 的页面入口。

仍未完成：

- 可视化 Scaffold / Draft 编辑器；
- Dockerfile 与生产部署；
- 登录、权限、多人协作；
- 数据库自动备份与恢复演练；
- 麦克风录音、音频上传、STT；
- TTS 与授权 voice cloning。

所以这一阶段的准确名称是**本地开发 Console**，不是“已经上线的产品”。下一步
应该先用真实个人素材走一遍 UI，记录具体摩擦，再决定优先做 Scaffold 编辑器、
本地打包，还是 Docker/单机部署，而不是一次把所有平台能力补齐。
