import { useTargetKB } from "@/api/queries";

export function KBPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;
  const sections = [
    ["hosts", data?.hosts],
    ["services", data?.services],
    ["credentials", data?.credentials],
    ["artifacts", data?.artifacts],
    ["notes", data?.notes],
  ] as const;
  return (
    <div className="p-2 text-xs font-mono space-y-3">
      {sections.map(([label, rows]) => (
        <div key={label}>
          <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1">
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
