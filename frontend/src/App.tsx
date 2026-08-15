import React, { useEffect, useMemo, useRef, useState } from "react";
import { ChatBox } from "./components/ChatBox";
import { Sidebar, type ViewKey } from "./components/Sidebar";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { GradeView } from "./views/GradeView";
import { CullView } from "./views/CullView";
import { AnalyzeView } from "./views/AnalyzeView";
import type { SelectedTarget } from "./components/WorkspacePanel";
import type {
  ChatMessage,
  Conversation,
  TaskProgress as TaskProgressType,
} from "./lib/api-types";
import {
  getTask,
  onTaskEvent,
  sendChat,
  type PhotoInfo,
} from "./lib/api";
import { usePersistedState } from "./lib/usePersistedState";

const LS_KEYS = {
  // v0.3.0+: 历史 messages key 改名为 legacy 留迁移用
  legacyMessages: "phc.messages.v1",
  conversations: "phc.conversations.v1",
  activeConvId: "phc.activeConv.v1",
  activeTask: "phc.activeTask.v1",
  taskPhotos: "phc.taskPhotos.v1",
  view: "phc.view.v1",
};

// Cap on persisted items to keep localStorage usage sane.
const MAX_PERSISTED_MESSAGES_PER_CONV = 200;
const MAX_PERSISTED_CONVERSATIONS = 30;
const MAX_PERSISTED_PHOTOS = 200;

const CONV_TITLE_MAX = 28;
const NEW_CONVERSATION_TITLE = "新对话";

/** 从用户消息里挑一句能当标题的(去前后空白 + 截断) */
function makeTitleFromText(text: string): string {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (!cleaned) return NEW_CONVERSATION_TITLE;
  return cleaned.length > CONV_TITLE_MAX
    ? cleaned.slice(0, CONV_TITLE_MAX) + "…"
    : cleaned;
}

function makeConversation(firstUserText?: string): Conversation {
  const now = Date.now();
  return {
    id: `conv-${now}-${Math.random().toString(36).slice(2, 8)}`,
    title: firstUserText ? makeTitleFromText(firstUserText) : NEW_CONVERSATION_TITLE,
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

// ----- Component -----
const App: React.FC = () => {
  // 多会话(每个对话独立 messages 上下文,互不串)。localStorage 持久化。
  // v0.3.0 升级:历史 phc.messages.v1 在 mount 时一次性迁到第一个对话里,
  // 之后用新版 schema。
  const [conversations, setConversations] = usePersistedState<Conversation[]>(
    LS_KEYS.conversations,
    [],
  );
  const [activeConvId, setActiveConvId] = usePersistedState<string>(
    LS_KEYS.activeConvId,
    "",
  );
  // 旧版 key 一次性迁移
  useEffect(() => {
    try {
      const legacyRaw = localStorage.getItem(LS_KEYS.legacyMessages);
      if (!legacyRaw) return;
      const legacy = JSON.parse(legacyRaw) as ChatMessage[];
      if (!Array.isArray(legacy) || legacy.length === 0) {
        localStorage.removeItem(LS_KEYS.legacyMessages);
        return;
      }
      setConversations((cur) => {
        if (cur.length > 0) {
          // 用户在另一个标签 / 浏览器已经升级过,清掉 legacy
          localStorage.removeItem(LS_KEYS.legacyMessages);
          return cur;
        }
        const firstUserText = legacy.find((m) => m.role === "user")?.text;
        const conv = makeConversation(firstUserText);
        conv.messages = legacy;
        localStorage.removeItem(LS_KEYS.legacyMessages);
        return [conv];
      });
      setActiveConvId((cur) => cur || "");
    } catch (e) {
      // ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 派生:当前 active conversation。可能是新建的、用户切换的、或者已被删的(被删 → 回落第一项)
  const activeConversation = useMemo<Conversation | null>(() => {
    if (conversations.length === 0) return null;
    const found = conversations.find((c) => c.id === activeConvId);
    if (found) return found;
    return conversations[0];
  }, [conversations, activeConvId]);

  const activeConvIdEffective = activeConversation?.id ?? "";

  // 当前会话 messages(派生)
  const messages = activeConversation?.messages ?? [];

  const [streamingText, setStreamingText] = useState<string>("");
  const [activeTask, setActiveTask] = usePersistedState<TaskProgressType | null>(
    LS_KEYS.activeTask,
    null,
  );
  const [taskPhotos, setTaskPhotos] = usePersistedState<PhotoInfo[]>(
    LS_KEYS.taskPhotos,
    [],
  );
  const [toast, setToast] = useState<{ level: "info" | "warn" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = usePersistedState<ViewKey>(LS_KEYS.view, "chat");
  const [refreshKey, setRefreshKey] = useState(0);
  const taskUnlistenRef = useRef<Map<string, () => void>>(new Map());
  const activeTaskRef = useRef<TaskProgressType | null>(null);
  useEffect(() => {
    activeTaskRef.current = activeTask;
  }, [activeTask]);

  // ---- helpers:会话管理 ----
  const handleNewConversation = () => {
    const conv = makeConversation();
    setConversations((cur) => {
      const next = [conv, ...cur];
      // 超过 cap 时丢最旧的(不丢用户当前正看的)
      return next.length > MAX_PERSISTED_CONVERSATIONS
        ? next.slice(0, MAX_PERSISTED_CONVERSATIONS)
        : next;
    });
    setActiveConvId(conv.id);
    setStreamingText("");
    setBusy(false);
    if (view !== "chat") setView("chat");
  };

  const handleSelectConversation = (id: string) => {
    if (id === activeConvIdEffective) return;
    setActiveConvId(id);
    setStreamingText("");
    setBusy(false);
  };

  const handleDeleteConversation = (id: string) => {
    setConversations((cur) => {
      if (cur.length <= 1) {
        // 删掉最后一个就只剩 0,自动建一个新的
        const fresh = makeConversation();
        // 注意:setActiveConvId 在 setConversations 之前/之后调用都行,
        // 这里依赖 setActiveConvId 后续 effect 兜底
        return [fresh];
      }
      const next = cur.filter((c) => c.id !== id);
      return next;
    });
    // 如果删的是当前 active,会触发 useMemo 落到第一项;这里不需要手动 setActiveConvId
  };

  const handleClearCurrentConversation = () => {
    if (!activeConversation) return;
    if (!confirm(`清空当前对话「${activeConversation.title}」的消息?(不影响其他对话)`)) {
      return;
    }
    setConversations((cur) =>
      cur.map((c) =>
        c.id === activeConvIdEffective
          ? { ...c, messages: [], updatedAt: Date.now() }
          : c,
      ),
    );
    setStreamingText("");
  };

  // ---- Toast ----
  const showToast = (level: "info" | "warn" | "error", text: string) => {
    setToast({ level, text });
    setTimeout(() => setToast(null), 4000);
  };

  // ---- Subscribe to current task's events ----
  useEffect(() => {
    if (!activeTask) return;
    const taskId = activeTask.task_id;
    let cancelled = false;

    const subscribe = async () => {
      const existing = taskUnlistenRef.current.get(taskId);
      if (existing) {
        existing();
        taskUnlistenRef.current.delete(taskId);
      }

      if (activeTask.status !== "done") {
        try {
          const res = await getTask(taskId);
          if (res.task && res.task.status === "done") {
            syncTaskDoneFromServer(res.task, res.photos);
            return;
          }
        } catch {
          /* fall through */
        }
      }

      const unlisten = await onTaskEvent(
        taskId,
        (evt) => {
          if (cancelled) return;
          handleTaskEvent(evt.event, evt.payload);
        },
        (status) => {
          if (cancelled) return;
          if (status.state === "closed") {
            showToast("warn", "任务进度连接中断,5 秒轮询兜底中");
          } else if (status.state === "error") {
            showToast("warn", "任务进度连接异常,正在重连…");
          }
        },
      );
      if (cancelled) {
        unlisten();
        return;
      }
      taskUnlistenRef.current.set(taskId, unlisten);
    };
    subscribe();

    return () => {
      cancelled = true;
      const u = taskUnlistenRef.current.get(taskId);
      if (u) {
        u();
        taskUnlistenRef.current.delete(taskId);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.task_id]);

  // ---- Polling fallback ----
  useEffect(() => {
    if (!activeTask) return;
    if (activeTask.status === "done") return;
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      try {
        const res = await getTask(activeTask.task_id);
        if (stopped) return;
        if (res.task && res.task.status === "done") {
          syncTaskDoneFromServer(res.task, res.photos);
        }
      } catch (e: any) {
        const msg = e?.message || "";
        if (msg.includes("404") || msg.toLowerCase().includes("not found")) {
          stopped = true;
          setActiveTask(null);
          showToast("warn", "之前的任务已不存在(可能 server 重启),已清空。请重新开始。");
        }
      }
    };
    const interval = setInterval(tick, 5000);
    return () => {
      stopped = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.task_id]);

  const syncTaskDoneFromServer = (serverTask: any, serverPhotos: PhotoInfo[]) => {
    const nextStatus = serverTask.status === "done" ? "done" : serverTask.status;
    setActiveTask((cur) => {
      if (!cur || cur.task_id !== activeTask?.task_id) return cur;
      return {
        ...cur,
        status: nextStatus,
        current: serverTask.progress_current ?? serverPhotos.length,
        total: serverTask.progress_total ?? serverPhotos.length,
        summary: serverTask.summary ?? cur.summary,
        output_folder: serverTask.output_folder ?? (cur as any).output_folder,
      };
    });
    setTaskPhotos(serverPhotos);
    showToast("info", serverTask.summary || "任务已完成");
    setRefreshKey((k) => k + 1);
  };

  // ---- Task event handler ----
  const handleTaskEvent = (event: string, payload: any) => {
    switch (event) {
      case "task_started":
        setActiveTask((t) =>
          t
            ? { ...t, status: "running", total: 0, output_folder: payload.output }
            : {
                task_id: payload.task_id,
                type: payload.type,
                status: "running",
                current: 0,
                total: 0,
                output_folder: payload.output,
              },
        );
        setTaskPhotos([]);
        break;
      case "photo_progress":
        setActiveTask((t) =>
          t
            ? {
                ...t,
                current: payload.current,
                total: payload.total,
                currentPhoto: payload.photo,
                stage: payload.stage,
              }
            : t,
        );
        break;
      case "photo_done":
        setTaskPhotos((prev) => {
          const exists = prev.find((p) => p.id === payload.photo_id);
          if (exists) return prev;
          const next = [
            ...prev,
            {
              id: payload.photo_id,
              source_path: payload.photo,
              output_path: payload.output,
              status: "done",
              keep: payload.keep !== undefined ? (payload.keep ? 1 : 0) : undefined,
              quality_score: payload.quality,
              comment: payload.comment,
            } as PhotoInfo,
          ];
          return next.length > MAX_PERSISTED_PHOTOS
            ? next.slice(next.length - MAX_PERSISTED_PHOTOS)
            : next;
        });
        break;
      case "photo_failed":
        setTaskPhotos((prev) => {
          const fallbackId = prev.length > 0
            ? Math.max(...prev.map((p) => p.id), 0) + 1
            : 1;
          const id = (payload.photo_id as number | undefined) ?? fallbackId;
          const next = [
            ...prev,
            {
              id,
              source_path: payload.photo,
              status: "failed",
              error: payload.error,
            } as PhotoInfo,
          ];
          return next.length > MAX_PERSISTED_PHOTOS
            ? next.slice(next.length - MAX_PERSISTED_PHOTOS)
            : next;
        });
        break;
      case "task_done":
        setActiveTask((t) =>
          t ? { ...t, status: "done", summary: payload.summary } : t,
        );
        showToast("info", payload.summary);
        setRefreshKey((k) => k + 1);
        getTask(activeTaskRef.current?.task_id ?? "")
          .then((res) => {
            setTaskPhotos(res.photos);
            const serverTask = res.task;
            if (serverTask?.output_folder) {
              setActiveTask((t) =>
                t
                  ? { ...t, output_folder: serverTask.output_folder }
                  : t,
              );
            }
          })
          .catch(console.error);
        break;
      case "ping":
        break;
    }
  };

  const handleStartTask = (
    taskId: string,
    _target: SelectedTarget,
    type: "grade" | "cull",
  ) => {
    setActiveTask({
      task_id: taskId,
      type,
      status: "pending",
      current: 0,
      total: 0,
    });
  };

  const handleFeedback = (id: number, fb: "up" | "down") => {
    // 更新本地 taskPhotos 让按钮高亮
    setTaskPhotos((cur) =>
      cur.map((p) => (p.id === id ? { ...p, feedback: fb } : p))
    );
    showToast(
      "info",
      fb === "up" ? "已记录 👍 AI 会参考你的偏好" : "已记录 👎 AI 会避开这种风格"
    );
  };

  // ---- Send chat ----
  // 注意:每个对话的 messages 互相独立,handleSend 只动当前 activeConvId
  // 对应的那一份。后端 chat 端点目前无 context(M3 单轮),所以"独立上下文"
  // 是前端组织上的隔离 —— 这点 README 里要讲清楚,免得用户以为能跨对话续聊。
  const handleSend = async (text: string) => {
    // 如果当前没有 active conversation(空状态),先建一个
    let convId = activeConvIdEffective;
    if (!convId) {
      const conv = makeConversation(text);
      setConversations((cur) => [conv, ...cur]);
      setActiveConvId(conv.id);
      convId = conv.id;
    }
    // 把 user 消息塞进当前 conv(用 convId 而不是 effective,避免上面新建后
    // setConversations 异步导致 updater 找不到)
    setConversations((cur) =>
      cur.map((c) => {
        if (c.id !== convId) return c;
        const next = [
          ...c.messages,
          { role: "user" as const, text },
        ];
        // 第一次发消息时把"新对话"标题改成消息摘要
        const title = c.title === NEW_CONVERSATION_TITLE ? makeTitleFromText(text) : c.title;
        return {
          ...c,
          title,
          messages: next,
          updatedAt: Date.now(),
        };
      }),
    );
    setStreamingText("");
    setBusy(true);
    try {
      await sendChat(text, (chunk) => {
        if (chunk.error) {
          setBusy(false);
          setStreamingText("");
          setConversations((cur) =>
            cur.map((c) => {
              if (c.id !== convId) return c;
              const next = [
                ...c.messages,
                { role: "assistant" as const, text: `错误:${chunk.error}` },
              ];
              return { ...c, messages: next, updatedAt: Date.now() };
            }),
          );
          return;
        }
        if (chunk.done) {
          setBusy(false);
          setStreamingText((cur) => {
            if (cur) {
              setConversations((cs) =>
                cs.map((c) => {
                  if (c.id !== convId) return c;
                  const next = [
                    ...c.messages,
                    { role: "assistant" as const, text: cur },
                  ];
                  return { ...c, messages: next, updatedAt: Date.now() };
                }),
              );
            }
            return "";
          });
          return;
        }
        setStreamingText((cur) => cur + (chunk.text || ""));
      });
    } catch (e: any) {
      setBusy(false);
      setStreamingText("");
      setConversations((cur) =>
        cur.map((c) => {
          if (c.id !== convId) return c;
          const next = [
            ...c.messages,
            { role: "assistant" as const, text: `发送失败:${e.message || e}` },
          ];
          return { ...c, messages: next, updatedAt: Date.now() };
        }),
      );
    }
  };

  // ---- Render ----
  const renderView = () => {
    switch (view) {
      case "grade":
        return (
          <GradeView
            activeTask={activeTask}
            taskPhotos={taskPhotos}
            onStart={handleStartTask}
            onFeedback={handleFeedback}
            onMessage={showToast}
            refreshKey={refreshKey}
          />
        );
      case "cull":
        return (
          <CullView
            activeTask={activeTask}
            taskPhotos={taskPhotos}
            onStart={handleStartTask}
            onFeedback={handleFeedback}
            onMessage={showToast}
            refreshKey={refreshKey}
          />
        );
      case "analyze":
        return <AnalyzeView onMessage={showToast} />;
      case "chat":
      default:
        return (
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden px-4 pb-4 gap-3">
            <div className="flex-1 min-h-0 apple-glass overflow-hidden">
              <ChatBox
                messages={messages}
                onSend={handleSend}
                disabled={busy}
                streamingText={streamingText}
              />
            </div>
          </div>
        );
    }
  };

  return (
    <ErrorBoundary>
      <div className="h-screen flex apple-bg overflow-hidden">
        <Sidebar
          view={view}
          onChangeView={setView}
          propsOnSystemMessage={(msg) => showToast(msg.level, msg.text)}
          conversations={conversations}
          activeConversationId={activeConvIdEffective}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onDeleteConversation={handleDeleteConversation}
          onClearCurrentConversation={handleClearCurrentConversation}
        />

        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {renderView()}
        </main>

        {toast && (
          <div
            className={`fixed bottom-4 right-4 px-4 py-2.5 apple-glass text-sm shadow-lg ${
              toast.level === "error"
                ? "border border-red-300 text-red-700 bg-red-50/90"
                : toast.level === "warn"
                ? "border border-amber-300 text-amber-700 bg-amber-50/90"
                : "text-phc-ink"
            }`}
            style={{ borderRadius: "14px" }}
          >
            {toast.text}
          </div>
        )}
      </div>
    </ErrorBoundary>
  );
};

export default App;
