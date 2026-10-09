import { useEffect, useState, type FormEvent } from "react";

type Project = { id: string; name: string; description: string | null; created_at: string };
type Task = { id: string; project_id: string; name: string; objective: string | null; scene: string | null; created_at: string };
type Status = "loading" | "ready" | "error" | "missing";
const PAGE_SIZE = 20;

export function ProjectDetail({ projectId }: { projectId: string }) {
  const url = `/api/projects/${encodeURIComponent(projectId)}`;
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [attempt, setAttempt] = useState(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskStatus, setTaskStatus] = useState<Status>("loading");
  const [page, setPage] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [taskAttempt, setTaskAttempt] = useState(0);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [scene, setScene] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    async function load() {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (response.status === 404) {
          if (!controller.signal.aborted) setStatus("missing");
          return;
        }
        if (!response.ok) throw new Error("Request failed");
        const data = await response.json();
        if (data.id !== projectId || typeof data.name !== "string" ||
          !(data.description === null || typeof data.description === "string") ||
          !validDate(data.created_at)) throw new Error("Invalid response");
        if (!controller.signal.aborted) { setProject(data); setStatus("ready"); }
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [url, projectId, attempt]);

  useEffect(() => {
    if (status !== "ready") return;
    const controller = new AbortController();
    setTaskStatus("loading");
    async function load() {
      try {
        const response = await fetch(`${url}/tasks?limit=${PAGE_SIZE + 1}&offset=${page * PAGE_SIZE}`, { signal: controller.signal });
        if (!response.ok) throw new Error("Request failed");
        const data = await response.json();
        if (!Array.isArray(data) || !data.every((task) =>
          task && typeof task.id === "string" && task.project_id === projectId &&
          typeof task.name === "string" && validDate(task.created_at) &&
          (task.objective === null || typeof task.objective === "string") &&
          (task.scene === null || typeof task.scene === "string")
        )) throw new Error("Invalid response");
        if (!controller.signal.aborted) {
          setTasks(data.slice(0, PAGE_SIZE));
          setHasNext(data.length > PAGE_SIZE);
          setTaskStatus("ready");
        }
      } catch {
        if (!controller.signal.aborted) setTaskStatus("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [url, projectId, status, page, taskAttempt]);

  async function submitTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`${url}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), objective: objective.trim() || null, scene: scene.trim() || null }),
      });
      if (!response.ok) throw new Error("Request failed");
      setShowForm(false);
      clearForm();
      setTaskStatus("loading");
      setPage(0);
      setTaskAttempt((value) => value + 1);
    } catch {
      setError("创建失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  function clearForm() { setName(""); setObjective(""); setScene(""); setError(""); }
  function turnPage(next: number) { setTaskStatus("loading"); setPage(next); }

  return <>
    <a className="back-link" href="#projects">返回项目列表</a>
    {status !== "ready" || !project ? <div className="detail-status" role="status">
      {status === "missing" ? "项目不存在" : status === "error" ? "项目加载失败" : "加载中…"}
      {status === "error" && <button className="retry" onClick={() => setAttempt((value) => value + 1)}>重试</button>}
    </div> : <>
      <h1 className="detail-title">{project.name}</h1>
      <dl className="project-info">
        <dt>项目 ID</dt><dd>{project.id}</dd>
        <dt>描述</dt><dd>{project.description || "—"}</dd>
        <dt>创建时间</dt><dd><time dateTime={project.created_at}>{formatDate(project.created_at)}</time></dd>
      </dl>
      <section aria-label="项目任务">
        <div className="content-header">
          <h2>任务</h2>
          {!showForm && <button className="primary-button" onClick={() => setShowForm(true)}>新建任务</button>}
        </div>
        {showForm && <form className="project-form" onSubmit={submitTask}>
          <div className="form-fields">
            <label>任务名称 <input autoFocus required value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label>目标 <input value={objective} onChange={(event) => setObjective(event.target.value)} /></label>
            <label>场景 <input value={scene} onChange={(event) => setScene(event.target.value)} /></label>
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="form-actions">
            <button type="button" disabled={submitting} onClick={() => { setShowForm(false); clearForm(); }}>取消</button>
            <button className="primary-button" type="submit" disabled={submitting || !name.trim()}>{submitting ? "创建中…" : "创建"}</button>
          </div>
        </form>}
        <div className="table-scroll" role="region" aria-label="任务列表" tabIndex={0}>
          <table className="task-table">
            <caption className="sr-only">任务列表</caption>
            <thead><tr><th scope="col">任务名称</th><th scope="col">目标</th><th scope="col">场景</th><th scope="col">创建时间</th></tr></thead>
            <tbody>
              {taskStatus !== "ready" || tasks.length === 0 ? <tr><td colSpan={4} className="empty-state">
                <span role="status">{taskStatus === "error" ? "任务加载失败" : taskStatus === "loading" ? "加载中…" : "暂无任务"}</span>
                {taskStatus === "error" && <button className="retry" onClick={() => { setTaskStatus("loading"); setTaskAttempt((value) => value + 1); }}>重试</button>}
              </td></tr> : tasks.map((task) => <tr key={task.id}>
                <td>{task.name}</td><td>{task.objective || "—"}</td><td>{task.scene || "—"}</td>
                <td><time dateTime={task.created_at}>{formatDate(task.created_at)}</time></td>
              </tr>)}
            </tbody>
          </table>
        </div>
        <nav className="pagination" aria-label="任务分页">
          <span>第 {page + 1} 页</span>
          <button aria-label="上一页" title="上一页" disabled={taskStatus === "loading" || page === 0} onClick={() => turnPage(page - 1)}>←</button>
          <button aria-label="下一页" title="下一页" disabled={taskStatus !== "ready" || !hasNext} onClick={() => turnPage(page + 1)}>→</button>
        </nav>
      </section>
    </>}
  </>;
}

function validDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}
function formatDate(value: string) { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
