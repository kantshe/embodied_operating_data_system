import { useEffect, useState, type FormEvent, type ReactNode } from "react";

export type Annotation = { outcome: string; failure_reason: string | null; annotation_version: number };
type Issue = { code: string; field: string; asset_id: string | null; row?: number };
type Report = { id: string; checked_at: string; rule_version: string; passed: boolean; complete: boolean; issues: Issue[] };
type Revision = { id: string; revision: number; changed_at: string; field_name: string; previous_value: string | null; current_value: string | null };
const labels: Record<string, string> = {
  pending: "待确认", success: "成功", failure: "失败", outcome: "结果", failure_reason: "失败原因",
  required_field: "必填字段缺失", required_assets: "未关联文件", invalid_value: "字段值无效",
  file_missing: "文件缺失", file_unreadable: "文件无法读取", file_invalid_path: "路径无效或不是普通文件",
  empty_file: "文件为空", invalid_datetime: "时间格式无效或缺少时区", invalid_time_range: "结束时间必须晚于开始时间",
  invalid_csv: "CSV 格式无效", missing_timestamp: "缺少 timestamp_s 列", invalid_timestamp: "时间戳不是有限数值",
  negative_timestamp: "时间戳小于零", timestamp_not_increasing: "时间戳未严格递增",
  timestamp_out_of_range: "时间戳超出记录时长", empty_sequence: "序列没有数据行",
  sequence_limit: "序列超过检查上限", issue_limit: "问题数量超过检查上限", file_changed: "检查期间文件发生变化",
  device_id: "设备", started_at: "开始时间", ended_at: "结束时间", created_at: "创建时间",
  source_type: "数据来源", assets: "关联文件", path: "文件路径", asset_type: "文件类型",
  task_id: "任务 ID", id: "记录 ID", timestamp_s: "序列时间戳", checks: "检查",
};
const label = (value: string | null) => value === null ? "—" : labels[value] || value;
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const result = (report: Report) => !report.complete ? "检查未完成" : report.passed ? "通过" : `发现 ${report.issues.length} 个问题`;

function History<T extends { id: string }>({ url, title, empty, render }: {
  url: string; title: string; empty: string; render: (item: T) => ReactNode;
}) {
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [items, setItems] = useState<T[]>([]);
  const [status, setStatus] = useState("loading");
  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    void (async () => {
      try {
        const response = await fetch(`${url}?limit=11&offset=${page * 10}`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error();
        if (!controller.signal.aborted) { setItems(data); setStatus("ready"); }
      } catch { if (!controller.signal.aborted) setStatus("error"); }
    })();
    return () => controller.abort();
  }, [url, page, attempt]);
  return <section aria-label={title}>
    <h3>{title}</h3>
    {status === "loading" ? <p role="status">加载中…</p> : status === "error" ?
      <p role="alert">{title}加载失败 <button onClick={() => setAttempt(attempt + 1)}>重试</button></p> :
      items.length ? <ul className="review-history">{items.slice(0, 10).map((item) => <li key={item.id}>{render(item)}</li>)}</ul> : <p role="status">{empty}</p>}
    <nav className="pagination" aria-label={`${title}分页`}><span>第 {page + 1} 页</span>
      <button title="上一页" aria-label={`${title}上一页`} disabled={status === "loading" || page === 0} onClick={() => setPage(page - 1)}>←</button>
      <button title="下一页" aria-label={`${title}下一页`} disabled={status !== "ready" || items.length <= 10} onClick={() => setPage(page + 1)}>→</button>
    </nav>
  </section>;
}

export function QualityReview({ url, assets }: { url: string; assets: { id: string; path: string }[] }) {
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  async function run() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${url}/quality-check`, { method: "POST" });
      if (!response.ok) throw new Error();
      setReport(await response.json()); setRefresh((value) => value + 1);
    } catch { setError("检查失败，未取得新的报告，请重试"); }
    finally { setBusy(false); }
  }
  return <section className="detail-section" aria-label="质量检查">
    <div className="content-header"><h2>质量检查</h2><button className="primary-button" disabled={busy} onClick={() => void run()}>{busy ? "检查中…" : "运行质量检查"}</button></div>
    {error && <p role="alert" className="form-error">{error}</p>}
    {report && <section aria-label="报告详情">
      <h3>报告详情</h3><p role="status">{result(report)} · 检查时间：{date(report.checked_at)}</p>
      <p className="review-meta">{report.rule_version} · {report.id}</p>
      {report.issues.length > 0 && <ul className="check-issues">{report.issues.map((issue, index) => <li key={index}>
        {label(issue.code)}：{label(issue.field)}{issue.row !== undefined && ` · 第 ${issue.row} 行`}
        {issue.asset_id && `（${assets.find((asset) => asset.id === issue.asset_id)?.path || issue.asset_id}）`}
      </li>)}</ul>}
    </section>}
    <History<Report> key={refresh} url={`${url}/quality-reports`} title="检查历史" empty="暂无检查报告" render={(item) => <>
      <button disabled={busy} onClick={() => setReport(item)}>查看报告</button>
      <span>{date(item.checked_at)} · {result(item)} · {item.rule_version}</span>
    </>} />
  </section>;
}

export function AnnotationEditor({ url, value, onSaved }: { url: string; value: Annotation; onSaved: (value: Annotation) => void }) {
  const [outcome, setOutcome] = useState(value.outcome);
  const [reason, setReason] = useState(value.failure_reason || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { setOutcome(value.outcome); setReason(value.failure_reason || ""); }, [value.outcome, value.failure_reason, value.annotation_version]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || conflict) return;
    if (outcome === "failure" && !reason.trim()) { setError("请填写失败原因"); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${url}/annotation`, { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outcome, failure_reason: outcome === "failure" ? reason.trim() : null, expected_version: value.annotation_version }) });
      if (response.status === 409) { setConflict(true); setError("标注已被修改，当前输入尚未保存。请载入最新标注后重新编辑。"); return; }
      if (!response.ok) throw new Error();
      const data = await response.json();
      onSaved(data); setReason(data.failure_reason || "");
      setMessage(data.changed ? "标注已保存" : "标注无变化"); setRefresh((value) => value + 1);
    } catch { setError("保存失败，输入已保留，请重试"); }
    finally { setBusy(false); }
  }
  async function reload() {
    setBusy(true); setError("");
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error();
      const data = await response.json(); onSaved(data);
      setOutcome(data.outcome); setReason(data.failure_reason || ""); setConflict(false);
      setMessage("已载入最新标注"); setRefresh((value) => value + 1);
    } catch { setError("载入失败，输入已保留，请重试"); }
    finally { setBusy(false); }
  }
  return <section className="detail-section" aria-label="人工标注">
    <h2>人工标注</h2>
    <form className="project-form" onSubmit={(event) => void save(event)}>
      <div className="form-fields">
        <label>操作结果<select value={outcome} disabled={busy} onChange={(event) => { setOutcome(event.target.value); setMessage(""); }}>
          <option value="pending">待确认</option><option value="success">成功</option><option value="failure">失败</option>
        </select></label>
        {outcome === "failure" && <label>失败原因<textarea rows={3} maxLength={2000} required value={reason} disabled={busy} onChange={(event) => { setReason(event.target.value); setMessage(""); }} /></label>}
      </div>
      {error && <p role="alert" className="form-error">{error}</p>}
      {message && <p role="status">{message}</p>}
      <div className="form-actions">
        {conflict ? <button type="button" disabled={busy} onClick={() => void reload()}>放弃输入并载入最新标注</button> :
          <button type="button" disabled={busy} onClick={() => { setOutcome(value.outcome); setReason(value.failure_reason || ""); setError(""); setMessage(""); }}>重置标注</button>}
        <button type="submit" className="primary-button" disabled={busy || conflict}>{busy ? "处理中…" : "保存标注"}</button>
      </div>
    </form>
    <History<Revision> key={refresh} url={`${url}/annotations`} title="标注历史" empty="暂无标注修改" render={(item) => <>
      <span>{date(item.changed_at)} · 修订 {item.revision} · {label(item.field_name)}</span>
      <span className="revision-values">{item.field_name === "outcome" ? label(item.previous_value) : item.previous_value ?? "—"} → {item.field_name === "outcome" ? label(item.current_value) : item.current_value ?? "—"}</span>
    </>} />
  </section>;
}
