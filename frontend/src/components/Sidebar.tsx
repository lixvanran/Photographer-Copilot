import React, { useEffect, useState } from "react";
import {
  Camera,
  MessageSquare,
  Layers,
  Server,
  Wand2,
  Scissors,
  Plus,
  Trash2,
  MessagesSquare,
  Eye,
} from "lucide-react";
import { getConfig, onSystemMessage, sidecarHealth, type ConfigInfo } from "../lib/api";
import type { Conversation } from "../lib/api-types";

export type ViewKey = "chat" | "grade" | "cull" | "analyze";

interface Props {
  view: ViewKey;
  onChangeView: (v: ViewKey) => void;
  propsOnSystemMessage: (msg: { level: "info" | "warn" | "error"; text: string }) => void;
  /** 多对话:Sidebar 显示对话列表 + 新建/删除/清空当前 */
  conversations: Conversation[];
  activeConversationId: string;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string) => void;
  onClearCurrentConversation: () => void;
}

function relTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

export const Sidebar: React.FC<Props> = ({
  view,
  onChangeView,
  propsOnSystemMessage,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onClearCurrentConversation,
}) => {
  const [config, setConfig] = useState<ConfigInfo | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [version] = useState("0.3.0");

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

  // Sidebar 整体布局:flex column,中部用 flex-1 装 conversations + nav,底部固定 status 卡片
  return (
    <aside className="w-64 apple-glass-strong flex flex-col z-10 h-full">
      {/* Header */}
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

      {/* Middle: conversations (chat-only) + nav */}
      <div className="flex-1 min-h-0 flex flex-col">
        {/* 对话区(只在 view === 'chat' 时展开;其他 view 折叠避免占空间) */}
        {view === "chat" && (
          <div className="px-3 pt-3 pb-1">
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-500">
                <MessagesSquare size={11} />
                对话
              </div>
              <div className="flex items-center gap-0.5">
                <button
                  onClick={onNewConversation}
                  className="p-1 rounded text-zinc-500 hover:text-phc-accent hover:bg-black/5 transition"
                  title="新建对话"
                >
                  <Plus size={12} />
                </button>
                <button
                  onClick={onClearCurrentConversation}
                  className="p-1 rounded text-zinc-500 hover:text-red-600 hover:bg-black/5 transition"
                  title="清空当前对话消息(不影响其他对话)"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
            <div
              className="space-y-0.5 max-h-48 overflow-y-auto pr-1"
              // 用 inverse onComplete 跟主区域滚动独立
              onWheel={(e) => e.stopPropagation()}
            >
              {conversations.length === 0 ? (
                <div className="text-[11px] text-zinc-400 italic px-2 py-1.5">
                  还没有对话
                </div>
              ) : (
                conversations.map((c) => {
                  const isActive = c.id === activeConversationId;
                  return (
                    <div
                      key={c.id}
                      className={`group flex items-center gap-1 rounded-md px-2 py-1.5 cursor-pointer transition ${
                        isActive
                          ? "bg-phc-accent/15 text-phc-ink"
                          : "hover:bg-black/5 text-zinc-700"
                      }`}
                      onClick={() => onSelectConversation(c.id)}
                    >
                      <div className="flex-1 min-w-0">
                        <div
                          className={`text-[12px] truncate ${isActive ? "font-semibold" : "font-medium"}`}
                          title={c.title}
                        >
                          {c.title}
                        </div>
                        <div className="text-[10px] text-zinc-400 truncate">
                          {c.messages.length > 0
                            ? `${c.messages.length} 条 · ${relTime(c.updatedAt)}`
                            : "空对话"}
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (
                            confirm(
                              `删除对话「${c.title}」?该对话的消息会被清掉(不影响其他对话)`,
                            )
                          ) {
                            onDeleteConversation(c.id);
                          }
                        }}
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-red-600 hover:bg-black/5 transition shrink-0"
                        title="删除对话"
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
            <div className="border-t border-black/5 my-2" />
          </div>
        )}

        {/* Nav items */}
        <nav className="flex-1 px-3 pb-3 space-y-1">
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
          <button
            className={`nav-item ${view === "analyze" ? "nav-item-active" : ""}`}
            onClick={() => onChangeView("analyze")}
          >
            <Eye size={16} className={view === "analyze" ? "" : "text-phc-accent"} />
            <span>AI 看图</span>
          </button>
        </nav>
      </div>

      {/* Bottom: status cards */}
      <div className="p-3 border-t border-black/5 space-y-2">
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

        <div className="apple-glass p-2.5">
          <div className="text-[10px] text-zinc-500 mb-0.5">工作区</div>
          <div
            className="text-[10px] font-mono text-zinc-700 break-all leading-tight"
            title={config?.workspace}
          >
            {config?.workspace ?? "—"}
          </div>
        </div>

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
