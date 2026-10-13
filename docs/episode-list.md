# 操作记录列表

`GET /api/projects/{project_id}/episodes` 只返回该项目的记录，按创建时间、
记录 ID 倒序。项目不存在返回 `404`，无匹配记录返回空数组。

| 参数 | 规则 |
| --- | --- |
| limit | 默认 20，范围 1 至 100 |
| offset | 默认 0，非负整数 |
| task_id | 任务 ID，精确匹配；其他项目的任务不会返回记录 |
| device_id | 设备标识，精确匹配 |
| outcome | `pending`、`success` 或 `failure` |
| started_from | 操作开始时间下限，带时区的 ISO 8601 时间，包含边界 |
| started_to | 操作开始时间上限，带时区的 ISO 8601 时间，包含边界 |

所有筛选条件取交集。时间按实际时刻比较，接受不同的时区偏移；
缺失开始时间的历史记录不匹配时间筛选。下限晚于上限或非法参数返回 `422`。
返回记录包含 `id`、`task_id`、`task_name`、`import_batch_id`、`device_id`、
`started_at`、`ended_at`、`outcome`、`source_type` 和 `created_at`。

项目详情的“操作记录”视图每页显示 20 条。时间输入按浏览器所在时区解释，
发送前转为 UTC；点击筛选或重置后返回第一页。
