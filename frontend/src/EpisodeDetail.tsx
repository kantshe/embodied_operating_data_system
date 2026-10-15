import { useEffect, useState } from "react";
import { AnnotationEditor, QualityReview, type Annotation } from "./EpisodeReview";

type Summary = {
  status: string; columns: string[]; row_count: number | null;
  preview: string[][]; truncated: boolean;
};
type Asset = {
  id: string; path: string; asset_type: string; size_bytes: number | null;
  actual_size_bytes: number | null; checksum: string | null;
  status: string; state_summary: Summary | null;
};
type Detail = Annotation & {
  id: string; task_id: string; task_name: string; import_batch_id: string | null;
  device_id: string | null; started_at: string | null; ended_at: string | null;
  outcome: string; source_type: string; created_at: string; assets: Asset[];
};
const labels: Record<string, string> = {
  video: "视频", state: "状态数据", metadata: "元数据",
  available: "可读取", missing: "文件缺失", unreadable: "无法读取", invalid_path: "路径无效或不是普通文件",
  pending: "待确认", success: "成功", failure: "失败", synthetic: "合成", collected: "采集",
  required_field: "必填字段缺失", required_assets: "未关联文件", invalid_value: "字段值无效",
  file_missing: "文件缺失", file_unreadable: "文件无法读取",
  file_invalid_path: "路径无效或不是普通文件", empty_file: "文件为空",
  device_id: "设备", started_at: "开始时间", ended_at: "结束时间",
  created_at: "创建时间", source_type: "数据来源", outcome: "结果", assets: "关联文件",
  path: "文件路径", asset_type: "文件类型", task_id: "任务 ID", id: "记录 ID",
};

export function EpisodeDetail({ projectId, episodeId, onChanged }: { projectId: string; episodeId: string; onChanged: () => void }) {
  const url = `/api/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}`;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [status, setStatus] = useState("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    async function load() {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (response.status === 404) { if (!controller.signal.aborted) setStatus("missing"); return; }
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!data || data.id !== episodeId || typeof data.task_name !== "string" ||
          !Array.isArray(data.assets) || !data.assets.every((asset: Asset) =>
            asset && typeof asset.id === "string" && typeof asset.path === "string" &&
            typeof asset.status === "string" && typeof asset.asset_type === "string" &&
            (asset.state_summary === null || (Array.isArray(asset.state_summary?.columns) &&
              Array.isArray(asset.state_summary?.preview)))
          )) throw new Error();
        if (!controller.signal.aborted) { setDetail(data); setStatus("ready"); }
      } catch { if (!controller.signal.aborted) setStatus("error"); }
    }
    void load();
    return () => controller.abort();
  }, [url, episodeId, attempt]);

  return <section aria-label="记录详情">
    <a className="back-link" href={`#project=${encodeURIComponent(projectId)}`}>返回操作记录</a>
    {status !== "ready" || !detail ? <div className="detail-status" role="status">
      {status === "missing" ? "记录不存在或不属于当前项目" : status === "error" ? "详情加载失败" : "加载中…"}
      {status === "error" && <button className="retry" onClick={() => setAttempt((value) => value + 1)}>重试详情</button>}
    </div> : <>
      <div className="content-header"><h2>记录详情</h2>
        <button onClick={() => setAttempt((value) => value + 1)}>刷新详情</button>
      </div>
      <dl className="project-info episode-info">
        <dt>记录 ID</dt><dd>{detail.id}</dd>
        <dt>任务</dt><dd>{detail.task_name}（{detail.task_id}）</dd>
        <dt>批次 ID</dt><dd>{detail.import_batch_id || "—"}</dd>
        <dt>设备</dt><dd>{detail.device_id || "—"}</dd>
        <dt>开始时间</dt><dd>{formatDate(detail.started_at)}</dd>
        <dt>结束时间</dt><dd>{formatDate(detail.ended_at)}</dd>
        <dt>结果</dt><dd>{labels[detail.outcome] || detail.outcome || "—"}</dd>
        {detail.failure_reason && <><dt>失败原因</dt><dd>{detail.failure_reason}</dd></>}
        <dt>数据来源</dt><dd>{labels[detail.source_type] || detail.source_type || "—"}</dd>
        <dt>创建时间</dt><dd>{formatDate(detail.created_at)}</dd>
      </dl>
      <AnnotationEditor url={url} value={detail} onSaved={(value) => {
        setDetail({ ...detail, outcome: value.outcome, failure_reason: value.failure_reason, annotation_version: value.annotation_version });
        onChanged();
      }} />
      <QualityReview url={url} assets={detail.assets} />
      <section aria-label="关联文件" className="detail-section">
        <h2>关联文件</h2>
        {!detail.assets.length && <p role="status">暂无关联文件</p>}
        {detail.assets.map((asset) => <article key={asset.id} className="asset-item">
          <h3>{labels[asset.asset_type] || asset.asset_type}</h3>
          <dl className="project-info">
            <dt>文件路径</dt><dd>{asset.path}</dd>
            <dt>文件状态</dt><dd>{labels[asset.status] || asset.status}</dd>
            <dt>文件大小</dt><dd>{asset.actual_size_bytes === null ? "未知" : `${asset.actual_size_bytes} 字节`}（登记：{asset.size_bytes === null ? "未知" : `${asset.size_bytes} 字节`}）</dd>
            <dt>SHA-256</dt><dd>{asset.checksum || "未登记"}</dd>
          </dl>
          {asset.status === "available" && <>
            {asset.asset_type === "video" && <VideoPreview url={`${url}/assets/${encodeURIComponent(asset.id)}/content`} />}
            {asset.asset_type === "state" && asset.state_summary && <StatePreview summary={asset.state_summary} />}
          </>}
        </article>)}
      </section>
    </>}
  </section>;
}

function VideoPreview({ url }: { url: string }) {
  const [status, setStatus] = useState("loading");
  const [duration, setDuration] = useState<number | null>(null);
  return <div>
    <video className="episode-video" aria-label="操作视频" controls preload="metadata" src={url}
      onLoadedMetadata={(event) => {
        const value = event.currentTarget.duration;
        setDuration(Number.isFinite(value) ? value : null); setStatus("ready");
      }}
      onError={() => { setStatus("error"); setDuration(null); }} />
    <p role="status">{status === "error" ? "视频无法播放或浏览器不支持，时长未知" :
      status === "loading" ? "正在读取视频信息…" : `视频时长：${duration === null ? "未知" : `${duration.toFixed(2)} 秒`}`}</p>
  </div>;
}

function StatePreview({ summary }: { summary: Summary }) {
  if (summary.status !== "ready") return <p role="status">{
    summary.status === "too_large" ? "状态文件超过 2 MiB，摘要未读取" : "状态文件格式无效或不是 UTF-8 CSV，摘要未知"
  }</p>;
  return <>
    <p>数据行数：{summary.truncated ? "至少 " : ""}{summary.row_count}；字段数：{summary.columns.length}</p>
    {!summary.row_count && <p role="status">状态文件没有数据行</p>}
    <div className="table-scroll" role="region" aria-label="状态数据预览" tabIndex={0}><table className="state-preview">
      <thead><tr>{summary.columns.map((name, index) => <th key={index} scope="col">{name}</th>)}</tr></thead>
      <tbody>{summary.preview.map((row, index) => <tr key={index}>{row.map((value, column) => <td key={column}>{value}</td>)}</tr>)}</tbody>
    </table></div>
  </>;
}

function formatDate(value: string | null) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : value || "—";
}
