# M5.1c《Obsession》Bear 视角真实浏览器 E2E 素材

这组 fixture 用于一次真实 React UI + Playwright + DeepSeek 的内容生产验收。测试者
模拟一位希望从 Bear 视角写心理日记的用户，完整操作 Project、Source、Writing
Sample、Creative Brief、两个材料检查点、初稿、质量报告和显式 Revision。

> **内容警告：**以下材料包含完整剧透，以及强迫关系、谋杀、动物伤害、药物过量
> 与自杀相关情节。合成的第一人称段落也会讨论身体自主权丧失和死亡。

## 版权与事实边界

- 电影名、人物和剧情锚点仅来自链接中的公开简介、主创采访、媒体报道与评论/结局
  解析；不同公开资料存在差异时，本目录保留归属与不确定性；
- 这是基于上述公开资料制作的转换性合成测试，不复制电影剧本、长段对白或逐镜复述；
- 三篇 Bear 日记与两篇补充口述是转换性合成创作，明确标为非电影正史；
- Writing Sample 完全原创且只用于声音，不得成为电影事实证据；
- 最终只能评估与已核对公开资料和主创解释的一致性，不声称替代观看影片，也不构成
  片方、主创或权利人的官方认可；
- 电影及角色相关权利归各自权利人所有；本目录不对其使用是否构成法律意义上的
  `fair use` 作出结论；
- 电影有意保持 Nikki 在愿望前是否喜欢 Bear 的歧义，模型不得替她下结论。

## 预期用户旅程

1. 创建 Project，按 manifest 导入 5 份事实/合成创作 Source 与 1 份 Writing Sample；
2. 创建 15 分钟 `conversational_diary` Run；
3. 在两个人工检查点分别导入 `supplemental-01` 和 `supplemental-02`；
4. 查看采访脚手架、口播候选、Show Notes、质量报告和所有 Run trace；
5. 若时长不足且仍有高价值未利用素材，执行一次 `reuse_unused_material`；
6. 若仍不足，回答系统依据当前稿提出的具体问题，再创建显式 child Revision；
7. 从用户视角评价“像不像 Bear、是否愿意录、是否把控制浪漫化”；
8. 从平台管理者视角核对 SQLite 中的 Project、Source、Run、Task、Artifact、Event、
   ModelCall、Token、费用、parent/child lineage 与失败/重试信息。

## 2026-08-04 真实运行结果

- Project：`proj_7c2ce1ae62ad4507823baf633a30ba85`；
- 主 lineage 从 `run_2ff8fa47a5174253ab7384e2e1688305` 经过两轮补充采访与
  三轮成功 Revision，最终到
  `run_76ce45e7d4634830b66f6259b19c2d49`；
- 最终候选稿：4,055 个非空白口播字符，估算 14.48 分钟，24/24 段有来源，
  6 Sources / 31 Segments，完全重复段落为 0；
- 最终 Run 的 2 次 DeepSeek V4 Pro 调用共 63,684 tokens，估算 ¥0.213138 CNY；
- 整个 Project（含失败预检、重试与完整 lineage）共 31 次模型调用、373,020
  tokens，估算 ¥1.067259 CNY；
- 人工反馈确认 Bear 死后全知问题已被受控 Revision 修复，但仍发现一句元编辑话语
  跳出 Bear 人设，以及 3 处模板化对比句，因此最终仍为
  `revision_recommended`，不宣称可以直接录制。

可读结果见 [最终候选稿](results/final-podcast-draft.md)。完整用户旅程、平台审计、
失败记录和产品结论见
[实验报告](../../../../docs/experiments/m5-1c-obsession-bear-browser-e2e.zh-CN.md)。
