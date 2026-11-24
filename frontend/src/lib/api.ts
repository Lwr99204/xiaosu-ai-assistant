export type ComponentStatus = "ok" | "down" | "not_wired";
export type DocumentStatus = "pending" | "indexed" | "failed";

export interface HealthResponse {
  status: string;
  version: string;
  llm: { status: ComponentStatus; detail: string };
  embed: { status: ComponentStatus; detail: string };
  vector_store: { status: ComponentStatus; detail: string };
  db: { status: ComponentStatus; detail: string };
  im: { wecom: { enabled: boolean; connected: boolean; last_heartbeat: string | null } };
}

export interface DocumentMeta {
  doc_id: string;
  filename: string;
  ext: string;
  byte_size: number;
  status: DocumentStatus;
  chunk_count: number;
  version: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  indexed_at: string | null;
}

export interface UploadResult {
  filename: string;
  doc_id: string | null;
  action: "created" | "skipped" | "replaced" | "rejected";
  status: DocumentStatus | null;
  reason: string | null;
}

export interface LogItem {
  id: number;
  request_id: string;
  platform: string;
  user_id: string;
  user_name: string;
  question: string;
  answer: string | null;
  status: "ok" | "degraded" | "refused" | "error";
  refused_by: string | null;
  tool_count: number;
  total_tokens: number;
  latency_ms: number;
  created_at: string;
}

export interface ToolCall {
  round: number;
  tool_name: string;
  arguments: Record<string, unknown>;
  result_summary: string;
  ok: boolean;
  error: string | null;
  latency_ms: number;
}

export interface Citation {
  ref_no: number;
  doc_id: string;
  filename: string;
  heading_path: string[] | string;
  snippet: string;
  char_start: number;
  char_end: number;
  page: number | null;
  score: number;
}

export interface LogDetail extends LogItem {
  session_key: string;
  citations: Citation[];
  tool_calls: ToolCall[];
  usage: {
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_cny: number;
  };
}

export interface StatsValue { count: number; tokens: number; cost_cny: number }
export interface LogStats { today: StatsValue; total: StatsValue }

interface ApiErrorBody { message?: string; detail?: string; user_facing_msg?: string }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.user_facing_msg || body.message || body.detail || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}
