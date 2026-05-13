import { useTargetKB } from "@/api/queries";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-blue-400",
  info: "text-neutral-500",
};

export function FindingsPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;
  const findings = (data?.findings ?? []) as Array<Record<string, unknown>>;
  if (findings.length === 0) return <p className="p-3 text-xs text-neutral-500">no findings yet</p>;
  return (
    <div className="p-2 text-xs space-y-1 font-mono">
      {findings.map((f, i) => {
        const sev = String(f.severity ?? "info").toLowerCase();
        return (
          <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              <span className={SEVERITY_COLOR[sev] ?? "text-neutral-500"}>● {sev}</span>
              <span className="text-neutral-200">{String(f.title ?? "—")}</span>
            </div>
            {!!f.description && (
              <p className="text-neutral-400 mt-1 line-clamp-3">{String(f.description)}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
