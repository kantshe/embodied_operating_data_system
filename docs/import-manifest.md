# JSON 导入清单 v1.0

`POST /api/projects/{project_id}/imports` 接收 JSON 对象。先创建项目和任务，
再将清单中的 `task_id` 替换成该项目已有的任务 ID。

| 字段 | 规则 |
| --- | --- |
| schema_version | 固定字符串 `"1.0"` |
| source | 必填、非空的来源名称，仅作标识，不作为服务器文件路径 |
| episodes | 1 至 1000 条操作记录 |
| episodes[].id | 必填 UUID，用于判重，已有记录不可覆盖 |
| episodes[].task_id | 当前项目已有任务的 UUID |
| episodes[].device_id | 必填、非空的设备标识 |
| episodes[].started_at / ended_at | 带时区的 ISO 8601 字符串，结束时间必须晚于开始时间 |
| episodes[].outcome | `pending`（默认）、`success` 或 `failure` |
| episodes[].source_type | 必填：`synthetic`（合成）或 `collected`（采集） |
| episodes[].assets | 至少一个文件引用，同一记录内路径不可重复 |
| assets[].path | 相对后端配置的来源目录的 POSIX 路径 |
| assets[].asset_type | `video` 对应 `.mp4`，`state` 对应 `.csv`，`metadata` 对应 `.json` |

所有层级拒绝未知字段；非空文本去除首尾空白。时间写入数据库时统一为 UTC，
不接受数值形式的 Unix 时间戳。状态 CSV 的 `timestamp_s` 以秒为单位，
相对记录开始时间；关节角以弧度为单位。导入不检查序列内容，详情页的[质量检查](episode-detail.md)会验证时间戳是否为有限数值、非负、严格递增且位于记录时长内。

路径使用 `/` 分隔，不接受绝对路径、反斜线、冒号、控制字符、空路径段、
`.` 或 `..`。路径按字面使用，不做 URL 解码。
读取时逐级打开目录，拒绝文件和目录符号链接，仅接受普通文件。

格式错误（包括未知字段、非法时间或路径）仍整批返回 `422`。
格式有效的记录按清单顺序逐条处理，任务不属于当前项目、文件缺失或复制失败时，
该条标为 `failed`，清理该条副本，其他记录继续处理。
同一项目已有的 UUID 标为 `skipped`，清单内部重复同样处理；UUID 属于其他项目时
标为 `failed`。判重只比较 UUID，不比较文件内容；同一 UUID 即使附带不同内容
也不会覆盖原记录，不同 UUID 的相同文件仍会分别保存。

成功返回 `201`，包含批次 `id`、`project_id`、`source`、`imported_at`、
`episode_ids`（本次实际导入的 ID）、`asset_count`（本次保存的文件数）和 `results`。
`results` 按原清单顺序返回 `index`（从 0 开始）、`episode_id`、
`status`（`imported`、`skipped`、`failed`）与 `detail`（成功时为 `null`）。
批次和结果会保存到数据库，即使全部跳过或失败也返回 `201`，
因此判断逐条成功必须查看 `results`。
项目不存在返回 `404`，数据库约束冲突返回 `409`。

## 来源与文件保存

默认来源目录是 `backend/data/incoming`，来源名称 `source` 仅作标识，不拼接为路径。
调用 `create_app(database_path, source_root=..., storage_root=...)` 可配置来源目录和
存储目录；默认存储目录是数据库同级的 `assets`。
提交前将关联文件放入来源目录，保留清单中的相对目录结构。
服务按 `<批次 UUID>/<清单序号>/<文件 UUID>.<扩展名>` 保存副本，原文件保持不变。
数据库 `assets.path` 相对存储目录，`size_bytes` 为实际字节数，
`checksum` 是副本内容的 SHA-256 十六进制值（64 个字符）。

数据库使用一个写事务；数据库错误时回滚整批记录并清理本次已写副本。
复制异常时仅清理该条副本。进程被强制终止或掉电时可能留下未登记副本，
本阶段没有实现崩溃后的孤立文件清理。已有元数据导入记录不会自动补齐文件；
后续新增记录正常写入大小和校验值。

## 页面入口

项目详情页的“导入”视图可选择 JSON 文件或直接填写清单，显示批次 ID、
导入与跳过及失败数量、保存的文件数和逐条原因。请求失败时保留清单以便重试；
成功处理批次后刷新该项目的操作记录列表。
此入口提交清单，不上传关联文件，关联文件仍需提前放入后端来源目录。

## 合成样例

[`examples/synthetic-manifest.json`](../examples/synthetic-manifest.json)
及其关联的状态 CSV 是人工构造的格式样例，不是设备采集数据。
样例任务 ID 是占位 UUID，需要替换；同一 UUID 再次提交会跳过已导入记录。
使用默认来源目录时，将 `examples/episodes` 复制至 `backend/data/incoming/episodes`。

在 `backend` 目录可独立校验样例：

```sh
uv run python -c 'from pathlib import Path; from app.import_manifest import ImportManifest; manifest = ImportManifest.model_validate_json(Path("../examples/synthetic-manifest.json").read_text()); print(f"Valid manifest: {len(manifest.episodes)} episode")'
```
