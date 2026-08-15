import React, { useState } from "react";
import { Wand2 } from "lucide-react";
import { WorkspacePanel, type SelectedTarget } from "../components/WorkspacePanel";
import { TaskProgress } from "../components/TaskProgress";
import type { TaskProgress as TaskProgressType } from "../lib/api-types";
import type { PhotoInfo } from "../lib/api";
import { startGradeTask } from "../lib/api";

interface Props {
  activeTask: TaskProgressType | null;
  taskPhotos: PhotoInfo[];
  onStart: (taskId: string, target: SelectedTarget, type: "grade" | "cull") => void;
  onFeedback: (photoId: number, fb: "up" | "down") => void;
  onMessage: (level: "info" | "warn" | "error", text: string) => void;
  /** Bump this to force a refresh of the folder list (e.g. after upload) */
  refreshKey: number;
}

// v0.2.2:不预设风格 — 让 AI 自己看图说话。
// 用户给的方向是"用文字告诉 AI 你想保留什么、想避开什么",
// 而不是套"自然/电影/胶片"那种市面滤镜。
const HINT_PLACEHOLDER =
  "可选:用自然语言告诉 AI 你想做什么(如 '逆光人像,皮肤偏黄,保留背景冷蓝对比')";

export const GradeView: React.FC<Props> = ({
  activeTask, taskPhotos, onStart, onFeedback, onMessage, refreshKey,
}) => {
  const [hint, setHint] = useState<string>("");

  const handleStart = async (target: SelectedTarget) => {
    try {
      const task = await startGradeTask(target.folderName, hint.trim() || null);
      onMessage("info", `修图任务已启动:${target.displayName}`);
      onStart(task.task_id, target, "grade");
    } catch (e: any) {
      onMessage("error", `修图启动失败:${e.message || e}`);
    }
  };

  const isRunning = activeTask?.type === "grade" && activeTask.status !== "done";

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
      <div className="px-4 pt-4 pb-2 flex items-center gap-2">
        <Wand2 size={16} className="text-phc-accent" />
        <h2 className="text-sm font-semibold text-phc-ink">一键修图</h2>
        <span className="text-xs text-zinc-500">AI 看图说话,不是套预设滤镜</span>
      </div>

      {/* v0.2.2:仅保留"自定义提示词"输入框 — 让用户用自然语言告诉 AI
          自己的意图(场景 / 保留什么 / 避开什么),而不是选预设。 */}
      <div className="px-4 pb-3">
        <textarea
          disabled={isRunning}
          value={hint}
          onChange={e => setHint(e.target.value)}
          placeholder={HINT_PLACEHOLDER}
          rows={2}
          className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:border-phc-accent focus:outline-none disabled:opacity-50 resize-y"
        />
        <div className="mt-1 text-[10px] text-zinc-500 leading-relaxed">
          AI 会自己读 EXIF + 图像直方图,先描述场景再给参数。你要做的只是告诉它"这张想怎么用"。
        </div>
      </div>

      <WorkspacePanel
        mode="grade"
        disabled={isRunning}
        refreshKey={refreshKey}
        onStart={handleStart}
        onMessage={onMessage}
      />

      <div className="px-4 pb-4 flex-1 min-h-0">
        <TaskProgress
          task={activeTask?.type === "grade" ? activeTask : null}
          photos={taskPhotos}
          onFeedback={onFeedback}
        />
      </div>
    </div>
  );
};
