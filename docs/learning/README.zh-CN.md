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
10. [M2.4：从研究结果生成采访脚手架](m2-4-interview-scaffold.zh-CN.md)
11. [M3.1：可持久化的人工暂停与恢复](m3-1-human-checkpoint.zh-CN.md)
12. [SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)

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
| M2.3b-2a | 受限真实调用命令、dry-run 与脱敏输出 | 离线已验证 | `fd232e0` |
| M2.3b-2b | 使用短合成素材执行两次真实调用 | 完成 | 本次 focused commit |
| M2.3b-3 | DeepSeek 费用可显式按 USD/CNY 估算与分组 | 完成 | 本次 focused commit |
| M2.4 | fan-in 后串行生成并安全导出采访脚手架 | 完成 | `81b150d`、`d30f68f` |
| M3.1 | 生成脚手架后持久化等待用户，并用补充 Source 幂等恢复 | 完成 | 本次 focused commit |

## 当前系统已经能做什么

默认 Swagger 和本地开发路径继续使用零费用 Fake Provider，可以完成：

```text
导入一段测试文字
  -> 保存 Source 和 SourceSegment
  -> 用 topic 和 source_ids 创建 episode-research v3 Run
  -> Manager 分发两个 Child Task
  -> Timeline / Theme Fake Researcher 并行执行
  -> 严格校验结构和来源引用
  -> 合并为 episode_research_bundle
  -> 串行 Interviewer 生成 build_interview_scaffold_result
  -> 严格校验脚手架内每一处来源引用
  -> 持久化停在 waiting_for_user
  -> 通过 API 安全导出带引用的 Markdown
  -> 用户把补充口述文字导入为新 Source
  -> 用 Source ID 幂等 Resume
  -> 从 Run、Task、Artifact、Event 和日志中复盘全过程
```

M2.3a 让每次 Provider attempt 产生持久化调用记录，并在调用前执行单 Run
预算。M2.3b-1 又把 DeepSeek 的 HTTP、Prompt、JSON、错误和费用接到这条链路，
M2.3b-2a 提供默认不联网、必须显式 `--execute` 的两次调用命令。默认仍是
Fake；M2.3b-2b 已用短合成素材完成两次真实调用，并通过严格引用校验与
fan-in。真实 Trace 保存在独立 SQLite 中，查看方法见
[SQLite 数据与排查指南](sqlite-data-guide.zh-CN.md)。M2.3b-3 支持显式选择
USD 或 CNY 价格表；默认 USD 保持兼容，不自动猜测账户币种，也不改写历史
记录。M2.4 把研究结果继续变成可使用的采访脚手架：正常 Fake Run 最终有
4 个 Task、4 个 Artifact 和 3 个 ModelCall；Manager 只负责编排，不调用
模型。M2.4 当时的新建 `episode-research` 使用 v2，已在该阶段开始前运行的
v1 Run 仍按旧流程在 research bundle 处结束。M2.4 没有修改数据库结构，
因此没有新增 migration。M3.1 将新建 Run 升级为 v3：脚手架完成后保留 4 个 Task、
4 个 Artifact 和 3 个 ModelCall，但状态停在 `waiting_for_user`，且等待
期间仍可导出脚手架。用户通过 `POST /sources` 导入一段已经转成文字的补充
口述，再调用 `POST /runs/{run_id}/resume`。首次 Resume 新增一个只保存
Source / SourceSegment 引用的 `user_material_submission` Artifact，随后
确定性结束 Run；Task 仍为 4 个，Artifact 变成 5 个，ModelCall 仍为 3 个。
相同 submission 可安全重放，重启和并发路径也有自动化测试。M3.1 同样没有
修改数据库结构；v1 与 v2 在途 Run 保持原有结束方式。

这里的 `voice_note_transcript` 是“已经转成文字的口述”这一 Source 分类，
不是麦克风或语音识别功能。当前可以在 Swagger 中直接输入或粘贴文字。

## 当前还不能做什么

- DeepSeek 真实 API 已通过合成素材 smoke，但尚未用个人素材评价内容质量；
- 已能生成采访脚手架，但还不是完整播客稿；
- 尚未提供采访脚手架 editor；
- 尚未根据补充 Source 运行新的 Editor Agent；
- 尚未提供麦克风录音、音频上传、STT 或语音克隆；
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
