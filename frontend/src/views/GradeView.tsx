import React from "react";
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

export const GradeView: React.FC<Props> = ({
  activeTask, taskPhotos, onStart, onFeedback, onMessage, refreshKey,
}) => {
  const handleStart = async (target: SelectedTarget) => {
    try {
      const task = await startGradeTask(target.folderName, null);
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
        <span className="text-xs text-zinc-500">选文件夹 → 启动 → 看进度</span>
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
