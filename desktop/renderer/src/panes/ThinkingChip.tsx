// desktop/renderer/src/panes/ThinkingChip.tsx
import { useState } from "react";
import { Brain } from "lucide-react";

export function ThinkingChip({ deltas }: { deltas: string[] }) {
  const [open, setOpen] = useState(false);
  if (deltas.length === 0) return null;
  return (
    <div className="text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 rounded border border-neutral-800 bg-neutral-900/45 px-2 py-1 text-neutral-500 hover:text-neutral-300"
      >
        <Brain className="h-3.5 w-3.5 text-amber-300/70" />
        {open ? "▾" : "▸"} thinking [{open ? "hide" : `show ${deltas.length}`}]
      </button>
      {open && (
        <pre className="mt-2 rounded border border-neutral-800 bg-neutral-950/70 p-3 whitespace-pre-wrap italic text-neutral-500">
          {deltas.join("\n\n")}
        </pre>
      )}
    </div>
  );
}
