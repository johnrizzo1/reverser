/**
 * Typed hooks for the Target model endpoints introduced by the
 * target/session decoupling (Task 31).
 *
 * GET /api/targets          → TargetSummaryDto[]  (list)
 * GET /api/targets/{name}   → TargetDto           (detail with addresses)
 *
 * These hooks use the shared api client so the auth token and port are
 * picked up automatically.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useConnection } from "@/state/connection";

export interface AddressDto {
  id: string;
  kind: "ip" | "url" | "binary";
  value: string;
  status: "active" | "retired";
  added_at: string;
  retired_at?: string;
  sha256?: string;
  label?: string;
}

export interface TargetDto {
  name: string;
  kind: "network" | "binary";
  addresses: AddressDto[];
  primary_address_id: string;
  created_at: string;
  updated_at: string;
  notes?: string;
}

export interface TargetSummaryDto {
  name: string;
  kind: "network" | "binary" | null;
  primary_address: string | null;
  address_count: number;
  updated_at: string | null;
  has_kb: boolean;
  has_scope: boolean;
  archived: boolean;
}

function useReady() {
  return useConnection((s) => s.status === "ready");
}

/** Fetch all target summaries (list endpoint). */
export function useTargetsSummary() {
  const ready = useReady();
  return useQuery<TargetSummaryDto[]>({
    queryKey: ["targets-summary"],
    queryFn: async () => {
      const data = await api.get<{ targets: TargetSummaryDto[] }>("/api/targets");
      return data.targets;
    },
    enabled: ready,
    staleTime: 30_000,
  });
}

/** Fetch the full Target detail (addresses, kind, primary) for a named target. */
export function useTarget(name: string | null | undefined) {
  const ready = useReady();
  return useQuery<TargetDto>({
    queryKey: ["target-detail", name],
    queryFn: () =>
      api.get<TargetDto>(`/api/targets/${encodeURIComponent(name!)}`),
    enabled: ready && !!name,
    staleTime: 30_000,
  });
}
