// desktop/renderer/src/panes/SubTurnBubble.tsx
import type { SubTurn } from "@/state/session-store";
import { ThinkingChip } from "./ThinkingChip";
import { SpeechBlock } from "./SpeechBlock";

export function SubTurnBubble({ subTurn, num }: { subTurn: SubTurn; num: number }) {
  return (
    <div className="border-l border-neutral-800 pl-2 space-y-1 text-xs">
      <div className="text-neutral-600">sub-turn {num}</div>
      <ThinkingChip deltas={subTurn.thinkingDeltas} />
      <SpeechBlock deltas={subTurn.speechDeltas} />
      {subTurn.toolCalls.map((tc, i) => (
        <div key={`tc-${i}`} className="font-mono text-cyan-400/80">
          → {tc.content}
        </div>
      ))}
      {subTurn.toolResults.map((tr, i) => (
        <div
          key={`tr-${i}`}
          className={`font-mono ${tr.ok ? "text-green-400/70" : "text-red-400/70"} whitespace-pre-wrap`}
        >
          {tr.content}
        </div>
      ))}
    </div>
  );
}
