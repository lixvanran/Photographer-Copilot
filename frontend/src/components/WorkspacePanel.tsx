import React, { useEffect, useState, useCallback } from "react";
import { RefreshCw, Wand2, Scissors, FolderOpen, FileImage, Loader2 } from "lucide-react";
import { listInputFolders, type FolderInfo, type LooseFileInfo, type UploadResult } from "../lib/api";
import { UploadButton } from "./UploadButton";

const LOOSE_KEY = "__loose__";

export interface SelectedTarget {
  /** Folder name to pass to backend (or LOOSE_KEY) */
  folderName: string;
  /** Human-friendly label for UI */
  displayName: string;
  /** Optional photo count for hint */
  photoCount?: number;
}

interface Props {
  /** Which action button to show: "grade" | "cull" */
  mode: "grade" | "cull";
  /** Disable all controls (e.g. while a task is running) */
  disabled?: boolean;
  /** External trigger to refetch (e.g. after upload completes) */
  refreshKey?: number;
  /** Called when user clicks the start button */
  onStart: (target: SelectedTarget) => void | Promise<void>;
  /** Toast channel */
  onMessage: (level: "info" | "warn" | "error", text: string) => void;
}

export const WorkspacePanel: React.FC<Props> = ({ mode, disabled, refreshKey, onStart, onMessage }) => {
  const [folders, setFolders] = useState<FolderInfo[]>([]);
  const [looseFiles, setLooseFiles] = useState<LooseFileInfo[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const res = await listInputFolders();
      setFolders(res.folders);
      setLooseFiles(res.loose_files);
      // If nothing selected (or current selection vanished), pick the first
      // sensible target: a folder if any, else loose files if any.
      setSelected((cur) => {
        if (cur === LOOSE_KEY) return res.loose_files.length > 0 ? LOOSE_KEY : (res.folders[0]?.name ?? "");
        if (cur && res.folders.some((f) => f.name === cur)) return cur;
        return res.folders[0]?.name ?? (res.loose_files.length > 0 ? LOOSE_KEY : "");
      });
    } catch (e: any) {
      onMessage("error", `刷新 input 目录失败:${e.message || e}`);
    } finally {
      setLoading(false);
    }
  }, [onMessage]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh, refreshKey]);

  const handleUploadComplete = useCallback((result: UploadResult) => {
    onMessage("info", `上传完成:${result.accepted} 张照片已落盘`);
    if (result.rejected > 0) {
      onMessage("warn", `${result.rejected} 个文件被过滤(只支持 arw/cr2/cr3/nef/dng/jpg/jpeg/png)`);
    }
    // Auto-select the freshly uploaded folder
    setSelected(result.folder_name);
    refresh();
  }, [onMessage, refresh]);

  const handleStart = async () => {
    if (!selected) {
      onMessage("warn", "请先选一个文件夹,或上传照片");
      return;
    }
    setStarting(true);
    try {
      const target: SelectedTarget =
        selected === LOOSE_KEY
          ? { folderName: LOOSE_KEY, displayName: `散文件 (${looseFiles.length} 张)`, photoCount: looseFiles.length }
          : {
              folderName: selected,
              displayName: selected,
              photoCount: folders.find((f) => f.name === selected)?.photo_count,
            };
      await onStart(target);
    } finally {
      setStarting(false);
    }
  };

  const totalPhotos = folders.reduce((s, f) => s + (f.photo_count ?? 0), 0) + looseFiles.length;
  const noTargets = folders.length === 0 && looseFiles.length === 0;

  return (
    <div className="apple-glass mx-4 mt-4 px-4 py-3 space-y-3" style={{ borderRadius: "14px" }}>
      {/* Row 1: input selector + refresh + start */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 text-xs text-zinc-600">
          <FolderOpen size={14} className="text-phc-sky" />
          <span className="font-medium">input:</span>
        </div>

        {noTargets ? (
          <span className="text-xs text-zinc-400 italic">
            还没有照片 — 用下面的「上传文件 / 文件夹」按钮添加
          </span>
        ) : (
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={disabled || starting}
            className="bg-white/70 border border-black/10 rounded-full px-3 py-1 text-xs focus:outline-none focus:border-phc-accent max-w-[280px]"
          >
            {folders.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}{typeof f.photo_count === "number" ? ` (${f.photo_count} 张)` : ""}
              </option>
            ))}
            {looseFiles.length > 0 && (
              <option value={LOOSE_KEY}>
                散文件 ({looseFiles.length} 张)
              </option>
            )}
          </select>
        )}

        <button
          onClick={refresh}
          disabled={disabled || loading}
          className="text-zinc-400 hover:text-phc-accent transition"
          title="刷新"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
        </button>

        <div className="text-xs text-zinc-500">
          共 {totalPhotos} 张照片
        </div>

        <div className="ml-auto" />

        <button
          onClick={handleStart}
          disabled={disabled || starting || !selected}
          className="apple-btn apple-btn-primary"
        >
          {starting ? (
            <Loader2 size={14} className="animate-spin" />
          ) : mode === "grade" ? (
            <Wand2 size={14} />
          ) : (
            <Scissors size={14} />
          )}
          {starting ? "启动中..." : mode === "grade" ? "一键修图" : "一键筛片"}
        </button>
      </div>

      {/* Row 2: upload controls (separate row so the progress bar can stretch) */}
      <div className="flex items-center gap-2 flex-wrap border-t border-black/5 pt-2.5">
        <div className="flex items-center gap-1.5 text-xs text-zinc-500">
          <FileImage size={13} />
          <span>添加照片:</span>
        </div>
        <UploadButton
          disabled={disabled || starting}
          onComplete={handleUploadComplete}
          onError={(msg) => onMessage("error", `上传失败:${msg}`)}
        />
      </div>
    </div>
  );
};
