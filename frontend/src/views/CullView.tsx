import React from "react";
import { Scissors } from "lucide-react";
import { WorkspacePanel, type SelectedTarget } from "../components/WorkspacePanel";
import { TaskProgress } from "../components/TaskProgress";
import type { TaskProgress as TaskProgressType } from "../lib/api-types";
import type { PhotoInfo } from "../lib/api";
import { startCullTask } from "../lib/api";

interface Props {
  activeTask: TaskProgressType | null;
  taskPhotos: PhotoInfo[];
  onStart: (taskId: string, target: SelectedTarget, type: "grade" | "cull") => void;
  onFeedback: (photoId: number, fb: "up" | "down") => void;
  onMessage: (level: "info" | "warn" | "error", text: string) => void;
  refreshKey: number;
}

export const CullView: React.FC<Props> = ({
  activeTask, taskPhotos, onStart, onFeedback, onMessage, refreshKey,
}) => {
  const handleStart = async (target: SelectedTarget) => {
    try {
      const task = await startCullTask(target.folderName, null);
      onMessage("info", `筛片任务已启动:${target.displayName}`);
      onStart(task.task_id, target, "cull");
    } catch (e: any) {
      onMessage("error", `筛片启动失败:${e.message || e}`);
    }
  };

  const isRunning = activeTask?.type === "cull" && activeTask.status !== "done";

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
      <div className="px-4 pt-4 pb-2 flex items-center gap-2">
        <Scissors size={16} className="text-phc-accent" />
        <h2 className="text-sm font-semibold text-phc-ink">一键筛片</h2>
        <span className="text-xs text-zinc-500">选文件夹 → 启动 → 看每张的保留/剔除建议</span>
      </div>

      <WorkspacePanel
        mode="cull"
        disabled={isRunning}
        refreshKey={refreshKey}
        onStart={handleStart}
        onMessage={onMessage}
      />

      <div className="px-4 pb-4 flex-1 min-h-0">
        <TaskProgress
          task={activeTask?.type === "cull" ? activeTask : null}
          photos={taskPhotos}
          onFeedback={onFeedback}
        />
      </div>
    </div>
  );
};
