import React, { useEffect, useState } from "react";
import { Camera, MessageSquare, Layers, Server, Wand2, Scissors, Eraser } from "lucide-react";
import { getConfig, onSystemMessage, sidecarHealth, type ConfigInfo } from "../lib/api";

export type ViewKey = "chat" | "grade" | "cull";

interface Props {
  view: ViewKey;
  onChangeView: (v: ViewKey) => void;
  propsOnSystemMessage: (msg: { level: "info" | "warn" | "error"; text: string }) => void;
  onClearHistory?: () => void;
}

export const Sidebar: React.FC<Props> = ({ view, onChangeView, propsOnSystemMessage, onClearHistory }) => {
  const [config, setConfig] = useState<ConfigInfo | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [version] = useState("0.2.0");

  useEffect(() => {
    const refresh = async () => {
      try {
        const cfg = await getConfig();
        setConfig(cfg);
        const h = await sidecarHealth();
        setHealthy(h.ok);
      } catch (e: any) {
        propsOnSystemMessage({ level: "error", text: `Sidecar 连接失败:${e.message || e}` });
        setHealthy(false);
      }
    };
    refresh();
    const t = setInterval(refresh, 5000);
    const off = onSystemMessage((msg) => propsOnSystemMessage(msg));
    return () => {
      clearInterval(t);
      off();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <aside className="w-64 apple-glass-strong flex flex-col z-10 h-full">
      {/* Header: project name + tagline */}
      <div className="p-5 border-b border-black/5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-phc-accent to-phc-sky flex items-center justify-center shadow-sm">
            <Camera size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-[15px] font-bold tracking-tightish text-phc-ink">
              摄影师助手
            </h1>
            <p className="text-[11px] text-zinc-500 -mt-0.5">Photographer Copilot</p>
          </div>
        </div>
      </div>

      {/* Nav items */}
      <nav className="flex-1 p-3 space-y-1">
        <button
          className={`nav-item ${view === "chat" ? "nav-item-active" : ""}`}
          onClick={() => onChangeView("chat")}
        >
          <MessageSquare size={16} className={view === "chat" ? "" : "text-phc-accent"} />
          <span>智能对话</span>
        </button>
        <button
          className={`nav-item ${view === "grade" ? "nav-item-active" : ""}`}
          onClick={() => onChangeView("grade")}
        >
          <Wand2 size={16} className={view === "grade" ? "" : "text-phc-accent"} />
          <span>一键修图</span>
        </button>
        <button
          className={`nav-item ${view === "cull" ? "nav-item-active" : ""}`}
          onClick={() => onChangeView("cull")}
        >
          <Scissors size={16} className={view === "cull" ? "" : "text-phc-accent"} />
          <span>一键筛片</span>
        </button>
        {/* 历史:这里原来是"后端活动"tab,现在已经把日志搬到 cmd 里
            实时显示。后端活动直接在终端里看 stdout 即可。 */}
        {onClearHistory && (
          <>
            <div className="border-t border-black/5 my-2" />
            <button
              className="nav-item text-zinc-500 hover:text-red-600"
              onClick={() => {
                if (confirm("清空聊天记录与任务进度?(不影响后端已生成的照片)")) {
                  onClearHistory();
                }
              }}
              title="清空本地缓存的聊天记录和任务进度"
            >
              <Eraser size={15} />
              <span>清空历史</span>
            </button>
          </>
        )}
      </nav>

      {/* Bottom: status card */}
      <div className="p-3 border-t border-black/5 space-y-2">
        {/* Sidecar status */}
        <div className="apple-glass p-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Server size={12} className="text-zinc-500" />
              <span className="text-[11px] font-medium text-zinc-600">Sidecar</span>
            </div>
            <span
              className={`status-dot ${
                healthy === null
                  ? "status-dot-pending"
                  : healthy
                  ? "status-dot-ok"
                  : "status-dot-err"
              }`}
            />
          </div>
          <div className="text-[10px] text-zinc-500">
            {healthy === null ? "检查中..." : healthy ? "运行中" : "离线"}
          </div>
        </div>

        {/* M3 model */}
        <div className="apple-glass p-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Layers size={12} className="text-zinc-500" />
              <span className="text-[11px] font-medium text-zinc-600">M3 模型</span>
            </div>
            <span
              className={`status-dot ${
                config?.m3_mock ? "status-dot-warn" : config ? "status-dot-ok" : "status-dot-pending"
              }`}
            />
          </div>
          <div className="text-[11px] font-mono text-phc-ink truncate" title={config?.m3_model ?? undefined}>
            {config?.m3_model ?? "—"}
          </div>
          <div className="text-[10px] text-zinc-500">
            {config?.m3_mock ? "Mock 模式" : config ? "API 已配置" : "未连接"}
          </div>
        </div>

        {/* Workspace */}
        <div className="apple-glass p-2.5">
          <div className="text-[10px] text-zinc-500 mb-0.5">工作区</div>
          <div
            className="text-[10px] font-mono text-zinc-700 break-all leading-tight"
            title={config?.workspace}
          >
            {config?.workspace ?? "—"}
          </div>
        </div>

        {/* Version */}
        <div className="flex items-center justify-between px-1 pt-1">
          <div className="text-[10px] text-zinc-400">v{version}</div>
          <div className="flex items-center gap-1">
            <span className={`status-dot ${healthy ? "status-dot-ok" : "status-dot-pending"}`} />
            <span className="text-[10px] text-zinc-500">
              {healthy ? "服务运行中" : "未就绪"}
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
};
