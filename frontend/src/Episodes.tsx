import { useEffect, useRef, useState, type FormEvent } from "react";
import { EpisodeDetail } from "./EpisodeDetail";

type Episode = {
  id: string; task_id: string; task_name: string; device_id: string | null;
  started_at: string | null; ended_at: string | null; outcome: string; source_type: string;
};
type Result = {
  id: string; episode_ids: string[]; asset_count: number;
  results: { index: number; episode_id: string; status: string; detail: string | null }[];
};
type Task = { id: string; name: string };
const PAGE_SIZE = 20;
const labels: Record<string, string> = {
  pending: "待确认", success: "成功", failure: "失败",
  synthetic: "合成", collected: "采集",
  imported: "已导入", skipped: "重复跳过", failed: "失败",
};
const reasons: Record<string, string> = {
  "Task does not belong to project": "任务不存在或不属于当前项目",
  "Episode ID already exists": "记录 ID 已存在",
  "Asset missing, unreadable or outside import directory": "关联文件缺失、无法读取或路径超出来源目录",
};
const emptyFilters = { task_id: "", device_id: "", outcome: "", started_from: "", started_to: "" };

export function Episodes({ projectId, view, active }: { projectId: string; view: "episodes" | "imports"; active: boolean }) {
  const base = `/api/projects/${encodeURIComponent(projectId)}`;
  const [entries, setEntries] = useState<Episode[]>([]);
  const [status, setStatus] = useState("loading");
  const [page, setPage] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState(emptyFilters);
  const [filterError, setFilterError] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskError, setTaskError] = useState(false);
  const [taskAttempt, setTaskAttempt] = useState(0);
  const [manifest, setManifest] = useState("");
  const [importError, setImportError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reading, setReading] = useState(false);
  const fileAttempt = useRef(0);
  const [episodeId, setEpisodeId] = useState(readEpisodeId);

  useEffect(() => {
    const update = () => setEpisodeId(readEpisodeId());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setTaskError(false);
    async function loadTasks() {
      try {
        const all: Task[] = [];
        for (let offset = 0; ; offset += 100) {
          const response = await fetch(`${base}/tasks?limit=100&offset=${offset}`, { signal: controller.signal });
          if (!response.ok) throw new Error();
          const items = await response.json();
          if (!Array.isArray(items) || !items.every((item) => typeof item.id === "string" && typeof item.name === "string")) throw new Error();
          all.push(...items);
          if (items.length < 100) break;
        }
        if (!controller.signal.aborted) setTasks(all);
      } catch {
        if (!controller.signal.aborted) setTaskError(true);
      }
    }
    void loadTasks();
    return () => controller.abort();
  }, [base, taskAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    async function load() {
      try {
        const query = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(page * PAGE_SIZE) });
        Object.entries(filters).forEach(([key, value]) => {
          if (value) query.set(key, key.startsWith("started_") ? new Date(value).toISOString() : value);
        });
        const response = await fetch(`${base}/episodes?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!Array.isArray(data) || !data.every((item) =>
          typeof item.id === "string" && typeof item.task_id === "string" && typeof item.task_name === "string" &&
          (item.device_id === null || typeof item.device_id === "string") &&
          [item.started_at, item.ended_at].every((date) => date === null || (typeof date === "string" && Number.isFinite(Date.parse(date)))) &&
          typeof item.outcome === "string" && typeof item.source_type === "string"
        )) throw new Error();
        if (!controller.signal.aborted) {
          setEntries(data.slice(0, PAGE_SIZE)); setHasNext(data.length > PAGE_SIZE); setStatus("ready");
        }
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [base, filters, page, attempt]);

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    if (draft.started_from && draft.started_to && new Date(draft.started_from) > new Date(draft.started_to)) {
      setFilterError("开始时间下限不能晚于上限"); return;
    }
    setFilterError(""); setStatus("loading"); setPage(0);
    setFilters({ ...draft, device_id: draft.device_id.trim() });
  }

  async function readFile(file?: File) {
    const token = ++fileAttempt.current;
    setResult(null); setImportError(""); setManifest("");
    if (!file) { setReading(false); return; }
    setReading(true);
    try {
      const content = await file.text();
      if (token === fileAttempt.current) setManifest(content);
    } catch {
      if (token === fileAttempt.current) setImportError("无法读取清单文件");
    } finally {
      if (token === fileAttempt.current) setReading(false);
    }
  }

  async function submitImport(event: FormEvent) {
    event.preventDefault();
    if (submitting || reading) return;
    setImportError(""); setResult(null);
    let payload: unknown;
    try { payload = JSON.parse(manifest); } catch { setImportError("JSON 格式错误"); return; }
    setSubmitting(true);
    try {
      const response = await fetch(`${base}/imports`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!response.ok) {
        setImportError(response.status === 422 ? "清单校验失败，请检查字段、时间和路径" : "导入失败，请重试");
        return;
      }
      const data = await response.json();
      if (typeof data.id !== "string" || !Array.isArray(data.episode_ids) ||
        !Number.isInteger(data.asset_count) || !Array.isArray(data.results) ||
        !data.results.every((item: Result["results"][number]) => Number.isInteger(item.index) &&
          typeof item.episode_id === "string" && ["imported", "skipped", "failed"].includes(item.status) &&
          (item.detail === null || typeof item.detail === "string"))) throw new Error();
      setResult(data); setPage(0); setAttempt((value) => value + 1);
    } catch { setImportError("导入失败，请重试"); }
    finally { setSubmitting(false); }
  }

  if (!active) return null;
  return view === "imports" ? <section aria-label="批次导入">
    <h2>批次导入</h2>
    <form className="project-form" onSubmit={submitImport}>
      <div className="form-fields">
        <label>清单文件<input type="file" accept=".json,application/json" disabled={submitting} onChange={(event) => void readFile(event.target.files?.[0])} /></label>
        <label className="full-width">JSON 清单<textarea rows={10} value={manifest} disabled={submitting || reading} onChange={(event) => { setManifest(event.target.value); setResult(null); }} /></label>
      </div>
      {importError && <p className="form-error" role="alert">{importError}</p>}
      <div className="form-actions"><button type="submit" className="primary-button" disabled={submitting || reading || !manifest.trim()}>{submitting ? "导入中…" : "导入"}</button></div>
    </form>
    {result && <>
      <p role="status">已导入 {result.episode_ids.length} 条，重复跳过 {result.results.filter((item) => item.status === "skipped").length} 条，失败 {result.results.filter((item) => item.status === "failed").length} 条；保存 {result.asset_count} 个文件</p>
      <p className="batch-id">批次 ID：{result.id}</p>
      <div className="table-scroll" role="region" aria-label="导入结果" tabIndex={0}><table>
        <thead><tr><th>记录 ID</th><th>结果</th><th>原因</th></tr></thead>
        <tbody>{result.results.map((item) => <tr key={item.index}><td>{item.episode_id}</td><td>{labels[item.status]}</td><td>{item.detail ? reasons[item.detail] || item.detail : "—"}</td></tr>)}</tbody>
      </table></div>
    </>}
  </section> : episodeId ? <EpisodeDetail key={episodeId} projectId={projectId} episodeId={episodeId} /> : <section aria-label="操作记录">
    <h2>操作记录</h2>
    <form className="project-form" onSubmit={applyFilters}>
      <div className="form-fields filter-fields">
        <label>任务<select aria-label="任务" value={draft.task_id} onChange={(event) => setDraft({ ...draft, task_id: event.target.value })}>
          <option value="">全部任务</option>{tasks.map((task) => <option key={task.id} value={task.id}>{task.name}</option>)}
        </select></label>
        <label>设备<input value={draft.device_id} onChange={(event) => setDraft({ ...draft, device_id: event.target.value })} /></label>
        <label>结果<select aria-label="结果" value={draft.outcome} onChange={(event) => setDraft({ ...draft, outcome: event.target.value })}>
          <option value="">全部结果</option>{["pending", "success", "failure"].map((value) => <option key={value} value={value}>{labels[value]}</option>)}
        </select></label>
        <label>开始时间下限<input type="datetime-local" step="1" value={draft.started_from} onChange={(event) => setDraft({ ...draft, started_from: event.target.value })} /></label>
        <label>开始时间上限<input type="datetime-local" step="1" value={draft.started_to} onChange={(event) => setDraft({ ...draft, started_to: event.target.value })} /></label>
      </div>
      {taskError && <p className="form-error">任务选项加载失败<button type="button" className="retry" onClick={() => setTaskAttempt((value) => value + 1)}>重试任务</button></p>}
      {filterError && <p className="form-error" role="alert">{filterError}</p>}
      <div className="form-actions">
        <button type="button" onClick={() => { setDraft(emptyFilters); setFilters(emptyFilters); setFilterError(""); setPage(0); setAttempt((value) => value + 1); }}>重置</button>
        <button type="submit" className="primary-button">筛选</button>
      </div>
    </form>
    <div className="table-scroll" role="region" aria-label="操作记录列表" tabIndex={0}><table className="episode-table">
      <thead><tr><th>记录 ID</th><th>任务</th><th>设备</th><th>开始时间</th><th>结束时间</th><th>结果</th><th>来源</th></tr></thead>
      <tbody>{status !== "ready" || !entries.length ? <tr><td colSpan={7} className="empty-state">
        <span role="status">{status === "loading" ? "加载中…" : status === "error" ? "记录加载失败" : "暂无操作记录"}</span>
        {status === "error" && <button className="retry" onClick={() => setAttempt((value) => value + 1)}>重试</button>}
      </td></tr> : entries.map((item) => <tr key={item.id}>
        <td><a className="project-link" href={`#project=${encodeURIComponent(projectId)}&episode=${encodeURIComponent(item.id)}`}>{item.id}</a></td><td>{item.task_name}</td><td>{item.device_id || "—"}</td>
        <td>{formatDate(item.started_at)}</td><td>{formatDate(item.ended_at)}</td>
        <td>{labels[item.outcome] || item.outcome}</td><td>{labels[item.source_type] || item.source_type}</td>
      </tr>)}</tbody>
    </table></div>
    <nav className="pagination" aria-label="记录分页"><span>第 {page + 1} 页</span>
      <button aria-label="上一页" title="上一页" disabled={status === "loading" || page === 0} onClick={() => { setStatus("loading"); setPage(page - 1); }}>←</button>
      <button aria-label="下一页" title="下一页" disabled={status !== "ready" || !hasNext} onClick={() => { setStatus("loading"); setPage(page + 1); }}>→</button>
    </nav>
  </section>;
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function readEpisodeId() {
  return new URLSearchParams(window.location.hash.slice(1)).get("episode");
}
