/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

export type WarningEvent = {
  type: "warning";
  step: string;
  message: string;
};

export type ValidationIssue = {
  phase: "syntax" | "semantic" | "safety" | "parse";
  severity: "error" | "warning";
  code: string;
  message: string;
};

export type FinalErrorEvent = {
  type: "final_error";
  sql?: string;
  validation_phase?: string;
  validation_issues?: ValidationIssue[];
  syntax_correction_count?: number;
  semantic_correction_count?: number;
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | WarningEvent
  | FinalErrorEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  error?: string;
  warnings?: string[];
  finalError?: FinalErrorEvent;
  referenceSql?: string;
};
