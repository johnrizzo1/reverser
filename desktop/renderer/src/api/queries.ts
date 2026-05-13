import { useQuery } from "@tanstack/react-query";
import { api, type HealthResponse, type ProfilesResponse, type BackendsResponse } from "./client";
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
