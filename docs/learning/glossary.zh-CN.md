# 常见术语表

这份术语表优先解释“它在 Epiphany Studio 中具体指什么”，不追求教科书式
定义。

## 前端、后端与 API

### 前端（Frontend）

用户直接看到和操作的页面。最终会包括素材导入、Run 状态、Agent Trace
和文稿编辑页面。

项目目前还没有正式前端。开发阶段通过 Swagger API 页面操作后端。

### 后端（Backend）

接收请求、校验数据、执行业务规则、保存状态和调度任务的程序。当前
`backend/src/epiphany` 下的 Python 代码就是后端。

### API

前端或其他程序与后端约定好的通信入口。例如：

- `POST /sources`：导入素材；
- `POST /runs`：创建一次工作流；
- `GET /runs/{run_id}`：查询运行结果。

可以把 API 理解成餐厅的点菜单：用户不需要进入厨房，只要按照菜单规定
提交信息。

## 数据与持久化

### 数据库（Database）

系统长期保存结构化数据的地方。本项目当前使用 SQLite，一个本地文件就是
一套数据库。

### 持久化（Persistence）

程序关闭或电脑重启以后，数据仍然存在。Run 如果只放在 Python 内存里，
进程一关就消失；存入 SQLite 后才能恢复和查询。

### Schema

数据的结构约定。它规定“必须有哪些字段、字段是什么类型、哪些字段不允许
出现”。

本项目有两类常见 Schema：

- 数据库 Schema：表、字段、外键和唯一约束；
- Pydantic Schema：API 请求和 Agent 输出的 JSON 结构。

### Migration

数据库结构的版本升级脚本。例如新增 `sources` 表时，需要让已经存在的
数据库安全升级，而不是删库重建。本项目使用 Alembic 管理 migration。

## 一次 Agent 运行中的对象

### Run

一次完整业务目标。例如“根据这些素材完成一期节目的双研究任务”是一条
Run。

### Task

Run 内部的一项具体工作。例如 Timeline Researcher 和 Theme Researcher
分别是一条 Task。

### Artifact

Task 产生并保存下来的结果，例如时间线候选、主题候选或合并后的研究包。
可以把它理解成可追踪的“交付物”。

### Event

记录 Run 发生过什么的持久化事件，例如：

- `run.created`
- `task.started`
- `task.succeeded`
- `workflow.fan_in.completed`

Event 存在数据库中，因此重启后仍能回放。

### State Machine（状态机）

明确规定状态可以怎样变化。例如：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

状态机能阻止“已经成功的任务又回到运行中”之类的错误。

## Agent 编排

### Orchestrator（编排器）

决定“现在应该创建什么 Task、接下来允许执行哪一步、什么时候整体成功或
失败”的确定性后端代码。

它不是大模型。当前实现在
`backend/src/epiphany/runtime/orchestrator.py`。

### Manager

一条负责协调 Child Task 的父任务。当前 `research_manager` 不调用模型，
它代表这一组研究任务的整体状态。

### Child Task / Subagent

由 Manager 创建、目标和权限受限制的子任务。

在本项目中，Subagent 不是独立服务器，也不是可以无限递归创造其他 Agent
的自由智能体。它就是一条有 `parent_task_id`、输入范围、输出 Schema、
重试次数和 lease 的持久化 Task。

### Fan-out（扇出）

把一个父任务拆成多个可以独立进行的子任务。

生活类比：编辑把同一批采访材料分别交给两位研究员：

- 一位只整理时间线；
- 一位只找主题和原话。

从一条工作分成两条，所以像扇子展开，叫 fan-out。

```text
                  -> Timeline Researcher
research_manager
                  -> Theme Researcher
```

Fan-out 本身只代表“分支”。是否真的同时执行，还取决于 Worker 的并发
实现。M2.2 使用 `asyncio.gather`，测试也证明两个 Provider 调用在时间上
发生了重叠。

### Fan-in（扇入）

等待所有必需的分支完成，再把结果汇合回一条主线。

生活类比：编辑不能只收到时间线就交稿。她要等待主题研究也完成，检查两份
材料都合格，然后装进同一个研究包。

```text
Timeline result
                -> research_manager -> research bundle
Theme result
```

本项目当前的 fan-in 不是让第三个 AI 自由总结。它是普通后端代码执行的
确定性操作：

1. 查询两个 Child Task 的状态；
2. 没全部成功就继续等待；
3. 全部成功后读取两份 Artifact；
4. 创建一个幂等的 `episode_research_bundle`；
5. 将 Manager 和 Run 标记为成功。

### Bounded Concurrency（有界并发）

允许多个任务同时执行，但设置明确上限。当前上限为 2。

这样既能缩短等待时间，也能避免无限创建 Agent、失控花费 API 费用或给
数据库造成过大压力。

### Fan-out / Fan-in 与普通顺序执行的区别

顺序执行：

```text
Timeline 完成 -> Theme 开始 -> 合并
```

并行 fan-out/fan-in：

```text
Timeline 开始 ─┐
               ├─ 全部完成后合并
Theme 开始 ────┘
```

如果两个模型调用各需要 10 秒，顺序执行大约需要 20 秒，并行执行理想情况
接近 10 秒。

## Worker 与可靠性

### Worker

不断从数据库寻找 `queued` Task、领取任务、执行 Provider、保存 Artifact
和更新状态的后台循环。

### Provider

把“需要生成一个结构化结果”转换成具体模型调用的适配层。

`FakeProvider` 返回确定性测试数据，不联网、不收费。M2.3a 让 Fake 调用
经过和真实模型相同的预算、耗时与用量记录边界；M2.3b 增加
`DeepSeekProvider`，负责 HTTP、认证、JSON、usage、费用和错误映射。默认
仍是 Fake，只有显式配置后才允许真实网络请求。

### Lease

Worker 领取任务时拿到的一张有过期时间的“工作证”。提交结果时必须仍然
持有正确的 lease。

### Fencing（隔离迟到写入）

如果 Task 已取消、lease 已过期或任务被其他执行者重新领取，旧执行者即使
晚一点返回结果，也不能覆盖新状态。

### Idempotency（幂等）

同一个操作因为重试被执行多次，最终仍然只产生一份有效结果。例如同一个
Task 的 Artifact 使用稳定 idempotency key，避免网络重试生成重复交付物。

### Retry

遇到暂时性错误时重新尝试，但必须有次数上限。业务校验失败不是暂时性
错误，不应盲目重试。

## 可观测性

### Log（日志）

写到程序 stdout 的运行诊断信息，用来回答“什么时候发生了错误、哪个
请求慢、哪个 Worker 领取了任务”。

### Event 与 Log 的区别

- Event 是产品执行历史，存数据库，可长期回放；
- Log 是运行诊断轨迹，主要给开发和运维排错。

二者不能互相替代。

### Request ID

一次 HTTP 请求的关联编号。后端通过 `X-Request-ID` 接收和返回它。看到
错误时保留这个 ID，就能在日志中找到同一次请求。
