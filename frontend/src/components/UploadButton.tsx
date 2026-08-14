import React, { useRef, useState } from "react";
import { Upload, FolderInput, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { uploadFiles, type UploadResult } from "../lib/api";

interface Props {
  disabled?: boolean;
  onComplete: (result: UploadResult) => void;
  onError: (msg: string) => void;
}

interface UploadState {
  status: "idle" | "uploading" | "done" | "error";
  pct: number;
  sentBytes: number;
  totalBytes: number;
  result?: UploadResult;
  errorMsg?: string;
}

const ACCEPT = ".arw,.cr2,.cr3,.nef,.dng,.jpg,.jpeg,.png";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export const UploadButton: React.FC<Props> = ({ disabled, onComplete, onError }) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<UploadState>({
    status: "idle",
    pct: 0,
    sentBytes: 0,
    totalBytes: 0,
  });

  const handleFiles = async (files: FileList | null, mode: "files" | "folder") => {
    if (!files || files.length === 0) return;
    setState({ status: "uploading", pct: 0, sentBytes: 0, totalBytes: 0 });

    // Build a friendly folder label: when picking a folder, use the first
    // file's webkitRelativePath's top segment; when picking files, just
    // leave it null and let the backend name the batch by timestamp.
    let label: string | null = null;
    if (mode === "folder" && files[0]) {
      const rel = (files[0] as any).webkitRelativePath || "";
      label = rel.split("/")[0] || null;
    }

    try {
      const result = await uploadFiles(files, label, (pct, sent, total) => {
        setState((s) => ({ ...s, pct, sentBytes: sent, totalBytes: total }));
      });
      setState((s) => ({ ...s, status: "done", pct: 100, result }));
      onComplete(result);
    } catch (e: any) {
      const msg = e?.message || String(e);
      setState((s) => ({ ...s, status: "error", errorMsg: msg }));
      onError(msg);
    } finally {
      // Reset file inputs so picking the same files again still fires onChange
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (folderInputRef.current) folderInputRef.current.value = "";
    }
  };

  const isUploading = state.status === "uploading";
  const showResult = state.status === "done" && state.result;
  const showError = state.status === "error";

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {/* Hidden inputs — one for files, one for folder */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT}
        onChange={(e) => handleFiles(e.target.files, "files")}
        className="hidden"
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        // @ts-ignore — non-standard but supported in all major browsers
        webkitdirectory=""
        directory=""
        onChange={(e) => handleFiles(e.target.files, "folder")}
        className="hidden"
      />

      <button
        type="button"
        disabled={disabled || isUploading}
        onClick={() => fileInputRef.current?.click()}
        className="apple-btn apple-btn-secondary"
        title="选择单/多张照片上传"
      >
        <Upload size={14} />
        上传文件
      </button>
      <button
        type="button"
        disabled={disabled || isUploading}
        onClick={() => folderInputRef.current?.click()}
        className="apple-btn apple-btn-secondary"
        title="选择整个文件夹上传(自动过滤非照片文件)"
      >
        <FolderInput size={14} />
        上传文件夹
      </button>

      {isUploading && (
        <div className="flex items-center gap-2 text-xs text-phc-ink/80 ml-1">
          <Loader2 size={13} className="animate-spin text-phc-sky" />
          <span>上传中 {state.pct}%</span>
          <div className="w-32 h-1.5 bg-black/5 rounded-full overflow-hidden">
            <div
              className="h-full bg-phc-sky transition-all"
              style={{ width: `${state.pct}%` }}
            />
          </div>
          <span className="text-zinc-500">
            {formatBytes(state.sentBytes)} / {formatBytes(state.totalBytes)}
          </span>
        </div>
      )}

      {showResult && state.result && (
        <div className="flex items-center gap-1.5 text-xs text-emerald-700">
          <CheckCircle2 size={13} />
          <span>
            已落盘 <b>{state.result.accepted}</b> 张到 <code className="px-1 py-0.5 bg-black/5 rounded">{state.result.folder_name}</code>
            {state.result.rejected > 0 && (
              <span className="text-amber-600 ml-1">（{state.result.rejected} 个被过滤）</span>
            )}
          </span>
        </div>
      )}

      {showError && (
        <div className="flex items-center gap-1.5 text-xs text-red-600">
          <AlertTriangle size={13} />
          <span>上传失败:{state.errorMsg}</span>
        </div>
      )}
    </div>
  );
};
