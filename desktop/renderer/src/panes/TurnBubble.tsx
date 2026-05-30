// desktop/renderer/src/panes/TurnBubble.tsx
import type { Turn } from "@/state/session-store";
import { ThinkingChip } from "./ThinkingChip";
import { SpeechBlock } from "./SpeechBlock";
import { ToolCallChip } from "./ToolCallChip";
import { DispatchPanel } from "./DispatchPanel";
import { Bot, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

function formatMs(value?: number): string {
  if (value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

function llmStatusText(turn: Turn): string {
  const status = turn.llmStatus;
  if (!status) return "";
  if (status.phase === "prompt_processing") {
    return `prompt processing · ${formatMs(status.elapsedMs)}`;
  }
  if (status.phase === "generating") {
    const firstToken = status.firstTokenMs !== undefined
      ? `first token ${formatMs(status.firstTokenMs)} · `
      : "";
    const rate = status.rateCharsPerSec !== undefined
      ? ` · ${Math.round(status.rateCharsPerSec)} ch/s`
      : "";
    return `${firstToken}${status.generatedChars ?? 0} chars${rate}`;
  }
  return status.phase.replace(/_/g, " ");
}

export function TurnBubble({ turn }: { turn: Turn }) {
  // Consolidate thinking into one chip per turn (render only on first encounter).
  let thinkingRendered = false;

  return (
    <div
      className={cn(
        "overflow-hidden rounded-md border bg-neutral-950/70 shadow-sm",
        turn.status === "streaming"
          ? "border-amber-500/35 shadow-amber-950/20"
          : "border-neutral-800/90",
      )}
    >
      <div className="flex items-center gap-2 border-b border-neutral-800/80 bg-neutral-900/45 px-3 py-2 text-xs">
        <span className="flex h-6 w-6 items-center justify-center rounded-md border border-cyan-400/20 bg-cyan-400/10 text-cyan-200">
          <Bot className="h-3.5 w-3.5" />
        </span>
        <span className="font-medium text-neutral-200">Main agent</span>
        <span className="font-mono text-[10px] uppercase tracking-wide text-neutral-600">
          turn {turn.turn}
        </span>
        {turn.status === "streaming" && (
          <span className="ml-auto inline-flex items-center gap-1.5 rounded border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-200">
            <Loader2 className="h-3 w-3 animate-spin" />
            {turn.llmStatus ? llmStatusText(turn) : "running"}
          </span>
        )}
      </div>
      <div className="space-y-2 px-3 py-3">
        {turn.ordering.map((entry, i) => {
          if (entry.kind === "thinking") {
            if (thinkingRendered) return null;
            thinkingRendered = true;
            return <ThinkingChip key={`th-${i}`} deltas={turn.thinkingDeltas} />;
          }

          if (entry.kind === "speech") {
            // Consolidate consecutive speech entries: only render on a speech
            // entry that follows a non-speech entry (or is the first thing).
            const prevEntry = i > 0 ? turn.ordering[i - 1] : null;
            if (prevEntry && prevEntry.kind === "speech") {
              return null;
            }
            // Find the run: this entry's index up to the next non-speech entry.
            const startIdx = entry.index;
            let endIdx = turn.speechDeltas.length;
            for (let j = i + 1; j < turn.ordering.length; j++) {
              const e = turn.ordering[j];
              if (e.kind !== "speech") {
                // The first non-speech entry after this run bounds endIdx:
                // we use the next speech entry's start as the bound, OR the
                // current length if no further speech.
                const nextSpeech = turn.ordering.slice(j).find((x) => x.kind === "speech");
                if (nextSpeech && nextSpeech.kind === "speech") {
                  endIdx = nextSpeech.index;
                }
                break;
              }
            }
            return (
              <SpeechBlock key={`sp-${i}`} deltas={turn.speechDeltas.slice(startIdx, endIdx)} />
            );
          }

          if (entry.kind === "tool") {
            const tc = turn.toolCalls.get(entry.id);
            if (!tc) return null;
            // Backends should always provide a non-empty tool_use_id; fall
            // back to the ordering index when they don't so React doesn't
            // warn about duplicate keys if a legacy/malformed frame slips in.
            return <ToolCallChip key={`tl-${entry.id || i}`} call={tc} />;
          }

          if (entry.kind === "dispatch") {
            const d = turn.dispatches.get(entry.id);
            if (!d) return null;
            return <DispatchPanel key={`d-${entry.id || i}`} dispatch={d} />;
          }

          return null;
        })}
      </div>
    </div>
  );
}
