// desktop/renderer/src/panes/TurnBubble.tsx
import type { Turn } from "@/state/session-store";
import { ThinkingChip } from "./ThinkingChip";
import { SpeechBlock } from "./SpeechBlock";
import { ToolCallChip } from "./ToolCallChip";
import { DispatchPanel } from "./DispatchPanel";

export function TurnBubble({ turn }: { turn: Turn }) {
  // Consolidate thinking into one chip per turn (render only on first encounter).
  let thinkingRendered = false;

  return (
    <div className="border-l border-neutral-700 pl-3 py-1 space-y-2">
      <div className="text-[10px] text-neutral-500 uppercase tracking-wide">
        turn {turn.turn}
        {turn.status === "streaming" && <span className="ml-2 text-amber-400">●</span>}
      </div>
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
  );
}
