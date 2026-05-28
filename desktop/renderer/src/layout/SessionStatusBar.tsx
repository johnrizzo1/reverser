import { useState } from "react";
import { useStore } from "zustand";
import { ChevronDown, ChevronRight, CircleDot, Gauge, Target } from "lucide-react";
import { getSessionStore } from "@/state/session-store";
import { useProfiles, useSessions } from "@/api/queries";
import { SessionConfigPanel } from "./SessionConfigPanel";
import { cn } from "@/lib/utils";

export function SessionStatusBar({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const status = useStore(store, (s) => s.status);
  const budget = useStore(store, (s) => s.budget);
  const sessions = useSessions();
  const profiles = useProfiles();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);
  const profile = profiles.data?.profiles.find((p) => p.key === row?.profile);
  const [expanded, setExpanded] = useState(false);
  const displayTarget = row?.target_name || row?.target || "—";
  const displayAddress = row?.target_name && row.target_name !== row.target ? row.target : "";
  const statusTone =
    status === "running" ? "text-amber-300" :
    status === "awaiting_input" ? "text-emerald-300" :
    status === "stopped" ? "text-neutral-500" :
    status === "completed" ? "text-sky-300" : "text-neutral-300";

  return (
    <>
      <header className="min-h-11 border-b border-neutral-800 bg-neutral-950/85 px-3 py-1.5 text-xs">
        <div className="flex min-w-0 items-center gap-3">
          <span className={cn("inline-flex items-center gap-1.5 font-medium", statusTone)}>
            <CircleDot className="h-3.5 w-3.5" />
            {status}
          </span>
          <span className="h-4 w-px bg-neutral-800" />
          <span className="flex min-w-0 items-center gap-1.5 text-neutral-100">
            <Target className="h-3.5 w-3.5 shrink-0 text-cyan-300/80" />
            <span className="truncate font-medium">{displayTarget}</span>
            {displayAddress && (
              <span className="truncate font-mono text-[11px] text-neutral-500">
                {displayAddress}
              </span>
            )}
          </span>
          <span className="hidden rounded border border-neutral-800 bg-neutral-900/70 px-2 py-0.5 font-mono text-[11px] text-neutral-300 sm:inline">
            {profile?.name ?? row?.profile ?? "profile"}
            {profile?.domain && (
              <span className="ml-1 text-neutral-500">· {profile.domain}</span>
            )}
          </span>
          <span className="ml-auto inline-flex items-center gap-1.5 text-neutral-400">
            <Gauge className="h-3.5 w-3.5 text-neutral-500" />
          {budget
            ? <>${budget.spent.toFixed(2)} / ${(budget.spent + budget.remaining).toFixed(2)} · turn {budget.turn}/{row?.max_turns ?? "?"}</>
            : <>budget —</>}
          </span>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded p-1 text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200"
            title={expanded ? "Hide config" : "Show config"}
            aria-label={expanded ? "Hide config" : "Show config"}
          >
            {expanded
              ? <ChevronDown className="h-3.5 w-3.5" />
              : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        </div>
      </header>
      {expanded && row && <SessionConfigPanel session={row} />}
    </>
  );
}
