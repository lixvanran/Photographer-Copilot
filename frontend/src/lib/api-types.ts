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

export interface TaskProgress {
  task_id: string;
  type: "grade" | "cull";
  status: string;
  current: number;
  total: number;
  currentPhoto?: string;
  stage?: string;
  summary?: string;
}
