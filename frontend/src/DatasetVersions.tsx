import { useEffect, useState, type FormEvent } from "react";

type Episode = { id: string; task_name: string; device_id: string | null; outcome: string };
type Asset = { id: string; path: string; asset_type: string; size_bytes: number; checksum: string };
type SnapshotEpisode = Episode & { task: { name: string; objective: string | null; scene: string | null }; started_at: string | null; ended_at: string | null; failure_reason: string | null; annotation_version: number; source_type: string; assets: Asset[]; quality_report: { checked_at: string; rule_version: string; issues: { code: string; field: string; row?: number }[] } | null };
type Version = { id: string; name: string; created_at: string; episode_count: number; snapshot: { episodes: SnapshotEpisode[] } };
const labels: Record<string, string> = { pending: "待确认", success: "成功", failure: "失败", synthetic: "合成", collected: "采集" };
const date = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const versionId = () => new URLSearchParams(location.hash.slice(1)).get("version");

function usePage<T>(url: string, page: number, attempt: number) {
  const [items, setItems] = useState<T[]>([]);
  const [status, setStatus] = useState("loading");
  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    void (async () => {
      try {
        const response = await fetch(`${url}?limit=21&offset=${page * 20}`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error();
        if (!controller.signal.aborted) { setItems(data); setStatus("ready"); }
      } catch { if (!controller.signal.aborted) setStatus("error"); }
    })();
    return () => controller.abort();
  }, [url, page, attempt]);
  return { items: items.slice(0, 20), hasNext: items.length > 20, status };
}

function Paging({ title, page, next, loading, change }: { title: string; page: number; next: boolean; loading: boolean; change: (page: number) => void }) {
  return <nav className="pagination" aria-label={`${title}分页`}><span>第 {page + 1} 页</span>
    <button type="button" title="上一页" aria-label={`${title}上一页`} disabled={loading || !page} onClick={() => change(page - 1)}>←</button>
    <button type="button" title="下一页" aria-label={`${title}下一页`} disabled={loading || !next} onClick={() => change(page + 1)}>→</button>
  </nav>;
}

export function DatasetVersions({ projectId }: { projectId: string }) {
  const base = `/api/projects/${encodeURIComponent(projectId)}`;
  const [id, setId] = useState(versionId);
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [showForm, setShowForm] = useState(false);
  const list = usePage<Version>(`${base}/versions`, page, attempt);
  useEffect(() => {
    const update = () => setId(versionId());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const link = `#project=${encodeURIComponent(projectId)}&view=versions`;
  return <section aria-label="数据集版本">
    {id ? <VersionDetail key={id} url={`${base}/versions/${encodeURIComponent(id)}`} back={link} /> : <>
      <div className="content-header"><h2>数据集版本</h2><button className="primary-button" onClick={() => setShowForm(true)} disabled={showForm}>创建版本</button></div>
      {showForm && <VersionForm base={base} cancel={() => setShowForm(false)} saved={(version) => {
        setShowForm(false); setPage(0); setAttempt(attempt + 1); location.hash = `${link}&version=${encodeURIComponent(version.id)}`;
      }} />}
      {list.status === "error" ? <p role="alert">版本加载失败 <button onClick={() => setAttempt(attempt + 1)}>重试版本列表</button></p> : list.status === "loading" ? <p role="status">加载中…</p> :
        !list.items.length ? <p role="status">暂无数据集版本</p> : <div className="table-scroll" role="region" aria-label="版本列表" tabIndex={0}><table><thead><tr><th>版本名称</th><th>记录数</th><th>创建时间</th></tr></thead>
          <tbody>{list.items.map((item) => <tr key={item.id}><td><a className="project-link" href={`${link}&version=${encodeURIComponent(item.id)}`}>{item.name}</a></td><td>{item.episode_count}</td><td>{date(item.created_at)}</td></tr>)}</tbody>
        </table></div>}
      <Paging title="版本" page={page} next={list.hasNext && list.status === "ready"} loading={list.status === "loading"} change={setPage} />
    </>}
  </section>;
}

function VersionForm({ base, cancel, saved }: { base: string; cancel: () => void; saved: (version: Version) => void }) {
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = usePage<Episode>(`${base}/episodes`, page, attempt);
  function toggle(ids: string[], checked: boolean) {
    setSelected((previous) => { const next = new Set(previous); ids.forEach((id) => checked ? next.add(id) : next.delete(id)); return next; });
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !selected.size || !name.trim()) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${base}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim(), episode_ids: [...selected] }) });
      if (!response.ok) {
        setError(response.status === 409 ? "记录没有关联文件，或文件缺失、无法读取、已发生变化，版本未创建。请检查选中记录。" : response.status === 404 ? "项目或选中记录不存在，版本未创建。" : "创建失败，请重试"); return;
      }
      saved(await response.json());
    } catch { setError("创建失败，输入与选择已保留，请重试"); }
    finally { setBusy(false); }
  }
  return <form className="project-form" onSubmit={(event) => void submit(event)} aria-label="创建数据集版本">
    <div className="form-fields"><label>版本名称<input required maxLength={200} value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label></div>
    <p>已选择 {selected.size} / 500 条 <button type="button" disabled={busy || !selected.size} onClick={() => setSelected(new Set())}>清空选择</button></p>
    {list.status === "error" ? <p role="alert">记录加载失败 <button type="button" onClick={() => setAttempt(attempt + 1)}>重试记录</button></p> : list.status === "loading" ? <p role="status">加载中…</p> : !list.items.length ? <p role="status">暂无可选记录</p> :
      <div className="table-scroll" role="region" aria-label="可选记录" tabIndex={0}><table className="version-selection"><thead><tr><th><input type="checkbox" aria-label="选择本页" disabled={busy} checked={list.items.every((item) => selected.has(item.id))} onChange={(event) => toggle(list.items.map((item) => item.id), event.target.checked)} /></th><th>记录 ID</th><th>任务 / 设备</th><th>结果</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id}><td><input type="checkbox" aria-label={`选择记录 ${item.id}`} checked={selected.has(item.id)} disabled={busy} onChange={(event) => toggle([item.id], event.target.checked)} /></td><td>{item.id}</td><td>{item.task_name} / {item.device_id || "—"}</td><td>{labels[item.outcome] || item.outcome}</td></tr>)}</tbody>
      </table></div>}
    <Paging title="可选记录" page={page} next={list.hasNext && list.status === "ready"} loading={busy || list.status === "loading"} change={setPage} />
    {selected.size > 500 && <p className="form-error" role="alert">每个版本最多选择 500 条记录</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="form-actions"><button type="button" disabled={busy} onClick={cancel}>取消</button><button type="submit" className="primary-button" disabled={busy || !name.trim() || !selected.size || selected.size > 500}>{busy ? "创建中…" : "保存版本"}</button></div>
  </form>;
}

function VersionDetail({ url, back }: { url: string; back: string }) {
  const [data, setData] = useState<Version | null>(null);
  const [status, setStatus] = useState("loading");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setStatus("loading");
    void (async () => {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (response.status === 404) { if (!controller.signal.aborted) setStatus("missing"); return; }
        if (!response.ok) throw new Error();
        const value = await response.json();
        if (!controller.signal.aborted) { setData(value); setStatus("ready"); }
      } catch { if (!controller.signal.aborted) setStatus("error"); }
    })();
    return () => controller.abort();
  }, [url, attempt]);
  return <><a className="back-link" href={back}>返回版本列表</a>
    {status !== "ready" || !data ? <p role="status">{status === "missing" ? "版本不存在或不属于当前项目" : status === "error" ? "版本详情加载失败" : "加载中…"}{status === "error" && <button onClick={() => setAttempt(attempt + 1)}>重试版本详情</button>}</p> : <>
      <h2 className="detail-title">{data.name}</h2>
      <dl className="project-info version-info"><dt>版本 ID</dt><dd>{data.id}</dd><dt>创建时间</dt><dd>{date(data.created_at)}</dd><dt>记录数</dt><dd>{data.episode_count}</dd></dl>
      <h3>记录快照</h3>{data.snapshot.episodes.map((episode) => <details className="snapshot-record" key={episode.id}>
        <summary>{episode.id} · {episode.task.name} · {labels[episode.outcome] || episode.outcome}</summary>
        <dl className="project-info"><dt>设备</dt><dd>{episode.device_id || "—"}</dd><dt>开始时间</dt><dd>{date(episode.started_at)}</dd><dt>结束时间</dt><dd>{date(episode.ended_at)}</dd><dt>来源</dt><dd>{labels[episode.source_type] || episode.source_type}</dd><dt>失败原因</dt><dd>{episode.failure_reason || "—"}</dd><dt>标注版本</dt><dd>{episode.annotation_version}</dd><dt>任务目标</dt><dd>{episode.task.objective || "—"}</dd><dt>场景</dt><dd>{episode.task.scene || "—"}</dd></dl>
        <h4>文件快照</h4><div className="table-scroll" role="region" aria-label={`文件快照 ${episode.id}`} tabIndex={0}><table><thead><tr><th>文件路径 / 类型</th><th>字节数</th><th>SHA-256</th></tr></thead><tbody>{episode.assets.map((asset) => <tr key={asset.id}><td>{asset.path} / {asset.asset_type}</td><td>{asset.size_bytes}</td><td>{asset.checksum}</td></tr>)}</tbody></table></div>
        <h4>检查报告快照</h4>{episode.quality_report ? <><p>{date(episode.quality_report.checked_at)} · {episode.quality_report.rule_version} · {episode.quality_report.issues.length} 个问题</p><ul className="check-issues">{episode.quality_report.issues.map((issue, index) => <li key={index}>{issue.code} · {issue.field}{issue.row !== undefined && ` · 第 ${issue.row} 行`}</li>)}</ul></> : <p>未检查</p>}
      </details>)}
    </>}
  </>;
}
