import { useState } from "react";
import { useStore } from "zustand";
import { ChevronDown, ChevronRight } from "lucide-react";
import { getSessionStore } from "@/state/session-store";
import { useSessions } from "@/api/queries";
import { SessionConfigPanel } from "./SessionConfigPanel";

export function SessionStatusBar({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const status = useStore(store, (s) => s.status);
  const budget = useStore(store, (s) => s.budget);
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <header className="h-9 border-b border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-xs gap-4 font-mono">
        <span className={
          status === "running" ? "text-amber-400" :
          status === "awaiting_input" ? "text-green-400" :
          status === "stopped" ? "text-neutral-500" :
          status === "completed" ? "text-blue-400" : "text-neutral-300"
        }>● {status}</span>
        <span className="text-neutral-300">{row?.target ?? "—"}</span>
        <span>profile: <span className="text-neutral-300">{row?.profile ?? "—"}</span></span>
        <span className="ml-auto text-neutral-400">
          {budget
            ? <>${budget.spent.toFixed(2)} / ${(budget.spent + budget.remaining).toFixed(2)} · turn {budget.turn}/{row?.max_turns ?? "?"}</>
            : <>budget —</>}
        </span>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-neutral-500 hover:text-neutral-200"
          title={expanded ? "Hide config" : "Show config"}
          aria-label={expanded ? "Hide config" : "Show config"}
        >
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5" />
            : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
      </header>
      {expanded && row && <SessionConfigPanel session={row} />}
    </>
  );
}
