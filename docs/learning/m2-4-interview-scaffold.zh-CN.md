# M2.4：从研究结果生成采访脚手架

## 基本信息

- 阶段：M2.4 Interview Scaffold
- 日期：2026-07-28
- Commit：`81b150d`、`d30f68f`
- 状态：Fake、DeepSeek 适配、严格校验、Markdown 导出与自动化测试已完成

## 1. 为什么做这一步

M2.2 和 M2.3 已经能把素材并行整理成时间线与主题，但研究包仍像两叠给编辑
看的资料。它没有告诉本人从哪里开场、按什么顺序回忆、应该追问什么，以及
还缺哪些素材。

M2.4 新增一个 Interviewer。它只在两个 Researcher 都完成并合并以后运行，
把已经验证过的研究结果整理成半开放的采访脚手架：

- 节目主题与意图；
- 开场和收束；
- 2 至 6 个采访段落；
- 每段的已知背景、过渡、问题、追问目的和关键词；
- 还需要本人补充的素材缺口；
- 每一处内容对应的 Source 与 SourceSegment 引用。

它是“帮助继续口述的提问地图”，不是替本人写完的播客稿。

## 2. 生活化类比

可以把流程想成一次纪录片采访的准备会。

两位研究员先同时工作：一位排时间线，一位找主题和原话。主编等两份材料
都到齐后装订成研究包。接着，采访主持人才拿着这份研究包，按主题设计开场、
转场和问题。

```text
Timeline Researcher --\
                       -> fan-in -> Research Bundle -> Interviewer -> Scaffold
Theme Researcher ----/
```

前半段并行，节省等待时间；后半段串行，因为 Interviewer 必须依赖完整、
已经校验的研究包。类比的局限是：真实主持人会临场判断，而当前系统只生成
静态脚手架，还没有等待用户回答或在线编辑。

## 3. 完成了什么

一次成功的 `episode-research` v2 Run 会留下：

| 数量 | 内容 | 是否调用模型 |
| --- | --- | --- |
| 4 Tasks | `research_manager`、`timeline_research`、`theme_research`、`build_interview_scaffold` | Manager 否，其余 3 个是 |
| 4 Artifacts | Timeline 结果、Theme 结果、`episode_research_bundle`、`build_interview_scaffold_result` | Bundle 是确定性合并 |
| 3 ModelCalls | Timeline、Theme、Interviewer 各一次成功 attempt | Fake 默认全部零 Token、零费用 |

执行顺序是：

```text
POST /runs
  -> research_manager fan-out
  -> Timeline 与 Theme 并行
  -> fan-in 生成 episode_research_bundle
  -> 排队并串行执行 build_interview_scaffold
  -> 脚手架严格校验通过
  -> Run succeeded，最终 output_artifact_id 指向脚手架
```

专用 API：

```text
GET /runs/{run_id}/exports/interview-scaffold.md
```

会把成功的最终 Artifact 导出为可下载的 UTF-8 Markdown。Run 不存在时返回
404；尚未完成、最终 Artifact 类型不对或内容已损坏时返回 409。

## 4. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `backend/src/epiphany/interview_schemas.py` | 定义 Interviewer 输入、脚手架严格 Schema 和引用白名单 |
| `backend/src/epiphany/runtime/interview_prompts.py` | 把有界研究包变成 Interviewer Prompt |
| `backend/src/epiphany/runtime/orchestrator.py` | fan-in 后排队 Interviewer，并在完成后结束 Run |
| `backend/src/epiphany/runtime/output_validation.py` | 在 Artifact 落库前分派严格校验 |
| `backend/src/epiphany/runtime/providers/fake.py` | 默认零费用地生成确定性测试脚手架 |
| `backend/src/epiphany/runtime/providers/deepseek.py` | 让同一 Task 契约也能由 DeepSeek 执行 |
| `backend/src/epiphany/interview_markdown.py` | 确定性、安全地渲染 Markdown |
| `backend/src/epiphany/services.py`、`api.py` | 检查最终 Artifact 并提供导出 API |

## 5. 关键技术点

### 5.1 严格引用不是“有一个来源字段”就够了

Interviewer 只能看到已通过研究校验的 Timeline 与 Theme 内容。系统先从
研究包收集允许的 `(source_id, source_segment_id)`，再检查脚手架中的节目
意图、开场、收束、段落、已知背景、过渡、问题和素材缺口。

任何引用不在研究包白名单中，Task 都以
`invalid_scaffold_source_reference` 失败，Run 也不会把该输出作为最终
Artifact。标题还必须与请求中的 `topic` 完全一致；额外字段、空文本、
少于两个段落和重复空关键词也会被拒绝。

这一级不会重新接受模型凭空引入的新证据。Researcher 层此前还会检查 quote
是否逐字存在于原 SourceSegment；两层校验共同维持从脚手架回到原始素材的
证据链。

### 5.2 Markdown 导出为什么还要做安全处理

模型生成的文字属于不可信数据。如果直接拼进 Markdown，它可以把 `#` 变成
新标题、把 `[文字](网址)` 变成链接，或插入原始 HTML。

导出器会先按严格 Schema 重新验证最终 Artifact，移除仅供运行时追踪的
`_execution` 元数据，再转义 HTML 和 Markdown 控制字符。文档结构由程序
固定生成。正文来源显示为 `[S1]` 等短标签，文末再按“《Source 标题》片段
N”列出索引；原始 `source_id/source_segment_id` 仍保存在 Artifact 和数据库。
因此相同输入得到相同输出，模型文字不能偷偷创造新结构或远程图片。引用
无法解析到对应 Source/Segment 元数据时，导出返回 409。

### 5.3 v2 为什么不能直接改变旧 Run

新建 `episode-research` 会保存 `workflow_version="v2"`，并要求 payload
同时提供 `topic` 与 `source_ids`。但部署新代码时，数据库里可能已有正在
执行的 v1 Run，而且它的旧 payload 没有 `topic`。

Orchestrator 会按 Run 自己保存的版本继续执行：

- v2：fan-in 后再运行 Interviewer；
- v1：仍在 `episode_research_bundle` 处成功结束，不补造新 Task。

这叫在途兼容。它不是把历史 Run 偷偷升级，也不改写旧 Artifact。

### 5.4 为什么没有数据库 migration

M2.4 复用了既有的 Run、Task、Artifact、Event 和 ModelCall 表。新 Task 类型、
Artifact 类型、`workflow_version="v2"` 和 JSON 内容都能放进原有字段，因此
Alembic 仍是 `0003_model_call_trace (head)`，没有新增 migration。

“没有 migration”不是省略检查；仍应运行 `alembic check`，证明 ORM 模型与
现有 revision 没有差异。

## 6. 自动化测试

从 `backend` 运行：

```bash
source .venv/bin/activate
pytest -q
ruff check src tests
ruff format --check src tests
alembic check
```

当前全量基线是：

```text
99 passed
```

M2.4 的主要定向测试：

```bash
pytest tests/test_research_workflow.py \
       tests/test_interview_scaffold.py \
       tests/test_interview_export_api.py \
       tests/test_deepseek_research_workflow.py -vv
```

它们覆盖 4 Tasks / 4 Artifacts / 3 ModelCalls、两个 Researcher 确实并行、
Interviewer 必须晚于 fan-in、v1 在途兼容、严格 Schema 与越权引用失败、
模型调用上限在 Provider 前拦截、Markdown 确定性与注入转义、导出 API 的
200/404/409，以及 DeepSeek Mock 完整链路。普通 pytest 不联网、不读取 Key、
不产生费用。

M2.2 的 28 项、M2.3a 的 32 项、M2.3b 的 83 项是当时的历史基线，不应被
99 项的当前基线反向改写。M2.3b 已完成的 live smoke 也仍是两次调用、总计
2301 tokens 的历史验收；M2.4 当前 harness 已扩为最多三次调用，但尚未把
一次新的付费运行写成历史事实。

## 7. 本地 Swagger 手动验证

1. 在 `backend` 执行 `alembic upgrade head`，再运行
   `uvicorn epiphany.main:app --reload`。
2. 打开 <http://127.0.0.1:8000/docs>。
3. 用 `POST /sources` 导入不含隐私的合成文字，复制 `source.id`。
4. 用 `POST /runs` 创建：

```json
{
  "workflow_type": "episode-research",
  "payload": {
    "topic": "五年后重新开始录播客",
    "source_ids": ["src_替换成上一步返回的ID"]
  }
}
```

5. 用 `GET /runs/{run_id}` 反复查询，直到看到 `status="succeeded"`、
   `workflow_version="v2"`、4 个 Task、4 个 Artifact、3 条 ModelCall，
   且最终 Artifact 为 `build_interview_scaffold_result`。
6. 用 `GET /runs/{run_id}/events` 确认
   `workflow.fan_in.completed` 早于
   `workflow.interview_scaffold.queued`，最后出现
   `workflow.interview_scaffold.completed` 和 `run.succeeded`。
7. 调用 `GET /runs/{run_id}/exports/interview-scaffold.md`。成功响应应为
   200、`text/markdown; charset=utf-8`，并带有附件文件名；正文包含段落、
   问题和 `[S1]` 等来源标签，文末包含 `## 来源索引` 和 Source 标题/片段
   位置，不直接暴露原始 `src_...#seg_...`。

默认 `EPIPHANY_MODEL_PROVIDER=fake`，所以这三条 ModelCall 会留下真实的
attempt 与耗时 Trace，但 Token 和费用均为 0。

## 8. 日志与排错

重点事件：

```text
workflow.fan_out.started
workflow.fan_in.waiting
workflow.fan_in.completed
workflow.interview_scaffold.queued
workflow.interview_scaffold.completed
run.interview_scaffold_markdown.exported
```

用 `request_id`、`run_id`、`task_id`、`artifact_id` 和 `model_call_id`
关联排查。完成事件会记录 `section_count`、`question_count`；导出日志只记录
`markdown_char_count`，不会记录素材、Prompt 或 Markdown 正文。

常见错误：

- `invalid_scaffold_source_reference`：脚手架引用超出研究包；
- `interview_scaffold_schema_invalid`：字段、数量或文本不符合严格 Schema；
- `interview_scaffold_title_topic_mismatch`：输出标题与请求 topic 不一致；
- `model_call_limit_exceeded`：两次 Researcher 调用后没有为 Interviewer
  留出第三次预算；
- 导出 409：先确认 Run 成功且 `output_artifact_id` 指向合法脚手架。

## 9. 限制与下一步

M2.4 已能生成和下载采访脚手架，但还没有：

- `waiting_for_user` 状态；
- 在页面中回答问题或编辑脚手架的 editor；
- 面向普通用户的 Web UI；
- 根据用户补充内容继续运行；
- 生成完整播客稿；
- 用个人素材评价真实模型内容质量。

下一步应让用户真正参与脚手架的补充和确认，而不是把“已生成 Artifact”
误写成“完整采访体验已经完成”。

## 完成检查

- [x] 正常与失败路径自动化测试通过
- [x] 严格引用与 Markdown 安全导出通过
- [x] v2 新流程与 v1 在途兼容通过
- [x] Fake 默认零费用
- [x] 无新 migration，Alembic 无差异
- [x] Swagger、日志与排错步骤已记录
- [x] 当前限制已明确
