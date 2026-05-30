// desktop/renderer/src/panes/ToolCallChip.tsx
import { useState } from "react";
import { TerminalSquare } from "lucide-react";
import type { ToolCall } from "@/state/session-store";
import { cn } from "@/lib/utils";

export function ToolCallChip({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const ok = call.result?.ok;
  const status = call.result === undefined ? "…" : ok ? "✓" : "✗";
  const argsPreview = call.args.replace(/\s+/g, " ").slice(0, 80);
  return (
    <div className="rounded-md border border-neutral-800 bg-neutral-900/45 px-2.5 py-2 text-xs font-mono">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full min-w-0 items-center gap-2 text-left text-neutral-400 hover:text-neutral-200"
      >
        <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-cyan-300/80" />
        <span className={cn("shrink-0", ok === false ? "text-red-400" : ok ? "text-green-400" : "text-neutral-500")}>
          {status}
        </span>
        <span className="shrink-0 text-cyan-400">{call.name}</span>
        <span className="min-w-0 truncate text-neutral-500">{argsPreview}{call.args.length > 80 ? "…" : ""}</span>
      </button>
      {open && (
        <div className="mt-1 pl-4 space-y-2">
          <pre className="text-neutral-400 whitespace-pre-wrap">{call.args}</pre>
          {call.result && (
            <pre className={`whitespace-pre-wrap ${call.result.ok ? "text-green-400/80" : "text-red-400/80"}`}>
              {call.result.preview}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
