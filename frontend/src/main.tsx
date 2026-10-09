import { StrictMode, useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";
import { ProjectDetail } from "./ProjectDetail";

type Project = { id: string; name: string; description: string | null; created_at: string };
const PAGE_SIZE = 20;

function App() {
  const [projectId, setProjectId] = useState(() => readProjectId());
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [hasNext, setHasNext] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    const update = () => setProjectId(readProjectId());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    async function load() {
      try {
        const response = await fetch(`/api/projects?limit=${PAGE_SIZE + 1}&offset=${page * PAGE_SIZE}`, { signal: controller.signal });
        if (!response.ok) throw new Error("Request failed");
        const data: unknown = await response.json();
        if (!Array.isArray(data) || !data.every((item) =>
          item && typeof item.id === "string" && typeof item.name === "string" &&
          (item.description === null || typeof item.description === "string") &&
          typeof item.created_at === "string" && Number.isFinite(Date.parse(item.created_at))
        )) throw new Error("Invalid response");
        if (controller.signal.aborted) return;
        setProjects(data.slice(0, PAGE_SIZE));
        setHasNext(data.length > PAGE_SIZE);
        setStatus("ready");
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [page, attempt]);

  function turnPage(next: number) {
    setStatus("loading");
    setPage(next);
  }

  async function submitProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const response = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName, description: description.trim() || null }),
      });
      if (!response.ok) throw new Error("Request failed");
      setName("");
      setDescription("");
      setShowForm(false);
      setStatus("loading");
      setPage(0);
      setAttempt((current) => current + 1);
    } catch {
      setSubmitError("创建失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="app">
      <a className="skip-link" href="#projects">跳到项目列表</a>
      <header className="app-header">
        <span className="brand">具身智能操作数据管理系统</span>
        <span className="edition">工作空间</span>
      </header>
      <div className="workspace">
        <nav className="navigation" aria-label="主导航">
          <a href="#projects" aria-current="page">项目</a>
        </nav>
        <main id="projects" className="content" tabIndex={-1}>
          {projectId ? <ProjectDetail key={projectId} projectId={projectId} /> : <>
          <div className="content-header">
            <h1>项目</h1>
            {!showForm && <button className="primary-button" onClick={() => setShowForm(true)}>新建项目</button>}
          </div>
          {showForm && (
            <form className="project-form" onSubmit={submitProject}>
              <div className="form-fields">
                <label>项目名称 <input autoFocus required value={name} onChange={(event) => setName(event.target.value)} /></label>
                <label>描述 <input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
              </div>
              {submitError && <p className="form-error" role="alert">{submitError}</p>}
              <div className="form-actions">
                <button type="button" disabled={submitting} onClick={() => { setShowForm(false); setSubmitError(""); setName(""); setDescription(""); }}>取消</button>
                <button className="primary-button" type="submit" disabled={submitting || !name.trim()}>{submitting ? "创建中…" : "创建"}</button>
              </div>
            </form>
          )}
          <div className="table-scroll" role="region" aria-label="项目列表" tabIndex={0}>
            <table>
              <caption className="sr-only">项目列表</caption>
              <thead>
                <tr>
                  <th scope="col">项目名称</th>
                  <th scope="col">描述</th>
                  <th scope="col">创建时间</th>
                </tr>
              </thead>
              <tbody>
                {status !== "ready" || projects.length === 0 ? (
                  <tr><td colSpan={3} className="empty-state">
                    <span role="status">{status === "loading" ? "加载中…" : status === "error" ? "项目加载失败" : "暂无项目"}</span>
                    {status === "error" && <button className="retry" onClick={() => { setStatus("loading"); setAttempt(attempt + 1); }}>重试</button>}
                  </td></tr>
                ) : projects.map((project) => (
                  <tr key={project.id}>
                    <td><a className="project-link" href={`#project=${encodeURIComponent(project.id)}`}>{project.name}</a></td>
                    <td>{project.description || "—"}</td>
                    <td><time dateTime={project.created_at}>{new Date(project.created_at).toLocaleString("zh-CN", { hour12: false })}</time></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav className="pagination" aria-label="项目分页">
            <span>第 {page + 1} 页</span>
            <button aria-label="上一页" title="上一页" disabled={status === "loading" || page === 0} onClick={() => turnPage(page - 1)}>←</button>
            <button aria-label="下一页" title="下一页" disabled={status !== "ready" || !hasNext} onClick={() => turnPage(page + 1)}>→</button>
          </nav>
          </>}
        </main>
      </div>
    </div>
  );
}

function readProjectId(): string | null {
  const hash = window.location.hash.slice(1);
  return hash.startsWith("project=") ? new URLSearchParams(hash).get("project") || null : null;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
