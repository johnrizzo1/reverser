import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type HealthResponse,
  type ProfilesResponse,
  type BackendsResponse,
  type BackendModelsResponse,
  type SessionsResponse,
  type CreateSessionRequest,
  type CreateSessionResponse,
  type TargetsResponse,
  type KBResponse,
  type TargetSummary,
  type ConversationResponse,
  type SessionLogResponse,
  type ScopeBody,
  type ScopeResponse,
  type ReportResponse,
  type ExportReportResponse,
  type ScreenshotsResponse,
} from "./client";
import { useConnection } from "@/state/connection";

function useReady() {
  return useConnection((s) => s.status === "ready");
}

export function useHealth() {
  const ready = useReady();
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/api/health"),
    enabled: ready,
    refetchInterval: 10_000,
  });
}

export function useProfiles() {
  const ready = useReady();
  return useQuery({
    queryKey: ["profiles"],
    queryFn: () => api.get<ProfilesResponse>("/api/profiles"),
    enabled: ready,
    staleTime: 60_000,
  });
}

export function useBackends() {
  const ready = useReady();
  return useQuery({
    queryKey: ["backends"],
    queryFn: () => api.get<BackendsResponse>("/api/backends"),
    enabled: ready,
    staleTime: 60_000,
  });
}

export function useBackendModels(backend: string, apiBase: string) {
  const ready = useReady();
  const supports = backend === "lmstudio" || backend === "ollama";
  const qs = apiBase ? `?api_base=${encodeURIComponent(apiBase)}` : "";
  return useQuery({
    queryKey: ["backend-models", backend, apiBase],
    queryFn: () =>
      api.get<BackendModelsResponse>(`/api/backends/${backend}/models${qs}`),
    enabled: ready && supports,
    staleTime: 30_000,
    retry: false,
  });
}

export function useSessions() {
  const ready = useReady();
  return useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.get<SessionsResponse>("/api/sessions"),
    enabled: ready,
    refetchInterval: 5_000,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) =>
      api.post<CreateSessionResponse>("/api/sessions", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useSendMessage(sessionId: string) {
  return useMutation({
    mutationFn: (text: string) =>
      api.post<void>(`/api/sessions/${sessionId}/messages`, { text }),
  });
}

export function useTriggerSkill(sessionId: string) {
  return useMutation({
    mutationFn: (skillKey: string) =>
      api.post<void>(`/api/sessions/${sessionId}/skills/${skillKey}`),
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => api.post<void>(`/api/sessions/${sessionId}/stop`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useMarkDone() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => api.post<void>(`/api/sessions/${sessionId}/done`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useResumeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) =>
      api.post<CreateSessionResponse>(`/api/sessions/${sessionId}/resume`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useSetSudo(sessionId: string) {
  return useMutation({
    mutationFn: (password: string) =>
      api.post<void>(`/api/sessions/${sessionId}/sudo`, { password }),
  });
}

export function useTargets() {
  const ready = useReady();
  return useQuery({
    queryKey: ["targets"],
    queryFn: () => api.get<TargetsResponse>("/api/targets"),
    enabled: ready,
    staleTime: 30_000,
  });
}

export function useTargetKB(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["kb", target],
    queryFn: () => api.get<KBResponse>(`/api/targets/${encodeURIComponent(target!)}/kb`),
    enabled: ready && !!target,
    refetchInterval: 8_000,
  });
}

export function useTargetSummary(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["target-summary", target],
    queryFn: () =>
      api.get<TargetSummary>(`/api/targets/${encodeURIComponent(target!)}/summary`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useConversation(sessionId: string | null, target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["conversation", sessionId, target],
    queryFn: () =>
      api.get<ConversationResponse>(
        `/api/sessions/conversation/${encodeURIComponent(sessionId!)}` +
        `?target=${encodeURIComponent(target!)}`,
      ),
    enabled: ready && !!sessionId && !!target,
    // Snapshot history doesn't change after a session is stopped/completed,
    // so cache it generously. Hook only mounts for non-active sessions.
    staleTime: 5 * 60_000,
  });
}

export function useSessionLogReplay(
  sessionId: string | null,
  target: string | null,
) {
  const ready = useReady();
  return useQuery({
    queryKey: ["session-log", sessionId, target],
    queryFn: () =>
      api.get<SessionLogResponse>(
        `/api/sessions/log/${encodeURIComponent(sessionId!)}` +
        `?target=${encodeURIComponent(target!)}`,
      ),
    enabled: ready && !!sessionId && !!target,
    staleTime: 5 * 60_000,
  });
}

export function useScope(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["scope", target],
    queryFn: () =>
      api.get<ScopeResponse>(`/api/targets/${encodeURIComponent(target!)}/scope`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useUpdateScope(target: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScopeBody) =>
      api.put<void>(`/api/targets/${encodeURIComponent(target)}/scope`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scope", target] });
    },
  });
}

export type UpdateSessionConfigBody = {
  backend?: string;
  model?: string | null;
  api_base?: string | null;
  profile?: string;
  budget?: number;
  max_turns?: number;
};

export function useUpdateSessionConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sessionId,
      target,
      body,
    }: {
      sessionId: string;
      target: string;
      body: UpdateSessionConfigBody;
    }) =>
      api.patch<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/config` +
        `?target=${encodeURIComponent(target)}`,
        body,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useReport(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["report", target],
    queryFn: () =>
      api.get<ReportResponse>(`/api/targets/${encodeURIComponent(target!)}/report`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useExportReport(target: string) {
  return useMutation({
    mutationFn: () =>
      api.post<ExportReportResponse>(`/api/targets/${encodeURIComponent(target)}/report`),
  });
}

export function useScreenshots(target: string | null, findingId: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["screenshots", target, findingId],
    queryFn: () =>
      api.get<ScreenshotsResponse>(
        `/api/targets/${encodeURIComponent(target!)}/findings/${encodeURIComponent(findingId!)}/screenshots`,
      ),
    enabled: ready && !!target && !!findingId,
    staleTime: 60_000,
  });
}

// ---- Phase 4 (delete & archive): session-level mutations ----

export function useArchiveSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.post<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/archive` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useUnarchiveSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.del<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/archive` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.del<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useArchiveTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.post<void>(`/api/targets/${encodeURIComponent(name)}/archive`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); },
  });
}

export function useUnarchiveTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del<void>(`/api/targets/${encodeURIComponent(name)}/archive`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); },
  });
}

export function useDeleteTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del<void>(`/api/targets/${encodeURIComponent(name)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["targets"] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}
