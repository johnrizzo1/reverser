import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";
import { useSessions } from "@/api/queries";

export function SessionStatusBar({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const status = useStore(store, (s) => s.status);
  const budget = useStore(store, (s) => s.budget);
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);

  return (
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
    </header>
  );
}
