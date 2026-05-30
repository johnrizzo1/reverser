import { useTargetKB } from "@/api/queries";
import { Database } from "lucide-react";

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex h-full min-h-[14rem] items-center justify-center p-4 text-center">
      <div className="max-w-xs">
        <Database className="mx-auto mb-2 h-5 w-5 text-cyan-300/70" />
        <p className="text-sm font-medium text-neutral-200">{title}</p>
        <p className="mt-1 text-xs leading-5 text-neutral-500">{detail}</p>
      </div>
    </div>
  );
}

export function KBPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  if (!target) {
    return (
      <EmptyState
        title="No target selected"
        detail="Engagement target knowledge will appear after a target is available."
      />
    );
  }
  if (isLoading) {
    return (
      <EmptyState
        title="Loading knowledge base"
        detail="Target facts, artifacts, and notes are being loaded."
      />
    );
  }
  const sections = [
    ["hosts", data?.hosts],
    ["services", data?.services],
    ["credentials", data?.credentials],
    ["artifacts", data?.artifacts],
    ["notes", data?.notes],
  ] as const;
  return (
    <div className="space-y-3 p-3 text-xs font-mono">
      {sections.map(([label, rows]) => (
        <div key={label} className="rounded-md border border-neutral-800 bg-neutral-900/35 p-2">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-neutral-500">
            {label} · {rows?.length ?? 0}
          </div>
          <div className="space-y-1">
            {(rows ?? []).slice(0, 50).map((r, i) => (
              <pre key={i} className="text-neutral-400 truncate" title={JSON.stringify(r)}>
                {JSON.stringify(r)}
              </pre>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
