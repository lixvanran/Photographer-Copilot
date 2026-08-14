import React, { useEffect, useRef, useState } from "react";
import { ChatBox } from "./components/ChatBox";
import { Sidebar, type ViewKey } from "./components/Sidebar";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { GradeView } from "./views/GradeView";
import { CullView } from "./views/CullView";
import type { SelectedTarget } from "./components/WorkspacePanel";
import type { ChatMessage, TaskProgress as TaskProgressType } from "./lib/api-types";
import {
  getTask,
  onTaskEvent,
  sendChat,
  type PhotoInfo,
} from "./lib/api";
import { usePersistedState, clearPersisted } from "./lib/usePersistedState";

const LS_KEYS = {
  messages: "phc.messages.v1",
  activeTask: "phc.activeTask.v1",
  taskPhotos: "phc.taskPhotos.v1",
  view: "phc.view.v1",
};

// Cap on persisted messages / photos to keep localStorage usage sane.
const MAX_PERSISTED_MESSAGES = 200;
const MAX_PERSISTED_PHOTOS = 200;

// ----- Component -----
const App: React.FC = () => {
  const [messages, setMessages] = usePersistedState<ChatMessage[]>(LS_KEYS.messages, []);
  const [streamingText, setStreamingText] = useState<string>("");
  const [activeTask, setActiveTask] = usePersistedState<TaskProgressType | null>(LS_KEYS.activeTask, null);
  const [taskPhotos, setTaskPhotos] = usePersistedState<PhotoInfo[]>(LS_KEYS.taskPhotos, []);
  const [toast, setToast] = useState<{ level: "info" | "warn" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = usePersistedState<ViewKey>(LS_KEYS.view, "chat");
  /** Bumped when uploads complete so views refetch their folder list. */
  const [refreshKey, setRefreshKey] = useState(0);
  // Map<taskId, unsubscribe> — keyed by task id so that when the user
  // switches from task A to task B (or starts a new task while an old
  // one's SSE handshake is still in-flight), the old cleanup only ever
  // closes the *old* subscription. With a single `useRef<Unlisten>` we
  // had a race where the old cleanup could close a freshly-stored new
  // subscription if the new one finished its async setup between the
  // cleanup call and the next render.
  const taskUnlistenRef = useRef<Map<string, () => void>>(new Map());
  // Keep the latest activeTask available inside async callbacks (e.g. the
  // SSE handler registered with onTaskEvent) without having to rebuild the
  // subscription every time activeTask changes.
  const activeTaskRef = useRef<TaskProgressType | null>(null);
  useEffect(() => { activeTaskRef.current = activeTask; }, [activeTask]);

  // ----- Toast helper -----
  const showToast = (level: "info" | "warn" | "error", text: string) => {
    setToast({ level, text });
    setTimeout(() => setToast(null), 4000);
  };

  // ----- Subscribe to current task's events -----
  useEffect(() => {
    if (!activeTask) return;
    const taskId = activeTask.task_id;
    let cancelled = false;

    const subscribe = async () => {
      // If we already had a subscription for this exact task id, close
      // it before opening a new one. (This shouldn't happen normally —
      // useEffect only re-runs when the task_id changes — but is the
      // safe thing to do in StrictMode dev double-invoke.)
      const existing = taskUnlistenRef.current.get(taskId);
      if (existing) {
        existing();
        taskUnlistenRef.current.delete(taskId);
      }

      // If we recovered this task from localStorage on page load, also pull the
      // canonical server-side state so we don't show stale "running" forever if
      // the task already finished while the tab was reloading.
      if (activeTask.status !== "done") {
        try {
          const res = await getTask(taskId);
          if (res.task && res.task.status === "done") {
            // Task finished during the reload — sync and skip subscription
            syncTaskDoneFromServer(res.task, res.photos);
            return;
          }
        } catch {
          // Server may not have this task (e.g. it was wiped) — fall through to
          // attempt subscription anyway, then show an error if it fails.
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
          // Surface the SSE connection state to the user. We use a
          // *warn* (not error) for transient "connecting" because the
          // browser auto-reconnects on its own; the user just sees a
          // toast so they know progress is paused. Hard "closed" means
          // the browser gave up — the 5s polling fallback will take over.
          if (status.state === "closed") {
            showToast("warn", "任务进度连接中断,5 秒轮询兜底中");
          } else if (status.state === "error") {
            showToast("warn", "任务进度连接异常,正在重连…");
          }
        },
      );
      if (cancelled) {
        // React already unmounted this effect by the time onTaskEvent
        // resolved. Close the EventSource we just opened so we don't
        // leak it. The Map.delete is also defensive — if another
        // effect for the same taskId already overwrote the entry,
        // we'd otherwise close a *valid* new subscription.
        unlisten();
        return;
      }
      taskUnlistenRef.current.set(taskId, unlisten);
    };
    subscribe();

    return () => {
      cancelled = true;
      // Only close the subscription that *this* effect opened. We
      // look it up by taskId, not by "whatever is currently in the
      // ref", so we can't accidentally close a freshly-opened
      // subscription owned by a newer effect.
      const u = taskUnlistenRef.current.get(taskId);
      if (u) {
        u();
        taskUnlistenRef.current.delete(taskId);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.task_id]);

  // ----- Polling fallback -----
  // Belt-and-suspenders: even if SSE drops / handler never fires (e.g. an
  // early render unmounted the subscription), poll the server every 5s and
  // promote activeTask to "done" as soon as the server says so. Without this
  // a user could see the task stuck at 100% / spinning forever and the
  // start button disabled.
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
        // 404 means the server has forgotten this task (restart, cleared DB,
        // etc.) — there's no point polling any more. Drop the stale
        // activeTask so the UI unblocks; on next user action the server
        // will create a fresh task.
        const msg = e?.message || "";
        if (msg.includes("404") || msg.toLowerCase().includes("not found")) {
          stopped = true;
          setActiveTask(null);
          showToast("warn", "之前的任务已不存在(可能 server 重启),已清空。请重新开始。");
        }
        // Other errors (network blip, 5xx) — just retry next tick.
      }
    };
    const interval = setInterval(tick, 5000);
    return () => {
      stopped = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.task_id]);

  // Helper used by both the SSE fast path and the polling fallback. Centralized
  // so both routes promote the task to "done" with identical shape.
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
      };
    });
    setTaskPhotos(serverPhotos);
    showToast("info", serverTask.summary || "任务已完成");
    setRefreshKey((k) => k + 1);
  };

  // ----- Task event handler -----
  const handleTaskEvent = (event: string, payload: any) => {
    switch (event) {
      case "task_started":
        setActiveTask((t) =>
          t
            ? { ...t, status: "running", total: 0 }
            : {
                task_id: payload.task_id,
                type: payload.type,
                status: "running",
                current: 0,
                total: 0,
              }
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
            : t
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
          // Cap to avoid localStorage blowing up on big batches
          return next.length > MAX_PERSISTED_PHOTOS
            ? next.slice(next.length - MAX_PERSISTED_PHOTOS)
            : next;
        });
        break;
      case "photo_failed":
        setTaskPhotos((prev) => {
          // Use the catalog photo_id from the event when present (v0.1.4
          // fix: backend now includes it). Fall back to prev.length+1
          // for any pre-fix events or older server versions.
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
        // Promote to "done" via the canonical sync helper so the SSE event
        // and the polling fallback produce identical UI. We DON'T call
        // getTask() inside the setState updater (that would be a side effect
        // React 18 may double-invoke in StrictMode).
        setActiveTask((t) =>
          t ? { ...t, status: "done", summary: payload.summary } : t
        );
        showToast("info", payload.summary);
        // Bump refreshKey so views re-fetch folder list (might find new output)
        setRefreshKey((k) => k + 1);
        // Fire-and-forget photo sync (the poll will catch up if this fails).
        getTask(activeTaskRef.current?.task_id ?? "")
          .then((res) => setTaskPhotos(res.photos))
          .catch(console.error);
        break;
      case "ping":
        break;
    }
  };

  // ----- Start task from view (Grade/Cull) -----
  const handleStartTask = (taskId: string, _target: SelectedTarget, type: "grade" | "cull") => {
    setActiveTask({
      task_id: taskId,
      type,
      status: "pending",
      current: 0,
      total: 0,
    });
  };

  // ----- Photo feedback -----
  const handleFeedback = (id: number, fb: "up" | "down") => {
    showToast("info", fb === "up" ? "已记录 👍" : "已记录 👎");
  };

  // ----- Send chat -----
  const handleSend = async (text: string) => {
    setMessages((prev) => {
      const next = [...prev, { role: "user" as const, text }];
      return next.length > MAX_PERSISTED_MESSAGES
        ? next.slice(next.length - MAX_PERSISTED_MESSAGES)
        : next;
    });
    setStreamingText("");
    setBusy(true);
    try {
      await sendChat(text, (chunk) => {
        if (chunk.error) {
          setBusy(false);
          setStreamingText("");
          setMessages((prev) => {
            const next = [...prev, { role: "assistant" as const, text: `错误:${chunk.error}` }];
            return next.length > MAX_PERSISTED_MESSAGES
              ? next.slice(next.length - MAX_PERSISTED_MESSAGES)
              : next;
          });
          return;
        }
        if (chunk.done) {
          setBusy(false);
          setStreamingText((cur) => {
            if (cur) {
              setMessages((prev) => {
                const next = [...prev, { role: "assistant" as const, text: cur }];
                return next.length > MAX_PERSISTED_MESSAGES
                  ? next.slice(next.length - MAX_PERSISTED_MESSAGES)
                  : next;
              });
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
      setMessages((prev) => {
        const next = [...prev, { role: "assistant" as const, text: `发送失败:${e.message || e}` }];
        return next.length > MAX_PERSISTED_MESSAGES
          ? next.slice(next.length - MAX_PERSISTED_MESSAGES)
          : next;
      });
    }
  };

  // ----- "Clear history" handler (used by Sidebar / future menu) -----
  const handleClearHistory = () => {
    clearPersisted(LS_KEYS.messages);
    clearPersisted(LS_KEYS.activeTask);
    clearPersisted(LS_KEYS.taskPhotos);
    setMessages([]);
    setActiveTask(null);
    setTaskPhotos([]);
    showToast("info", "已清空聊天记录与任务进度");
  };

  // ----- Render -----
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
          onClearHistory={handleClearHistory}
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
