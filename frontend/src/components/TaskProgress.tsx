import React, { useEffect, useState } from "react";
import {
  ThumbsUp,
  ThumbsDown,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  FolderOpen,
  Image as ImageIcon,
  Copy,
} from "lucide-react";
import type { TaskProgress as TaskProgressType } from "../lib/api-types";
import type { PhotoInfo } from "../lib/api";
import { setPhotoFeedback } from "../lib/api";

interface Props {
  task: TaskProgressType | null;
  photos: PhotoInfo[];
  onFeedback?: (photoId: number, feedback: "up" | "down") => void;
}

const STAGE_LABEL: Record<string, string> = {
  converting: "转换预览",
  analyzing: "M3 分析中",
  grading: "应用调色",
};

function formatElapsed(ms: number): string {
  if (ms < 1000) return "刚刚开始";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec} 秒`;
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  return `${min} 分 ${s.toString().padStart(2, "0")} 秒`;
}

function basename(p?: string | null): string {
  if (!p) return "";
  return p.split(/[/\\]/).pop() || p;
}

export const TaskProgress: React.FC<Props> = ({ task, photos, onFeedback }) => {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!task) {
      setElapsed(0);
      return;
    }
    const startedAt = Date.now();
    const isRunning = task.status !== "done" && task.status !== "failed";
    if (!isRunning) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const id = setInterval(() => setElapsed(Date.now() - startedAt), 500);
    return () => clearInterval(id);
  }, [task?.task_id, task?.status]);

  if (!task) {
    return (
      <div className="apple-glass px-4 py-2.5 text-xs text-zinc-500 italic mx-4 mb-4" style={{ borderRadius: "14px" }}>
        任务启动后,这里会显示每张照片的处理进度。
      </div>
    );
  }

  const pct = task.total > 0 ? Math.round((task.current / task.total) * 100) : 0;
  const kept = photos.filter((p) => p.keep === 1).length;
  const culled = photos.filter((p) => p.keep === 0).length;
  const failed = photos.filter((p) => p.status === "failed").length;
  const isRunning = task.status !== "done" && task.status !== "failed";
  // Output 目录(grade / cull 都会在 task 完成后写到 catalog)
  const outputFolder = (task as any).output_folder as string | undefined;

  const handleOpenFolder = () => {
    if (!outputFolder) return;
    // 浏览器没有标准 API 直接打开本地文件夹,退化为复制路径到剪贴板
    // (Web 模式不在 Tauri 环境下能做的就是这点了 —— 真要看就用 Finder /
    // Explorer 粘贴路径)。
    try {
      navigator.clipboard?.writeText(outputFolder);
    } catch {
      // ignore
    }
  };

  return (
    <div className="apple-glass mx-4 mb-4 px-4 py-2.5 space-y-2" style={{ borderRadius: "14px" }}>
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-zinc-500">任务</span>
          <code className="text-phc-accent font-mono">{task.task_id.slice(0, 8)}</code>
          <span className="text-zinc-300">·</span>
          <span className="text-zinc-700 font-medium">
            {task.type === "grade" ? "🎨 修图" : "✂️ 筛片"}
          </span>
          <span className="text-zinc-300">·</span>
          <span
            className={`inline-flex items-center gap-1 font-medium ${
              task.status === "done" ? "text-phc-green" : task.status === "failed" ? "text-red-500" : "text-amber-600"
            }`}
          >
            {task.status === "done" ? (
              <CheckCircle2 size={12} />
            ) : task.status === "failed" ? (
              <XCircle size={12} />
            ) : (
              <Loader2 size={12} className="animate-spin" />
            )}
            {task.status}
          </span>
          {isRunning && (
            <span className="inline-flex items-center gap-1 text-zinc-500" title="任务已运行时长">
              <Clock size={11} />
              {formatElapsed(elapsed)}
            </span>
          )}
        </div>
        <div className="text-zinc-600 font-mono">
          {task.current}/{task.total} · {pct}%
        </div>
      </div>

      <div className="w-full h-1.5 bg-black/5 rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-phc-accent to-phc-sky transition-all duration-200 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex items-center gap-3 text-xs text-zinc-600 flex-wrap">
        {task.type === "cull" && (
          <>
            <span>
              ✅ 保留:<span className="text-phc-green font-mono font-semibold">{kept}</span>
            </span>
            <span>
              ❌ 剔除:<span className="text-red-500 font-mono font-semibold">{culled}</span>
            </span>
          </>
        )}
        {task.type === "grade" && (
          <span>
            🎨 完成:
            <span className="text-phc-green font-mono font-semibold">
              {photos.filter((p) => p.status === "graded").length}
            </span>
          </span>
        )}
        {failed > 0 && <span className="text-red-500">失败:{failed}</span>}
        {task.currentPhoto && isRunning && (
          <span className="text-zinc-700 truncate font-mono">
            · {STAGE_LABEL[task.stage ?? ""] || task.stage || "处理中"}:
            <span className="text-phc-accent ml-0.5">{task.currentPhoto}</span>
          </span>
        )}
      </div>

      {/* Output 目录(cull / grade 完成后显示,点按钮复制路径到剪贴板) */}
      {outputFolder && !isRunning && (
        <div className="flex items-center gap-2 text-[11px] text-zinc-500 border-t border-black/5 pt-1.5">
          <FolderOpen size={11} className="shrink-0" />
          <code
            className="font-mono truncate flex-1"
            title={outputFolder}
          >
            {outputFolder}
          </code>
          <button
            onClick={handleOpenFolder}
            className="apple-btn apple-btn-secondary shrink-0"
            style={{ padding: "2px 8px" }}
            title="复制 output 目录路径(在文件管理器里粘贴打开)"
          >
            <Copy size={10} />
            复制路径
          </button>
        </div>
      )}

      {/* Grade 任务的 photo 列表(带 👍/👎) */}
      {task.type === "grade" && photos.length > 0 && (
        <div className="max-h-32 overflow-y-auto border-t border-black/5 pt-1.5 space-y-0.5">
          {photos
            .filter((p) => p.status === "graded")
            .slice(-10)
            .map((p) => (
              <PhotoFeedbackRow key={p.id} photo={p} onFeedback={onFeedback} />
            ))}
        </div>
      )}

      {/* Cull 任务的 photo 列表(保留 / 剔除 / 失败,带 output 链接) */}
      {task.type === "cull" && photos.length > 0 && !isRunning && (
        <div className="max-h-40 overflow-y-auto border-t border-black/5 pt-1.5 space-y-0.5">
          {photos
            .filter((p) => p.keep === 1 || p.keep === 0 || p.status === "failed")
            .map((p) => (
              <CullPhotoRow key={p.id} photo={p} />
            ))}
        </div>
      )}

      {task.summary && (
        <div className="text-xs text-zinc-700 bg-phc-accent/10 border border-phc-accent/20 rounded-lg px-3 py-1.5">
          {task.summary}
        </div>
      )}
    </div>
  );
};

const PhotoFeedbackRow: React.FC<{
  photo: PhotoInfo;
  onFeedback?: (photoId: number, fb: "up" | "down") => void;
}> = ({ photo, onFeedback }) => {
  const handle = async (fb: "up" | "down") => {
    try {
      await setPhotoFeedback(photo.id, fb);
      onFeedback?.(photo.id, fb);
    } catch (e) {
      console.error("Failed to set feedback:", e);
    }
  };
  return (
    <div className="flex items-center justify-between text-[11px] py-0.5 group">
      <span className="truncate font-mono text-zinc-600" title={photo.source_path}>
        {basename(photo.source_path)}
      </span>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => handle("up")}
          className={`p-1 rounded ${
            photo.feedback === "up" ? "bg-phc-green/15 text-phc-green" : "text-zinc-400 hover:text-phc-green"
          }`}
          title="满意"
        >
          <ThumbsUp size={11} />
        </button>
        <button
          onClick={() => handle("down")}
          className={`p-1 rounded ${
            photo.feedback === "down" ? "bg-red-100 text-red-500" : "text-zinc-400 hover:text-red-500"
          }`}
          title="不满意"
        >
          <ThumbsDown size={11} />
        </button>
      </div>
    </div>
  );
};

const CullPhotoRow: React.FC<{ photo: PhotoInfo }> = ({ photo }) => {
  const isKept = photo.keep === 1;
  const isCulled = photo.keep === 0;
  const isFailed = photo.status === "failed";
  return (
    <div className="flex items-center gap-1.5 text-[11px] py-0.5 group">
      {isKept ? (
        <CheckCircle2 size={11} className="text-phc-green shrink-0" />
      ) : isCulled ? (
        <XCircle size={11} className="text-red-500 shrink-0" />
      ) : (
        <XCircle size={11} className="text-amber-500 shrink-0" />
      )}
      <span className="truncate font-mono text-zinc-700" title={photo.source_path}>
        {basename(photo.source_path)}
      </span>
      {isKept && photo.output_path && (
        <span
          className="text-phc-green font-mono shrink-0"
          title={photo.output_path}
        >
          → {basename(photo.output_path)}
        </span>
      )}
      {isCulled && (
        <span className="text-zinc-400 text-[10px] shrink-0">已剔除</span>
      )}
      {isFailed && photo.error && (
        <span className="text-red-500 text-[10px] shrink-0" title={photo.error}>
          {photo.error.slice(0, 60)}
        </span>
      )}
    </div>
  );
};
