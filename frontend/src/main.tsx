import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";

function App() {
  return (
    <main className="page">
      <p className="eyebrow">Embodied Operating Data System</p>
      <h1>具身智能操作数据管理系统</h1>
      <p className="description">
        离线管理机器人操作数据，先从项目、任务和数据记录开始。
      </p>
      <section className="status-panel" aria-label="系统状态">
        <span className="status-dot" />
        <span>项目骨架已建立</span>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
