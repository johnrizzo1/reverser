import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { cn } from "@/lib/utils";

type Tab = "findings" | "hypotheses" | "hosts" | "services" | "credentials";

const TABS: Tab[] = ["findings", "hypotheses", "hosts", "services", "credentials"];

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3, info: 4,
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-blue-400",
  info: "text-neutral-500",
};

export function KBTabbedView({ target }: { target: string | null }) {
  const [tab, setTab] = useState<Tab>("findings");
  const { data, isLoading } = useTargetKB(target);

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;

  const rows = (data?.[tab] ?? []) as Array<Record<string, unknown>>;

  return (
    <div className="flex flex-col h-full">
      <div className="flex gap-3 px-3 border-b border-neutral-800 text-[10px] uppercase tracking-wide h-7 items-center">
        {TABS.map((t) => {
          const count = ((data?.[t] ?? []) as unknown[]).length;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "transition-colors",
                t === tab ? "text-neutral-200" : "text-neutral-500 hover:text-neutral-300",
              )}
            >
              {t} ({count})
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2">
        {rows.length === 0 ? (
          <p className="text-xs text-neutral-500">empty</p>
        ) : tab === "findings" ? (
          <FindingsTable rows={rows} />
        ) : tab === "hypotheses" ? (
          <HypothesesList rows={rows} />
        ) : (
          <RawTable rows={rows} />
        )}
      </div>
    </div>
  );
}

function FindingsTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const sorted = rows.slice().sort((a, b) => {
    const av = SEVERITY_ORDER[String(a.severity ?? "info").toLowerCase()] ?? 99;
    const bv = SEVERITY_ORDER[String(b.severity ?? "info").toLowerCase()] ?? 99;
    return av - bv;
  });
  return (
    <div className="space-y-2 text-xs">
      {sorted.map((f, i) => {
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

function HypothesesList({ rows }: { rows: Array<Record<string, unknown>> }) {
  const STATUS_COLOR: Record<string, string> = {
    confirmed: "text-green-400",
    testing: "text-amber-400",
    proposed: "text-neutral-400",
    refuted: "text-red-400",
    abandoned: "text-neutral-600",
  };
  return (
    <div className="space-y-1 text-xs font-mono">
      {rows.map((h, i) => {
        const status = String(h.status ?? "proposed").toLowerCase();
        return (
          <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>● {status}</span>
              <span className="text-neutral-200 truncate">
                {String(h.statement ?? h.title ?? "—")}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RawTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  return (
    <div className="space-y-1 text-[10px] font-mono">
      {rows.slice(0, 200).map((r, i) => (
        <pre key={i} className="text-neutral-400 truncate" title={JSON.stringify(r)}>
          {JSON.stringify(r)}
        </pre>
      ))}
    </div>
  );
}
