import React, { useRef, useState } from "react";
import { Eye, Upload, Loader2, Sparkles, ThumbsUp, ThumbsDown, AlertCircle, CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { analyzePhoto, type AnalyzeResult, type AnalyzeReport } from "../lib/api";

interface Props {
  onMessage: (level: "info" | "warn" | "error", text: string) => void;
}

const SEVERITY_ICON: Record<string, React.ReactNode> = {
  minor: <Info size={12} className="text-zinc-400 shrink-0" />,
  moderate: <AlertTriangle size={12} className="text-amber-500 shrink-0" />,
  major: <AlertCircle size={12} className="text-red-500 shrink-0" />,
};

const PRIORITY_COLOR: Record<string, string> = {
  high: "bg-red-100 text-red-700 border-red-200",
  medium: "bg-amber-50 text-amber-700 border-amber-200",
  low: "bg-zinc-100 text-zinc-600 border-zinc-200",
};

export const AnalyzeView: React.FC<Props> = ({ onMessage }) => {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = (f: File | null) => {
    if (!f) return;
    if (!f.type.startsWith("image/")) {
      onMessage("error", "请选择图片文件(JPG/PNG)");
      return;
    }
    if (f.size > 30 * 1024 * 1024) {
      onMessage("error", `文件太大: ${(f.size / 1024 / 1024).toFixed(1)}MB > 30MB`);
      return;
    }
    setFile(f);
    setResult(null);
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(URL.createObjectURL(f));
  };

  const handleAnalyze = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const r = await analyzePhoto(file);
      setResult(r);
      onMessage("info", "AI 看图完成");
    } catch (e: any) {
      const msg = e.message || String(e);
      setError(msg);
      onMessage("error", `AI 看图失败:${msg}`);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setResult(null);
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
      <div className="px-4 pt-4 pb-2 flex items-center gap-2">
        <Eye size={16} className="text-phc-accent" />
        <h2 className="text-sm font-semibold text-phc-ink">AI 看图</h2>
        <span className="text-xs text-zinc-500">上传单图 → 5 维评分 + 问题清单 + 修图建议</span>
      </div>

      <div className="px-4 pb-4 flex-1 min-h-0">
        {!file ? (
          /* 上传区 */
          <label
            className="flex flex-col items-center justify-center h-64 border-2 border-dashed border-zinc-300 rounded-lg cursor-pointer hover:border-phc-accent transition-colors"
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const f = e.dataTransfer.files?.[0];
              if (f) handleFile(f);
            }}
          >
            <Upload size={32} className="text-zinc-400 mb-2" />
            <div className="text-sm text-zinc-600 mb-1">点击上传 或 拖入图片</div>
            <div className="text-xs text-zinc-500">JPG / PNG,最大 30MB</div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0] || null)}
            />
          </label>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* 左侧:预览 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {file.name} · {(file.size / 1024).toFixed(0)} KB
                </span>
                <button
                  onClick={handleReset}
                  className="text-xs text-zinc-500 hover:text-zinc-800 flex items-center gap-1"
                >
                  <X size={12} /> 换一张
                </button>
              </div>
              <img
                src={previewUrl!}
                alt={file.name}
                className="w-full max-h-[500px] object-contain bg-zinc-100 rounded border border-zinc-200"
              />
              <button
                onClick={handleAnalyze}
                disabled={loading}
                className="w-full bg-phc-accent text-white rounded py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {loading ? (
                  <>
                    <Loader2 size={14} className="animate-spin" />
                    AI 分析中(可能 5-15 秒)...
                  </>
                ) : (
                  <>
                    <Sparkles size={14} />
                    {result ? "重新分析" : "开始 AI 看图"}
                  </>
                )}
              </button>
            </div>

            {/* 右侧:报告 */}
            <div className="min-h-[400px]">
              {error && (
                <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
                  {error}
                </div>
              )}
              {!result && !error && !loading && (
                <div className="h-full flex items-center justify-center text-zinc-400 text-sm">
                  点上面"开始 AI 看图"
                </div>
              )}
              {loading && !result && (
                <div className="h-full flex items-center justify-center text-zinc-400 text-sm">
                  <Loader2 size={16} className="animate-spin mr-2" />
                  分析中...
                </div>
              )}
              {result && <ReportPanel report={result.report} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const ReportPanel: React.FC<{ report: AnalyzeReport }> = ({ report }) => {
  const stars = (n: number) => "★".repeat(n) + "☆".repeat(5 - n);
  return (
    <div className="space-y-3 text-sm">
      {/* 头部: scene + category + 综合分 */}
      <div className="bg-phc-accent/5 border border-phc-accent/20 rounded p-3 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="px-1.5 py-0.5 text-[10px] rounded bg-phc-accent text-white">
            {report.category}
          </span>
          <span className="text-xs text-zinc-500">综合分</span>
          <span className="text-amber-500 text-sm tracking-wider">
            {stars(report.rating.overall)}
          </span>
          <span className="text-zinc-600 text-xs">{report.rating.overall}/5</span>
        </div>
        <div className="text-zinc-700">{report.scene}</div>
        <div className="text-xs text-zinc-500 italic">{report.rating_reason}</div>
      </div>

      {/* 5 维评分 */}
      <Section title="5 维评分">
        <div className="grid grid-cols-5 gap-2 text-center">
          {(
            [
              ["构图", "composition"],
              ["光线", "lighting"],
              ["色彩", "color"],
              ["主体", "subject"],
              ["技术", "technical"],
            ] as [string, keyof typeof report.rating][]
          ).map(([label, key]) => (
            <div key={key} className="space-y-0.5">
              <div className="text-[10px] text-zinc-500">{label}</div>
              <div className="text-amber-500 text-xs tracking-tight">
                {stars(report.rating[key])}
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* 做得好的 */}
      {report.strengths?.length > 0 && (
        <Section title="👍 做得好的" icon={<ThumbsUp size={12} className="text-emerald-500" />}>
          <ul className="space-y-1">
            {report.strengths.map((s, i) => (
              <li key={i} className="flex gap-1.5 text-xs text-zinc-700">
                <CheckCircle2 size={11} className="text-emerald-500 shrink-0 mt-0.5" />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* 问题清单 */}
      {report.issues?.length > 0 && (
        <Section title="⚠️ 问题清单" icon={<AlertCircle size={12} className="text-amber-500" />}>
          <ul className="space-y-1.5">
            {report.issues.map((it, i) => (
              <li key={i} className="flex gap-1.5 text-xs">
                {SEVERITY_ICON[it.severity] || SEVERITY_ICON.minor}
                <div className="flex-1">
                  <div className="text-zinc-700">{it.description}</div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">
                    {it.severity} · {it.type}
                    {it.fixable ? " · 可修" : " · 拍摄问题"}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* 修图建议 */}
      {report.suggestions?.length > 0 && (
        <Section title="🛠️ 修图建议" icon={<Sparkles size={12} className="text-phc-accent" />}>
          <ul className="space-y-1.5">
            {report.suggestions.map((s, i) => (
              <li key={i} className="flex gap-1.5 text-xs">
                <span
                  className={`shrink-0 px-1.5 py-0.5 text-[10px] rounded border ${
                    PRIORITY_COLOR[s.priority] || PRIORITY_COLOR.low
                  }`}
                >
                  {s.priority}
                </span>
                <div className="flex-1">
                  <div className="text-zinc-700">
                    <span className="text-zinc-500">[{s.category}]</span> {s.action}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* 三段专项 */}
      <Section title="构图 / 光线 / 色彩 总评">
        <div className="space-y-1.5 text-xs text-zinc-700">
          {report.composition_notes && <NoteRow label="构图" text={report.composition_notes} />}
          {report.lighting_notes && <NoteRow label="光线" text={report.lighting_notes} />}
          {report.color_notes && <NoteRow label="色彩" text={report.color_notes} />}
        </div>
      </Section>

      {/* preserved */}
      {report.preserved && (
        <div className="bg-amber-50 border border-amber-200 rounded p-2.5 text-xs">
          <div className="flex items-start gap-1.5">
            <ThumbsDown size={11} className="text-amber-600 shrink-0 mt-0.5" />
            <div>
              <span className="text-amber-700 font-medium">最该保留的(动它就死):</span>
              <span className="text-amber-900 ml-1">{report.preserved}</span>
            </div>
          </div>
        </div>
      )}

      {/* 总结 */}
      {report.summary && (
        <div className="text-xs text-zinc-500 italic border-t border-zinc-200 pt-2">
          {report.summary}
        </div>
      )}
    </div>
  );
};

const Section: React.FC<{ title: string; icon?: React.ReactNode; children: React.ReactNode }> = ({ title, icon, children }) => (
  <div className="space-y-1.5">
    <div className="flex items-center gap-1.5 text-xs font-medium text-zinc-600">
      {icon}
      <span>{title}</span>
    </div>
    {children}
  </div>
);

const NoteRow: React.FC<{ label: string; text: string }> = ({ label, text }) => (
  <div>
    <span className="text-zinc-500">[{label}]</span> <span>{text}</span>
  </div>
);
