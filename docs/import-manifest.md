# JSON 导入清单 v1.0

`POST /api/projects/{project_id}/imports` 接收 JSON 对象。先创建项目和任务，
再将清单中的 `task_id` 替换成该项目已有的任务 ID。

| 字段 | 规则 |
| --- | --- |
| schema_version | 固定字符串 `"1.0"` |
| source | 必填、非空的来源名称，仅作标识，不作为服务器文件路径 |
| episodes | 1 至 1000 条操作记录 |
| episodes[].id | 必填 UUID，清单内不可重复，已导入的 ID 不可覆盖 |
| episodes[].task_id | 当前项目已有任务的 UUID |
| episodes[].device_id | 必填、非空的设备标识 |
| episodes[].started_at / ended_at | 带时区的 ISO 8601 字符串，结束时间必须晚于开始时间 |
| episodes[].outcome | `pending`（默认）、`success` 或 `failure` |
| episodes[].source_type | 必填：`synthetic`（合成）或 `collected`（采集） |
| episodes[].assets | 至少一个文件引用，同一记录内路径不可重复 |
| assets[].path | 相对清单所在目录的 POSIX 路径 |
| assets[].asset_type | `video` 对应 `.mp4`，`state` 对应 `.csv`，`metadata` 对应 `.json` |

所有层级拒绝未知字段；非空文本去除首尾空白。时间写入数据库时统一为 UTC，
不接受数值形式的 Unix 时间戳。状态 CSV 的 `timestamp_s` 以秒为单位，
相对记录开始时间；关节角以弧度为单位。序列内容、时间范围检查在质量检查阶段实现。

路径使用 `/` 分隔，不接受绝对路径、反斜线、冒号、控制字符、空路径段、
`.` 或 `..`。路径按字面使用，不做 URL 解码。
后续文件导入必须解析真实路径并确认仍在来源目录内，包括符号链接的目标。

目前接口在单个事务中保存批次、操作记录和文件引用。任一记录格式错误、
任务不属于当前项目或 ID 已存在时，整批拒绝，不留下部分记录。
成功返回 `201`，包含批次 `id`、`project_id`、`source`、`imported_at`、
`episode_ids` 和 `asset_count`；项目不存在返回 `404`，ID 冲突返回 `409`，
清单或任务归属错误返回 `422`。

本阶段不读取或复制文件，不检查文件存在性。文件引用中的 `size_bytes` 和
`checksum` 保持空值；实际文件保存和校验值记录在 6.2 实现。
因此导入成功只代表元数据与引用已保存。

## 合成样例

[`examples/synthetic-manifest.json`](../examples/synthetic-manifest.json)
及其关联的状态 CSV 是人工构造的格式样例，不是设备采集数据。
样例任务 ID 是占位 UUID，需要替换；再次导入前需为新记录生成新的 UUID。

在 `backend` 目录可独立校验样例：

```sh
uv run python -c 'from pathlib import Path; from app.import_manifest import ImportManifest; manifest = ImportManifest.model_validate_json(Path("../examples/synthetic-manifest.json").read_text()); print(f"Valid manifest: {len(manifest.episodes)} episode")'
```
