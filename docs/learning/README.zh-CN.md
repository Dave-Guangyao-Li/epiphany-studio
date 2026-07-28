# Epiphany Studio 学习实践手册

更新时间：2026-07-28

## 这套手册解决什么问题

Epiphany Studio 不只是一个等待 AI 帮忙完成的产品，也是一个用来亲手
理解全栈开发、Agent 编排和生产可靠性的学习项目。

代码回答“系统现在怎么运行”，这套手册回答：

- 这一步为什么要做；
- 它解决了什么真实问题；
- 新增了哪些模块，各自负责什么；
- 背后的技术概念是什么；
- 怎样自己运行、测试和观察；
- 出错时从哪里开始排查；
- 这一步完成后，项目真正具备了什么能力。

目标是即使几个月后重新回来，也能通过手册快速恢复上下文，而不是只看到
一堆陌生代码和 commit。

## 它和其他文档的区别

| 文档 | 主要回答的问题 | 目标读者 |
| --- | --- | --- |
| `product-spec` | 产品要解决什么问题 | 产品与设计 |
| `architecture` | 系统整体如何设计 | 工程设计 |
| `roadmap` | 下一步按什么顺序建设 | 项目规划 |
| `devlog` | 哪天完成了哪些事实 | 工程维护 |
| 本学习手册 | 为什么这样做，我怎样亲手验证 | 未来的自己和初学者 |

## 阅读顺序

第一次阅读建议按以下顺序：

1. [常见术语表](glossary.zh-CN.md)
2. [本地运行、测试与调试](local-development.zh-CN.md)
3. [M0：项目起点与架构选择](m0-foundation.zh-CN.md)
4. [M1：持久化 Agent Runtime](m1-durable-runtime.zh-CN.md)
5. [可观测性：如何知道系统发生了什么](observability.zh-CN.md)
6. [M2.1：Source 与来源引用](m2-1-source-contract.zh-CN.md)
7. [M2.2：双 Agent 并行编排](m2-2-parallel-agents.zh-CN.md)
8. [M2.3a：零费用模型调用 Trace](m2-3a-model-call-trace.zh-CN.md)
9. [M2.3b：DeepSeek Provider](m2-3b-deepseek-provider.zh-CN.md)

## 当前进度

| 阶段 | 用普通话描述 | 状态 | 对应 commit |
| --- | --- | --- | --- |
| M0 | 建立独立项目，明确产品和技术边界 | 完成 | `a37e23c` |
| M1 | 后端可以持久化并执行三步假工作流 | 完成 | `65f6046` |
| 开发流程 | 确立小步、可测试、及时提交的规则 | 完成 | `b0b07e7` |
| 可观测性 | 请求、任务和错误可以通过 ID 与日志追踪 | 完成 | `0f5dd5a` |
| M2.1 | 可以导入、分段并稳定引用文字素材 | 完成 | `0f2b46b` |
| M2.2 | Manager 可以并行调度两个研究 Child Task | 完成 | `8e4306a` |
| M2.3a | 在真实调用前记录预算、尝试、耗时与费用 | 完成 | `4d48c90` |
| M2.3b-1 | DeepSeek 接口、Prompt、错误和费用可离线验证 | Mock 已验证 | `046358c` |
| M2.3b-2a | 受限真实调用命令、dry-run 与脱敏输出 | 离线已验证 | 本次 focused commit |
| M2.3b-2b | 使用短合成素材执行两次真实调用 | 待执行 | — |

## 当前系统已经能做什么

目前可以完成一条不调用大模型的后端验证链路：

```text
导入一段测试文字
  -> 保存 Source 和 SourceSegment
  -> 创建 episode-research Run
  -> Manager 分发两个 Child Task
  -> Timeline / Theme Fake Researcher 并行执行
  -> 严格校验结构和来源引用
  -> 合并为 episode_research_bundle
  -> 从 Run、Task、Artifact、Event 和日志中复盘全过程
```

M2.3a 让每次 Provider attempt 产生持久化调用记录，并在调用前执行单 Run
预算。M2.3b-1 又把 DeepSeek 的 HTTP、Prompt、JSON、错误和费用接到这条链路，
M2.3b-2a 提供默认不联网、必须显式 `--execute` 的两次调用命令。默认仍是
Fake，尚未执行小额 live smoke。

## 当前还不能做什么

- DeepSeek 适配器和受限 smoke 命令已完成，但尚未执行真实 API smoke；
- 尚未生成真正有质量的时间线或主题分析；
- 尚未生成采访脚手架和播客稿；
- 尚未进入 `waiting_for_user`；
- 尚未提供普通用户使用的 Web UI；
- 尚未实现 SSE 实时 Trace 页面；
- 尚未部署到线上。

当前用于人工验证的界面是 FastAPI 自动生成的 Swagger 页面，而不是最终
产品界面。

## 每个后续步骤如何记录

从下一步开始，每个开发切片必须更新对应章节，或按
[学习记录模板](entry-template.zh-CN.md) 新建章节。每一条记录至少包含：

1. 问题与目标；
2. 非技术类比；
3. 完成的功能；
4. 代码模块地图；
5. 技术原理；
6. 自动化测试；
7. 本地手动验证；
8. 日志与排错入口；
9. 取舍、限制和未完成项；
10. commit 与下一步。

学习记录应与实现处于同一个 commit，避免文档永远落后于代码。
