import { useConnection } from "@/state/connection";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
  }
}

function buildHeaders(token: string, extra?: HeadersInit): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...extra };
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH",
  path: string,
  body?: unknown
): Promise<T> {
  const { port, token, status } = useConnection.getState();
  if (status !== "ready" || !port || !token) {
    throw new ApiError(0, null, "service not ready");
  }
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method,
    headers: buildHeaders(token),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let parsed: unknown = null;
    try { parsed = await res.json(); } catch { /* ignore */ }
    throw new ApiError(res.status, parsed, `${method} ${path} → ${res.status}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export type HealthResponse = {
  ok: boolean;
  version: string;
  checks: Record<string, { ok: boolean; detail: string | null }>;
};

export type Profile = {
  key: string;
  name: string;
  description: string;
  domain: "binary" | "web" | "network" | string;
  skills: { name: string; key: string; description: string }[];
  tools_allowlist: string[] | null;
};

export type ProfilesResponse = { profiles: Profile[] };

export type Backend = {
  key: string;
  name: string;
  default_api_base: string | null;
  requires_api_key: boolean;
  requires_model: boolean;
};

export type BackendsResponse = { backends: Backend[] };

export type BackendModel = { id: string };
export type BackendModelsResponse = { models: BackendModel[] };

// ---- Sessions ----

export type SessionRow = {
  id: string;
  target: string;
  /** Logical target name from server snapshot (Task 17 / target-session decoupling). */
  target_name?: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  turns: number;
  total_cost: number;
  stopped_at: string | null;
  archived_at: string | null;
  backend: string;
  model: string | null;
  api_base: string | null;
  budget: number;
  max_turns: number;
};

export type SessionsResponse = { sessions: SessionRow[] };

export type CreateSessionRequest = {
  /** Raw address / path — legacy field; server uses target_name first when provided. */
  target?: string;
  /** Logical target name (target/session decoupling). */
  target_name?: string;
  /** Per-session address override when using an existing target. */
  address?: string;
  profile: string;
  backend: string;
  model: string | null;
  api_base: string | null;
  budget: number;
  max_turns: number;
};

export type CreateSessionResponse = {
  id: string;
  state: "active";
  target: string;
  profile: string;
  turns: number;
  total_cost: number;
  budget: number;
  max_turns: number;
};

// ---- Targets / KB ----

export type TargetRow = {
  name: string;
  has_kb: boolean;
  has_scope: boolean;
  archived: boolean;
};
export type TargetsResponse = { targets: TargetRow[] };

export type KBResponse = {
  hosts: unknown[];
  services: unknown[];
  credentials: unknown[];
  findings: unknown[];
  hypotheses: unknown[];
  artifacts: unknown[];
  notes: unknown[];
};

// ---- Phase 2: Target summary ----

export type TargetSummary = {
  target: string;
  sessions: {
    total: number;
    by_state: {
      active: number;
      stopped: number;
      completed: number;
      abandoned: number;
    };
  };
  spend: { total_usd: number };
  profiles_used: string[];
  first_activity: string | null;
  last_activity: string | null;
  kb_counts: {
    hosts: number;
    services: number;
    credentials: number;
    findings: number;
    hypotheses: number;
    artifacts: number;
    notes: number;
  };
};

// ---- Phase 2: Conversation replay ----

export type ConversationEntry = {
  user: string;
  agent: string;
  turn: number;
  timestamp: string;
  cost: number;
};

export type ConversationResponse = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  conversation: ConversationEntry[];
};

// ---- Phase 3a: Session log replay ----

export type LogEvent =
  | { kind: "thinking";    content: string; ts: string | null }
  | { kind: "tool_call";   name: string; input: string; ts: string | null }
  | { kind: "tool_result"; ok: boolean; preview: string; ts: string | null }
  | { kind: "dispatch";    specialty: string; phase: string;
                           content: string; ts: string | null };

export type SessionLogResponse = {
  id: string;
  events: LogEvent[];
  truncated: boolean;
};

// ---- Phase 3b: Scope ----

export type ScopeBody = {
  in_scope_cidrs: string[];
  out_of_scope_ips: string[];
  allowed_hours: string | null;
  no_dos: boolean;
  no_account_lockout: boolean;
};

export type ScopeResponse = ScopeBody & { exists: boolean };

export type ScopeUpdateError = { errors: Record<string, string> };

// ---- Phase 3b: Report ----

export type ReportResponse = {
  target: string;
  markdown: string;
  generated_at: string;
  bytes: number;
};

export type ExportReportResponse = {
  target: string;
  path: string;
  bytes: number;
};

// ---- Phase 3b: Screenshots ----

export type ScreenshotEntry = {
  index: number;
  size_bytes: number;
  captured_at: string;
};

export type ScreenshotsResponse = {
  finding_id: string;
  screenshots: ScreenshotEntry[];
};
