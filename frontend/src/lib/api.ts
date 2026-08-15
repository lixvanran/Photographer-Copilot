/**
 * Sidecar API 客户端(纯 HTTP,直连)。
 *
 * 架构:浏览器 ← fetch → Python sidecar (FastAPI :8765)
 * 没有 Tauri、没有 Rust、没有 IPC bridge、没有 CSP 限制。
 *
 * 历史的"前端日志总线 + LogPanel"已被拿掉,后端活动现在通过 start.py
 * 让 sidecar 的 stdout 直接显示在用户运行的 cmd 里。前端只负责把错误
 * 用 console 打出来,排查时去 cmd 翻日志。
 */

const SIDECAR_PORT = (import.meta as any).env?.VITE_SIDECAR_PORT || "8765";
const SIDECAR = `http://127.0.0.1:${SIDECAR_PORT}`;

// ===== 通用 fetch 封装 =====
async function call<T>(method: "GET" | "POST", path: string, body?: any): Promise<T> {
  const url = `${SIDECAR}${path}`;
  const opts: RequestInit = {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  };
  const t0 = performance.now();
  try {
    const r = await fetch(url, opts);
    const ms = Math.round(performance.now() - t0);
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      // eslint-disable-next-line no-console
      console.error(`[api] ${method} ${path} ✗ HTTP ${r.status}: ${text.slice(0, 200)}`, `(${ms}ms)`);
      throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    const data = await r.json();
    // eslint-disable-next-line no-console
    console.log(`[api] ${method} ${path} (${ms}ms)`);
    return data as T;
  } catch (e: any) {
    const ms = Math.round(performance.now() - t0);
    if (!e.message?.startsWith("HTTP ")) {
      // eslint-disable-next-line no-console
      console.error(`[api] ${method} ${path} ✗ ${e.message || e}`, `(${ms}ms)`);
    }
    throw e;
  }
}

// ===== 业务 API =====
export interface ConfigInfo {
  workspace: string;
  m3_base_url_set: boolean;
  m3_api_key_set: boolean;
  m3_model: string;
  m3_mock: boolean;
}

export interface FolderInfo { name: string; path: string; mtime: number; photo_count?: number; }
export interface LooseFileInfo { name: string; path: string; size: number; is_raw: boolean; }
export interface TaskInfo { task_id: string; status: string; output_folder?: string; summary?: string; }
export interface PhotoInfo {
  id: number;
  source_path: string;
  output_path?: string;
  status: string;
  quality_score?: number;
  keep?: number;
  reasons?: string[];
  tags?: string[];
  comment?: string;
  grade_params?: Record<string, unknown>;
  feedback?: string;
  error?: string;
}
export type ChatChunk = { text?: string; done?: boolean; error?: string };

// ----- Upload -----
export interface UploadResult {
  folder_name: string;
  folder_path: string;
  accepted: number;
  rejected: number;
  total_bytes: number;
  files: Array<{ rel_path: string; size: number; ext: string }>;
  rejected_files: Array<{ rel_path: string; reason: string }>;
}

export const sidecarHealth = () => call<{ ok: boolean; workspace: string; m3_mock: boolean | null; m3_model: string | null }>("GET", "/health");
export const getConfig = () => call<ConfigInfo>("GET", "/config");
export const listInputFolders = () => call<{ folders: FolderInfo[]; loose_files: LooseFileInfo[] }>("GET", "/input/folders");
export const renameToIn = (folderName: string) => call<{ new_name: string }>("POST", "/input/rename", { folder_name: folderName });
export const startGradeTask = (folderName: string, sceneHint?: string | null) =>
  call<TaskInfo>("POST", "/tasks/grade", {
    folder_name: folderName,
    scene_hint: sceneHint ?? null,
  });
export const startCullTask = (folderName: string, sceneHint?: string | null) =>
  call<TaskInfo>("POST", "/tasks/cull", { folder_name: folderName, scene_hint: sceneHint ?? null });
export const getTask = (taskId: string) =>
  call<{ task: any; photos: PhotoInfo[]; photo_count: number }>("GET", `/tasks/${taskId}`);
export const setPhotoFeedback = (photoId: number, feedback: "up" | "down") =>
  call<void>("POST", `/photos/${photoId}/feedback`, { feedback });

// v0.2.3:AI 看图 — 上传单图,返回 5 维评价 + 问题清单 + 修图建议(JSON)
export interface AnalyzeReport {
  scene: string;
  category: string;
  rating: {
    composition: number;
    lighting: number;
    color: number;
    subject: number;
    technical: number;
    overall: number;
  };
  rating_reason: string;
  strengths: string[];
  issues: Array<{
    type: string;
    severity: "minor" | "moderate" | "major";
    description: string;
    fixable: boolean;
  }>;
  suggestions: Array<{
    category: string;
    action: string;
    priority: "high" | "medium" | "low";
  }>;
  composition_notes: string;
  lighting_notes: string;
  color_notes: string;
  preserved: string;
  summary: string;
}

export interface AnalyzeResult {
  analyze_id: string;
  photo_path: string;
  report: AnalyzeReport;
}

export const analyzePhoto = (file: File): Promise<AnalyzeResult> => {
  // 用 XHR 而不是 fetch,因为 fetch 不能显示 progress,而且 multipart 用 FormData + POST 即可
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    xhr.open("POST", `${SIDECAR}/analyze/photo`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        // 进度上传到 console(用户看不到 — 这是一个轻量辅助,UI 可以以后加进度条)
        // console.debug("analyze upload progress", e.loaded, "/", e.total);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new Error("返回 JSON 解析失败"));
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.detail || `HTTP ${xhr.status}`));
        } catch {
          reject(new Error(`HTTP ${xhr.status}: ${xhr.responseText.slice(0, 200)}`));
        }
      }
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(form);
  });
};

// 历史:getRecentLogs / onBackendLog / subscribeLogs / LogEntry / emit /
// emitBootLog 已经被移除。后端活动改在 cmd 里直接看 stdout(/workspace/.logs/sidecar.log)。

/**
 * Upload files to sidecar via XHR (so we can show real progress).
 * `files` is a FileList from <input type="file" multiple> or webkitdirectory.
 * `folderLabel` is optional user-given name for the batch.
 */
export function uploadFiles(
  files: FileList | File[],
  folderLabel: string | null,
  onProgress: (pct: number, sentBytes: number, totalBytes: number) => void,
): Promise<UploadResult> {
  const fileArr = Array.from(files);
  if (fileArr.length === 0) return Promise.reject(new Error("no files"));

  const fd = new FormData();
  if (folderLabel) fd.append("folder_label", folderLabel);
  for (const f of fileArr) {
    // For webkitdirectory uploads, File.webkitRelativePath gives "sub/IMG.jpg";
    // for plain file uploads, it's empty and we fall back to file.name.
    const rel = (f as any).webkitRelativePath || f.name;
    fd.append("files", f, rel);
  }

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const t0 = performance.now();
    xhr.open("POST", `${SIDECAR}/upload`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100), e.loaded, e.total);
      }
    };
    xhr.onload = () => {
      const ms = Math.round(performance.now() - t0);
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          // eslint-disable-next-line no-console
          console.log(`[api] POST /upload (${fileArr.length} files, ${data.accepted} accepted) (${ms}ms)`);
          resolve(data as UploadResult);
        } else {
          // Surface the server's reason to the user clearly. Common cases:
          //   400 "no files provided" — file input was empty
          //   413 "payload too large" — files exceed _UPLOAD_MAX_TOTAL_BYTES
          //   413 "too many files"    — files exceed _UPLOAD_MAX_FILES
          //   422 (FastAPI validation) — multipart parser failed (likely truncated)
          const detail = (data?.detail || "").slice(0, 200);
          // eslint-disable-next-line no-console
          console.error(`[api] POST /upload ✗ HTTP ${xhr.status}: ${detail} (${ms}ms)`);
          reject(new Error(detail || `HTTP ${xhr.status}`));
        }
      } catch (e: any) {
        // responseText wasn't JSON — most likely a proxy / Vite interception
        // or a 502 from a stuck sidecar. Show the first chunk verbatim.
        const preview = (xhr.responseText || "").slice(0, 200);
        // eslint-disable-next-line no-console
        console.error(`[api] POST /upload ✗ bad response: ${preview} (${ms}ms)`);
        reject(new Error(`bad response (HTTP ${xhr.status}): ${preview}`));
      }
    };
    xhr.onerror = (ev) => {
      // xhr.onerror fires on network-level failure: sidecar down, CORS
      // preflight blocked by browser, DNS failure, etc. There is no
      // status code in this branch. The user usually needs to check
      // "is the sidecar running?" + browser console for CORS errors.
      const ms = Math.round(performance.now() - t0);
      // eslint-disable-next-line no-console
      console.error(`[api] POST /upload ✗ network error (sidecar 没起来? CORS 被拦? 看浏览器 console + workspace/.logs/sidecar.log) (${ms}ms)`);
      reject(new Error("network error — check sidecar is running"));
    };
    xhr.onabort = () => {
      // eslint-disable-next-line no-console
          console.warn(`[api] POST /upload aborted`);
      reject(new Error("upload aborted"));
    };
    xhr.send(fd);
  });
}

// sendChat: POST 然后读 SSE,逐 chunk 调 onChunk
export async function sendChat(
  message: string,
  onChunk: (chunk: ChatChunk) => void,
): Promise<void> {
  const t0 = performance.now();
  let r: Response;
  try {
    r = await fetch(`${SIDECAR}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
  } catch (e: any) {
    const ms = Math.round(performance.now() - t0);
    // eslint-disable-next-line no-console
    console.error(`[api] POST /chat ${e.message || e} (${ms}ms)`);
    throw e;
  }
  if (!r.ok || !r.body) {
    const ms = Math.round(performance.now() - t0);
    // eslint-disable-next-line no-console
    console.error(`[api] POST /chat HTTP ${r.status} (${ms}ms)`);
    throw new Error(`HTTP ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  // SSE parser: line-based, robust to the reader slicing mid-line.
  //
  // The previous version did `buf.split("\n"); buf = lines.pop()` which
  // dropped the last "complete" line whenever the reader returned chunks
  // that didn't end with \n — because pop() took the not-yet-finished line
  // back, but then the *next* read concatenated onto that fragment, so
  // the previous line was lost. Symptom: chat worked once, then "stopped
  // working" until reload.
  //
  // The fix: only consume up to the last \n. Anything after stays in `buf`
  // for the next read, so no line is ever dropped, and `data: ` lines
  // are processed independently (SSE empty line is just a delimiter, we
  // don't need to honour it explicitly).
  const processLine = (line: string) => {
    if (!line.startsWith("data: ")) return;
    try {
      const msg = JSON.parse(line.slice(6));
      if (msg.event === "chunk" && msg.payload?.text) onChunk({ text: msg.payload.text });
      else if (msg.event === "done") onChunk({ done: true });
      else if (msg.event === "error") onChunk({ error: msg.payload });
    } catch (e) {
      // Log so we can spot a malformed frame if/when it happens.
      // eslint-disable-next-line no-console
      console.warn("[chat SSE] malformed frame:", line.slice(0, 100), e);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lastNL = buf.lastIndexOf("\n");
    if (lastNL < 0) {
      // No complete line yet — wait for more data.
      continue;
    }
    // Everything up to (and including) the last \n is composed of
    // complete lines; everything after is a possibly-partial trailing
    // line we keep for the next read.
    const completePart = buf.slice(0, lastNL);
    buf = buf.slice(lastNL + 1);
    for (const line of completePart.split("\n")) {
      if (line === "") continue; // SSE event delimiter; we don't need it
      processLine(line);
    }
  }
  // Edge case: if the stream ended without a trailing \n, process the
  // leftover. The server should always end with a `\n\n`-terminated
  // "done" event, but we don't want to silently drop data if it didn't.
  if (buf.length > 0) {
    processLine(buf);
    buf = "";
  }
  const ms = Math.round(performance.now() - t0);
  // eslint-disable-next-line no-console
  console.log(`[api] POST /chat (SSE done) (${ms}ms)`);
}

// 历史:onBackendLog / RecentLog / SSE /events/log 已经移除。后端活动
// 改到 cmd 里直接看 stdout + workspace/.logs/sidecar.log 文件。

// ===== SSE 事件订阅 =====

// /events/{task_id} 的 SSE 流(任务进度)
export type TaskEvent = { event: string; payload: Record<string, unknown>; ts: number };

// Disconnect reason callback so callers (e.g. App.tsx) can show a toast /
// log when the SSE stream goes down. Without this, a flaky network or
// sidecar crash leaves the user staring at a stuck progress bar with
// no indication anything's wrong (the 5s polling fallback is silent).
export type TaskEventStatus = {
  state: "connecting" | "open" | "closed" | "error";
  detail?: string;
};

export function onTaskEvent(
  taskId: string,
  handler: (e: TaskEvent) => void,
  onStatus?: (s: TaskEventStatus) => void,
): () => void {
  const es = new EventSource(`${SIDECAR}/events/${taskId}`);
  es.addEventListener("message", (ev) => {
    try {
      const msg = JSON.parse((ev as MessageEvent).data);
      handler(msg);
    } catch { /* ignore malformed frame */ }
  });
  // EventSource fires "open" once the connection is established. Without
  // this the caller can't tell apart "still connecting" from "stuck".
  es.addEventListener("open", () => {
    // eslint-disable-next-line no-console
  console.log(`[api] SSE /events/${taskId} 已建立`);
    onStatus?.({ state: "open" });
  });
  // EventSource fires "error" both on transient hiccups (browser auto-
  // reconnects) and on hard failures. The browser's built-in reconnect
  // gives us 3 attempts before it gives up — we surface every error so
  // the caller can decide whether to show a toast.
  es.addEventListener("error", () => {
    const state: TaskEventStatus["state"] =
      es.readyState === EventSource.CLOSED ? "closed" :
      es.readyState === EventSource.CONNECTING ? "connecting" : "error";
    const detail = `readyState=${es.readyState} (0=CONNECTING, 1=OPEN, 2=CLOSED)`;
    // eslint-disable-next-line no-console
    console.warn(`[api] SSE /events/${taskId} ${state}${state === "closed" ? " (浏览器放弃重连, 5s 轮询兜底)" : " (浏览器自动重连中)"}`);
    onStatus?.({ state, detail });
  });
  return () => es.close();
}

// 系统消息(Tauri 桌面模式 v0.2.0+ 才用到,web 模式空实现)
export type SystemMessage = { level: "info" | "warn" | "error"; text: string };
export function onSystemMessage(handler: (msg: SystemMessage) => void): () => void {
  // 预留:未来 Tauri 模式下,会监听 `system:message` event (Rust 端 emit)
  return () => {};
}

// ===== 桌面模式预留 API (Tauri 模式 v0.2.0+ 才用到) =====
// 当前 MVP 0.1.0 跑在 web 模式,这些是占位,真实调用走上面的 fetch 版本。
// 接入 Tauri 时,把这些函数改成调原生 @tauri-apps/api 即可,UI 代码不动。
export namespace TauriApi {
  // 未来在 src-tauri/ 编好,这里会调 @tauri-apps/api 的 invoke
  // 现在编译过但运行时抛错
  export async function invoke<T = any>(cmd: string, args?: any): Promise<T> {
    throw new Error("Tauri 模式未启用 (MVP 0.1.0 走 web 模式)。传 --desktop 启动。");
  }

  export async function listen<T = any>(event: string, handler: (e: { payload: T }) => void): Promise<() => void> {
    return () => {};
  }
}

// 历史:emitBootLog 已经移除(后端活动从 cmd 读)。
