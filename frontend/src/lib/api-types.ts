/**
 * Shared frontend types. Lives in its own module so we can import from
 * App.tsx, components, and views without circular deps.
 */
export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  result?: any;
  duration_ms?: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  toolCalls?: ToolCall[];
}

/** 单条独立对话。每个对话有自己的 messages 列表,互不串。 */
export interface Conversation {
  id: string;
  /** 用户看到的第一行,自动取自首条 user 消息的前 N 字 */
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
  /** Optional: 该对话关联的 task id(用户从对话触发的 grade/cull 任务) */
  taskId?: string;
}

export interface TaskProgress {
  task_id: string;
  type: "grade" | "cull";
  status: string;
  current: number;
  total: number;
  currentPhoto?: string;
  stage?: string;
  summary?: string;
  /** Absolute path to the task's output folder (set once task is done). */
  output_folder?: string;
}
