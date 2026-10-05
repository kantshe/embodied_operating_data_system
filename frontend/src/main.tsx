import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";

function App() {
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
          <h1>项目</h1>
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
                <tr>
                  <td colSpan={3} className="empty-state">尚未加载项目</td>
                </tr>
              </tbody>
            </table>
          </div>
        </main>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
