// desktop/renderer/src/panes/DispatchPanel.tsx
import { useState } from "react";
import { CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import type { Dispatch } from "@/state/session-store";
import { SubTurnBubble } from "./SubTurnBubble";
import { cn } from "@/lib/utils";

function latestActivity(dispatch: Dispatch): { label: string; detail: string } | null {
  const subTurns = Array.from(dispatch.subTurns.entries()).sort((a, b) => b[0] - a[0]);
  for (const [, st] of subTurns) {
    const lastResult = st.toolResults.at(-1);
    if (lastResult) {
      return { label: lastResult.ok ? "tool result" : "tool error", detail: lastResult.content };
    }
    const lastTool = st.toolCalls.at(-1);
    if (lastTool) return { label: "tool call", detail: lastTool.content };
    const lastSpeech = st.speechDeltas.at(-1);
    if (lastSpeech) return { label: "reporting", detail: lastSpeech };
    const lastThinking = st.thinkingDeltas.at(-1);
    if (lastThinking) {
      const label = /waiting for local backend slot/i.test(lastThinking)
        ? "queued on local backend"
        : /acquired local backend slot/i.test(lastThinking)
          ? "local backend active"
          : "thinking";
      return { label, detail: lastThinking };
    }
  }
  return null;
}

function trimDetail(value: string): string {
  const oneLine = value.replace(/\s+/g, " ").trim();
  return oneLine.length > 180 ? `${oneLine.slice(0, 177)}...` : oneLine;
}

export function DispatchPanel({ dispatch }: { dispatch: Dispatch }) {
  const [open, setOpen] = useState(true);
  const subTurns = Array.from(dispatch.subTurns.entries()).sort((a, b) => a[0] - b[0]);
  const activity = latestActivity(dispatch);
  const statusColor = dispatch.status === "completed" ? "text-emerald-300"
    : dispatch.status === "error" ? "text-red-300"
    : "text-amber-300";
  const StatusIcon = dispatch.status === "completed" ? CheckCircle2
    : dispatch.status === "error" ? XCircle
    : activity?.label === "queued on local backend" ? Clock3
      : Loader2;
  return (
    <div className={cn(
      "my-2 rounded-md border border-neutral-800 bg-neutral-950/70",
      dispatch.status === "running" && "border-amber-500/30 bg-amber-950/10",
    )}>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-xs text-neutral-300 hover:bg-neutral-900/70 hover:text-neutral-100"
      >
        <span className="w-3 shrink-0 text-neutral-500">{open ? "▾" : "▸"}</span>
        <StatusIcon
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            statusColor,
            dispatch.status === "running" && activity?.label !== "queued on local backend" && "animate-spin",
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="font-medium text-neutral-200">dispatch_specialist</span>
          <span className="text-neutral-600">[</span>
          <span className="text-fuchsia-300">{dispatch.specialty}</span>
          <span className="text-neutral-600">]</span>
          <span className={cn("ml-2", statusColor)}>{dispatch.status}</span>
          {activity && <span className="ml-2 text-neutral-500">· {activity.label}</span>}
        </span>
        <span className="hidden shrink-0 text-neutral-500 sm:inline">
          {dispatch.cost !== undefined && `$${dispatch.cost.toFixed(4)}`}
          {dispatch.cost !== undefined && dispatch.turnsConsumed !== undefined && " · "}
          {dispatch.turnsConsumed !== undefined && `${dispatch.turnsConsumed} turns`}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-neutral-800/80 px-3 py-2">
          <div className="text-xs text-neutral-500">Targeted task: {dispatch.subGoal}</div>
          {activity && (
            <div className="rounded border border-neutral-800 bg-neutral-900/60 px-2 py-1.5 text-xs">
              <span className={cn("font-medium", statusColor)}>{activity.label}</span>
              <span className="ml-2 text-neutral-400">{trimDetail(activity.detail)}</span>
            </div>
          )}
          {subTurns.map(([n, st]) => (
            <SubTurnBubble
              key={n}
              subTurn={st}
              num={n}
              specialty={dispatch.specialty}
            />
          ))}
        </div>
      )}
    </div>
  );
}
