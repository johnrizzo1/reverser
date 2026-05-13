import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";

const STATUS_COLOR: Record<string, string> = {
  confirmed: "text-green-400",
  testing: "text-amber-400",
  proposed: "text-neutral-400",
  refuted: "text-red-400",
  abandoned: "text-neutral-600",
};

export function HypothesesPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const hypotheses = useStore(store, (s) => s.hypotheses) as Array<Record<string, unknown>>;
  if (hypotheses.length === 0) {
    return <p className="p-3 text-xs text-neutral-500">no hypotheses yet</p>;
  }
  return (
    <div className="p-2 text-xs space-y-1 font-mono">
      {hypotheses.map((h, i) => {
        const status = String(h.status ?? "proposed").toLowerCase();
        return (
          <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>● {status}</span>
              <span className="text-neutral-200 truncate">{String(h.statement ?? h.title ?? "—")}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
