# 操作记录详情与基础检查

记录列表中的 ID 可打开详情，地址为 `#project=<项目 ID>&episode=<记录 ID>`。
同一页面返回列表时保留筛选和页码；刷新浏览器后列表条件恢复默认值。
详情显示任务、批次、设备、起止时间、结果、数据来源和关联文件。

## 接口

| 接口 | 行为 |
| --- | --- |
| `GET /api/projects/{project_id}/episodes/{episode_id}` | 返回记录及当前文件状态、实际大小、CSV 状态摘要 |
| `GET /api/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}/content` | 读取该记录关联的文件，视频支持单段字节范围请求 |
| `POST /api/projects/{project_id}/episodes/{episode_id}/quality-check` | 运行必填字段与关联文件检查，返回本次结果 |

三类接口均限制为当前项目所属记录，文件接口还要求资产属于当前记录，
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

## 基础检查范围

规则版本 `required-files-v1` 检查 `id`、`task_id`、`device_id`、
`started_at`、`ended_at`、`outcome`、`source_type`、`created_at` 是否缺失或空白，
并检查结果与来源枚举值。至少要求一个关联文件，检查文件路径和类型字段、
类型枚举、文件存在性、普通文件与可读取状态以及空文件。
不强制每条记录同时具备视频和状态数据。

返回 `episode_id`、`rule_version`、`checked_at`、`passed` 与 `issues`；
每个问题包含 `code`、`field`、`asset_id`（记录级问题为 `null`）。
`passed` 只代表上述基础项通过，不代表 CSV 内容或视频可解码，
也不代表时间范围与序列时间戳合格。

本阶段结果仅用于本次展示，不写入 `quality_reports`；
时间及序列校验在 8.2 实现，报告保存与历史查询在 8.3 实现。
