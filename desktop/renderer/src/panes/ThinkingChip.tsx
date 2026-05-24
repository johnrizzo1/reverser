// desktop/renderer/src/panes/ThinkingChip.tsx
import { useState } from "react";

export function ThinkingChip({ deltas }: { deltas: string[] }) {
  const [open, setOpen] = useState(false);
  if (deltas.length === 0) return null;
  return (
    <div className="text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="text-neutral-500 hover:text-neutral-300"
      >
        {open ? "▾" : "▸"} thinking [{open ? "hide" : `show ${deltas.length}`}]
      </button>
      {open && (
        <pre className="mt-1 pl-4 whitespace-pre-wrap italic text-neutral-500">
          {deltas.join("\n\n")}
        </pre>
      )}
    </div>
  );
}
