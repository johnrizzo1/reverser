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
  method: "GET" | "POST" | "PUT" | "DELETE",
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
