# ADR-0001：第一版使用自建轻量编排器

日期：2026-07-23

状态：Accepted

## 背景

项目既要快速验证个人创作工作流，又希望让作者亲手学习 Agent 编排、
后端、任务状态、恢复和部署。

候选方案包括：

1. LangGraph；
2. Temporal；
3. Celery/Redis；
4. 直接复用 OpenCode Runtime；
5. 在普通后端代码上实现受限的一层任务编排。

## 决策

MVP 采用方案 5：

- Python + FastAPI；
- SQLite 持久化 Run/Task/Event；
- 单 Worker；
- `asyncio` 有界并发；
- 代码定义的固定 Workflow；
- 官方 OpenAI SDK；
- 一层、allowlist 管理的 Subagent；
- SSE 事件展示。

## 原因

### 更快验证产品

第一阶段的未知数是：半开放采访脚手架是否真的帮助用户回忆和表达。
大框架不会减少这个产品风险。

### 保留学习价值

自己实现小范围状态机、fan-out/fan-in、重试、恢复、取消和幂等，能直接
理解 Agent Runtime 的组成，而不是只学习框架 API。

### 容易调试

个人项目的 Run 数量小。一个数据库、一个 Worker 和显式状态表足以观察
完整行为。

### 可演进

领域对象和产品事件不依赖框架内部 checkpoint。未来迁移到 PostgreSQL、
LangGraph 或 Temporal 时，产品数据模型仍可保留。

## 代价

- 需要自己实现状态转换和恢复测试；
- 不具备现成的分布式调度；
- 初期只支持单 Worker；
- 需要避免逐步造出一个没有边界的自制框架。

## 约束

- 不实现通用 DAG 编辑器；
- 不支持递归 Subagent；
- 不追求 exactly-once；
- 不提前实现多租户、公平队列或容器沙箱；
- 每新增一种 Workflow，只抽取真实重复的部分；
- 引入新的大型框架前必须新增 ADR，并给出当前方案无法满足的具体证据。

## 复审条件

当以下任一条件出现时复审：

- 需要多个 Worker 并发领取任务；
- Workflow 分支、暂停点和恢复逻辑明显失控；
- 本地进程恢复无法满足已部署产品的可靠性要求；
- 用户量或任务时长使 SQLite/单 Worker 成为测得的瓶颈。
