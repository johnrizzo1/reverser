// desktop/renderer/src/panes/DispatchPanel.tsx
import { useState } from "react";
import type { Dispatch } from "@/state/session-store";
import { SubTurnBubble } from "./SubTurnBubble";

export function DispatchPanel({ dispatch }: { dispatch: Dispatch }) {
  const [open, setOpen] = useState(true);
  const subTurns = Array.from(dispatch.subTurns.entries()).sort((a, b) => a[0] - b[0]);
  const statusColor = dispatch.status === "completed" ? "text-green-400"
    : dispatch.status === "error" ? "text-red-400"
    : "text-amber-400";
  return (
    <div className="border-l-2 border-neutral-700 pl-2 my-2">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-neutral-300 hover:text-neutral-100"
      >
        {open ? "▾" : "▸"} dispatch_specialist[<span className="text-fuchsia-400">{dispatch.specialty}</span>]{" "}
        · <span className={statusColor}>{dispatch.status}</span>
        {dispatch.cost !== undefined && ` · $${dispatch.cost.toFixed(4)}`}
        {dispatch.turnsConsumed !== undefined && ` · ${dispatch.turnsConsumed} turns`}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="text-xs text-neutral-500">→ {dispatch.subGoal}</div>
          {subTurns.map(([n, st]) => <SubTurnBubble key={n} subTurn={st} num={n} />)}
        </div>
      )}
    </div>
  );
}
