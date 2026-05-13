import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";
import { Check, Loader2, X } from "lucide-react";

export function ToolTimelinePane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const toolCalls = useStore(store, (s) => s.toolCalls);

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 h-7 flex items-center border-b border-neutral-800 text-[10px] uppercase tracking-wide text-neutral-500">
        tool timeline
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2 space-y-1 text-xs font-mono">
        {toolCalls.length === 0 && (
          <p className="text-neutral-500 px-2">no tools called yet</p>
        )}
        {toolCalls.map((c) => (
          <div key={c.id} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              {!c.result ? (
                <Loader2 className="w-3 h-3 animate-spin text-amber-400" />
              ) : c.result.ok ? (
                <Check className="w-3 h-3 text-green-400" />
              ) : (
                <X className="w-3 h-3 text-red-400" />
              )}
              <span className="text-neutral-200">{c.name}</span>
              <span className="text-neutral-500 truncate">{c.args.slice(0, 80)}</span>
            </div>
            {c.result && (
              <pre className="mt-1 text-neutral-400 text-[10px] whitespace-pre-wrap break-all line-clamp-4">
                {c.result.preview}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
