# M2.1：Source 与来源引用

日期：2026-07-23

对应 commit：`0f2b46b feat(sources): add persistent source contract`

## 1. 这一步要解决什么

M1 的 Run 可以可靠执行，但它还不知道“素材”是什么。Fake Workflow 的
payload 只是一段普通 JSON，无法做到：

- 保留原始文字；
- 稳定切分长内容；
- 精确指出结论来自哪一段；
- 对重复导入安全去重；
- 重启后继续查询。

对于日记和播客项目，如果 AI 只输出漂亮总结，却无法回到用户原话，这个
产品就失去了最重要的可信度。

## 2. 生活化类比

Source 像一本书，SourceSegment 像带固定页码和段落编号的摘录卡。

只说“我记得书里好像讲过”不够。系统需要记录：

- 来自哪本书；
- 来自哪一段；
- 这段文字在全文中的位置；
- 内容有没有变化。

## 3. 两个领域对象

### Source

代表一份完整素材，例如：

- 一篇日记；
- 一版播客稿；
- 一段语音转录；
- 一篇旧文章。

保存：

- 标题和类型；
- 规范化后的全文；
- SHA-256 内容 hash；
- 字符数；
- metadata；
- 创建和更新时间。

### SourceSegment

代表 Source 中一段稳定、可引用的文字，保存：

- 所属 Source；
- 片段顺序；
- 片段原文；
- 全文中的字符起止位置；
- 片段 hash；
- 稳定 Segment ID。

## 4. 为什么需要规范化

两段看起来相同的文字，底层字符可能不同：

- Windows 换行是 `\r\n`；
- macOS/Linux 常见换行是 `\n`；
- Unicode 同一个字可能有不同组合形式；
- 全文前后可能多出空格。

系统先执行：

1. Unicode NFC；
2. 将 `\r\n` 和 `\r` 统一为 `\n`；
3. 去掉全文首尾空白；
4. 再计算 hash 和分段。

这样重复导入不会因为无意义格式差异变成两份 Source。

## 5. 分段规则

当前采用确定性规则：

1. 优先按空行分段；
2. 保持原始顺序；
3. 每段保留精确字符区间；
4. 超过 1200 字符的长段落优先在标点或空格附近继续拆分；
5. 相同输入永远产生相同片段和 ID。

为什么暂时不用模型分段：

- 确定性规则免费；
- 容易测试；
- 不会每次运行产生不同边界；
- 先满足引用需求；
- 未来有真实质量证据再升级。

## 6. 稳定 ID 和去重

Source ID 来自规范化全文 hash。

Segment ID 来自：

- Source hash；
- 片段 position；
- 片段 hash。

数据库还有唯一约束作为最后一道防线。

同一内容重复导入：

- 第一次返回 HTTP 201 和 `created: true`；
- 后续返回 HTTP 200 和 `created: false`；
- Source 和 Segment ID 不变。

并发的两个请求即使同时认为“还没有”，唯一约束也会保证最终只有一份，
失败的一方重新读取已经提交的 Source。

## 7. SourceReference

Agent 将来输出的引用结构只允许：

```json
{
  "source_id": "src_...",
  "source_segment_id": "seg_..."
}
```

不允许在引用对象里混入：

- 模型自己改写的“原话”；
- 没有定义的自由字段；
- 只有 Source 没有 Segment 的模糊引用。

它只是引用地址。展示时再根据 ID 回到本地 SourceSegment。

## 8. API

M2.1 增加：

```text
POST /sources
GET  /sources
GET  /sources/{source_id}
```

列表只返回摘要。详情返回有序 segments，但不额外返回一份完整
`content_text`，避免无必要地扩大数据暴露。

## 9. 数据库 Migration

Alembic revision：

```text
0002_source_contract
```

它创建 `sources` 和 `source_segments`，以及：

- 外键；
- content hash 唯一约束；
- Source 内 position 唯一约束；
- 查询索引。

## 10. 开发中发现的 Schema Drift

实现过程中曾发现：

- 应用启动时的 `metadata.create_all()` 已经创建新表；
- 但 Alembic revision 仍停留在 `0001`；
- 数据库“看起来有表”，迁移历史却不知道。

这叫 schema drift：实际结构与 migration 历史不一致。

修复后规则是：

- Alembic 是正常开发和运行时唯一结构变更入口；
- 应用启动默认不执行 `create_all()`；
- `create_all()` 只用于隔离的临时测试库；
- 拉取代码后先运行 `alembic upgrade head`。

这是一个很重要的后端经验：表能用不等于数据库版本管理正确。

## 11. 代码模块地图

| 文件 | 作用 |
| --- | --- |
| `models.py` | Source 和 SourceSegment 数据模型 |
| `schemas.py` | API Schema 和 SourceReference |
| `source_segmentation.py` | 规范化、分段、hash 和稳定 ID |
| `source_service.py` | 导入、去重、读取和列表业务 |
| `source_api.py` | Source HTTP API |
| `alembic/versions/0002_...` | 数据库升级脚本 |
| `tests/test_source_segmentation.py` | 纯分段规则测试 |
| `tests/test_sources.py` | API、去重、并发和重启测试 |

## 12. 怎样测试

```bash
cd /Users/mac/Documents/wise_project/epiphany-studio/backend
source .venv/bin/activate

pytest tests/test_source_segmentation.py tests/test_sources.py -vv
alembic current
alembic check
```

重点验证：

- 换行和 Unicode 规范化；
- 稳定分段和字符区间；
- 长段落边界；
- 重复导入；
- 并发导入；
- API 列表和详情；
- 重启后查询；
- strict SourceReference。

## 13. 本地手动验证

启动：

```bash
alembic upgrade head
uvicorn epiphany.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>，执行 `POST /sources`：

```json
{
  "title": "分段测试",
  "source_type": "podcast_draft",
  "text": "第一段测试素材。\n\n第二段测试素材。",
  "metadata": {
    "purpose": "manual_test"
  }
}
```

然后：

1. 查询 `GET /sources/{source_id}`；
2. 检查有两个按 position 排序的 Segment；
3. 再次导入相同文本；
4. 检查返回相同 ID 和 `created: false`；
5. 重启 Uvicorn 后再次查询。

## 14. 日志与隐私

Source 日志只记录：

- `source_id`
- `source_type`
- `char_count`
- `segment_count`
- 导入或去重 event

不会记录 Source 正文。个人日记、录音和本地数据库也不能提交 Git。

## 15. 这一步学到了什么

- 领域对象应该先于复杂 Agent prompt；
- 来源可追踪需要稳定片段，不只是文件名；
- 确定性预处理更容易测试和缓存；
- hash 与数据库唯一约束共同实现幂等；
- 并发写入必须考虑竞争条件；
- Alembic 历史必须与真实数据库结构一致；
- 隐私边界需要落实到日志和 API 返回，而不只是写在说明里。

## 16. 当时还缺少什么

M2.1 只建立“可以被引用的素材”，还没有：

- Timeline/Theme 输出；
- 校验 Agent 是否越权引用；
- 并行 Child Task；
- fan-in；
- 真实模型。

M2.2 在这个 Source 契约之上建立第一条父子 Agent 工作流。
