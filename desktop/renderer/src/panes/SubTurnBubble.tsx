// desktop/renderer/src/panes/SubTurnBubble.tsx
import { Brain, Terminal, CheckCircle2, XCircle, MessageSquare } from "lucide-react";
import type { SubTurn } from "@/state/session-store";
import { SpeechBlock } from "./SpeechBlock";
import { cn } from "@/lib/utils";

function specialistLabel(specialty: string): string {
  return `${specialty.toUpperCase()} sub-agent`;
}

function trim(value: string, max = 320): string {
  const oneLine = value.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 3)}...` : oneLine;
}

export function SubTurnBubble({
  subTurn,
  num,
  specialty,
}: {
  subTurn: SubTurn;
  num: number;
  specialty: string;
}) {
  return (
    <div className="rounded border border-neutral-800 bg-neutral-950/60 text-xs">
      <div className="flex items-center gap-2 border-b border-neutral-800 px-2 py-1.5">
        <span className="font-medium text-fuchsia-300">{specialistLabel(specialty)}</span>
        <span className="text-neutral-600">specialist activity</span>
        <span className="ml-auto text-neutral-600">sub-turn {num}</span>
      </div>
      <div className="space-y-1.5 px-2 py-2">
        {subTurn.thinkingDeltas.map((delta, i) => (
          <div key={`th-${i}`} className="flex gap-2 text-neutral-500">
            <Brain className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300/80" />
            <span className="italic">{trim(delta)}</span>
          </div>
        ))}
        <SpeechBlock deltas={subTurn.speechDeltas} />
        {subTurn.toolCalls.map((tc, i) => (
          <div key={`tc-${i}`} className="flex gap-2 font-mono text-cyan-300/90">
            <Terminal className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 whitespace-pre-wrap break-words">{tc.content}</span>
          </div>
        ))}
        {subTurn.toolResults.map((tr, i) => (
          <div
            key={`tr-${i}`}
            className={cn(
              "flex gap-2 font-mono whitespace-pre-wrap",
              tr.ok ? "text-emerald-300/80" : "text-red-300/80",
            )}
          >
            {tr.ok
              ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
            <span className="min-w-0 break-words">{tr.content}</span>
          </div>
        ))}
        {subTurn.thinkingDeltas.length === 0
          && subTurn.speechDeltas.length === 0
          && subTurn.toolCalls.length === 0
          && subTurn.toolResults.length === 0 && (
            <div className="flex gap-2 text-neutral-600">
              <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              waiting for specialist activity
            </div>
          )}
      </div>
    </div>
  );
}
