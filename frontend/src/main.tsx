import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";

// 显示加载中,避免 React 渲染前白屏
const root = document.getElementById("root");
if (root) {
  root.innerHTML = `
    <div style="
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: radial-gradient(ellipse at 0% 0%, #e8f0fe 0%, transparent 50%),
                  radial-gradient(ellipse at 100% 0%, #fff1e8 0%, transparent 50%),
                  radial-gradient(ellipse at 50% 100%, #fee8f0 0%, transparent 50%),
                  #f5f5f7;
      font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
      color: #1a1f2e;
    ">
      <div style="text-align:center">
        <div style="
          width:48px;height:48px;border-radius:12px;
          background:linear-gradient(135deg,#FF7A45,#3A7BD5);
          display:inline-flex;align-items:center;justify-content:center;
          color:white;font-size:24px;margin-bottom:12px;
        ">📷</div>
        <div style="font-size:15px;font-weight:600">摄影师助手</div>
        <div style="font-size:11px;color:#6e6e73;margin-top:4px">加载中...</div>
      </div>
    </div>
  `;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
