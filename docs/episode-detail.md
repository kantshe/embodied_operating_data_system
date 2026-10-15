# 操作记录详情、质量检查与标注

记录列表中的 ID 可打开详情，地址为 `#project=<项目 ID>&episode=<记录 ID>`。
同一页面返回列表时保留筛选和页码；刷新浏览器后列表条件恢复默认值。
详情显示任务、批次、设备、起止时间、结果、失败原因、数据来源和关联文件。
保存标注后刷新列表中的结果与筛选；原页无记录时回退到前一页。

## 接口

| 接口 | 行为 |
| --- | --- |
| `GET /api/projects/{project_id}/episodes/{episode_id}` | 返回记录及当前文件状态、实际大小、CSV 状态摘要 |
| `GET /api/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}/content` | 读取该记录关联的文件，视频支持单段字节范围请求 |
| `POST /api/projects/{project_id}/episodes/{episode_id}/quality-check` | 检查字段、文件、时间与序列，保存并返回报告 |
| `GET /api/projects/{project_id}/episodes/{episode_id}/quality-reports` | 查询检查历史 |
| `GET /api/projects/{project_id}/episodes/{episode_id}/quality-reports/{report_id}` | 查询单份报告及问题明细 |
| `PATCH /api/projects/{project_id}/episodes/{episode_id}/annotation` | 修改结果和失败原因 |
| `GET /api/projects/{project_id}/episodes/{episode_id}/annotations` | 查询标注修改历史 |

所有接口均限制为当前项目所属记录，文件和报告还要求属于当前记录，
否则返回 `404`。不接受通过 URL 传入文件路径。
读取限制在配置的存储目录，逐级拒绝符号链接、目录及其他非普通文件。
字节范围请求成功返回 `206`，无效或多段范围返回 `416`。

## 文件状态与摘要

文件状态为 `available`、`missing`、`unreadable` 或 `invalid_path`。
详情返回登记的大小与 SHA-256，同时重新读取实际大小；未登记或无法读取时显示未知。
此处不重新计算 SHA-256，也不据此判断文件内容是否发生变化。
视频由浏览器播放并读取时长，无法解码或浏览器不支持时显示未知，不推断视频内容。

CSV 摘要接受 UTF-8（含 BOM），返回字段名称、数据行数和前 5 行。
最多读取 2 MiB，超过时返回 `too_large` 且行数为 `null`；
最多统计 10000 行，再有数据则 `truncated=true`，行数代表下限。
预览的每个单元格截取前 256 字符。空文件、非法编码、空或重复列名、
超过 64 列、字段数量不匹配、CSV 解析错误返回 `invalid`，行数为 `null`。
只有表头的 CSV 行数为 0。

## 质量检查范围

规则版本 `fields-files-time-v2` 检查 `id`、`task_id`、`device_id`、
`started_at`、`ended_at`、`outcome`、`source_type`、`created_at` 是否缺失或空白，
并检查结果与来源枚举值。至少要求一个关联文件，检查文件路径和类型字段、
类型枚举、文件存在性、普通文件与可读取状态以及空文件。
不强制每条记录同时具备视频和状态数据。
结果为 `failure` 时还要求非空失败原因。

起止时间必须为带时区的 ISO 8601 时间，转换到 UTC 后结束时间必须晚于开始时间。
状态文件按 UTF-8 CSV 读取，要求存在 `timestamp_s` 列，表示相对开始时间的秒数。
每个时间戳必须为有限数值、非负、严格递增且不超过记录时长，允许零与结束时刻。
数值支持十进制与科学计数法；拒绝空值、NaN、Infinity 和下划线形式。
时间范围无效时仍检查序列格式和递增性，但无法判定相对时长上界。
空序列、非法编码、重复或空列名、超过 64 列、行宽不一致及 CSV 解析错误均记为问题。

质量检查独立于摘要预览，最多读取每个状态文件 16 MiB、100000 条数据行。
超出限制记为 `sequence_limit`；最多保留 200 个问题，再发现问题时附加 `issue_limit` 并停止。
检查期间已打开文件的大小或修改时间发生变化时记录 `file_changed`。
这些情况返回 `complete=false`，不会判为通过；可在问题明细中看到未完成原因。

返回 `id`、`episode_id`、`rule_version`、`checked_at`、`passed`、`complete` 与 `issues`；
每个问题包含 `code`、`field`、`asset_id`（记录级问题为 `null`）。
能定位到 CSV 行时还包含 `row`，从 1 开始计数，包含表头和空行。
`passed` 仅在没有任何问题时为真，不代表视频可解码、物理状态正确或文件校验值匹配。

每次检查都会向 `quality_reports` 写入新报告，保存成功才返回结果。
检查时的记录元数据在事务内保持一致；文件不是快照，不保证检查后的文件状态。
历史报告保存当时的规则、问题与时间，不会因修改标注或文件而更新，需要重新运行检查。
报告列表按检查时间倒序；相同时间按写入顺序倒序。

## 人工标注

请求示例：

```json
{
  "outcome": "failure",
  "failure_reason": "抓取位置偏移",
  "expected_version": 0
}
```

`outcome` 为 `pending`、`success` 或 `failure`。
失败原因去除首尾空白，最长 2000 字符，失败时必填；其他结果只能传空值或省略。
从失败改为其他结果会清除原失败原因，并记入修改历史。未知字段返回 `422`。

`expected_version` 必须为详情返回的 `annotation_version`，类型为非负整数。
版本冲突返回 `409`，不覆盖已有标注。页面保留输入，提供放弃输入并载入最新标注的入口。
成功响应包含 `id`、`outcome`、`failure_reason`、`annotation_version` 和 `changed`。
实际值未变化时 `changed=false`，不增加版本或历史。
修改结果、版本与历史在同一事务内保存，任意一步失败全部回滚。

每个变化字段保存一条 `annotation_revisions`，包含 `field_name`、`previous_value`、
`current_value`、`changed_at`、`revision`；同一次保存的字段共享版本号和时间。
历史按版本、时间及写入顺序倒序。当前没有用户身份系统，不记录虚构的操作人。

两类历史接口均接受 `limit`（1–100，默认 20）与 `offset`（非负整数），返回数组。
页面每页展示 10 条，支持上一页、下一页、加载失败重试和空状态。
应用启动会补齐旧数据库缺少的标注字段；既有记录、报告和历史保留，旧标注版本初始化为 0。
