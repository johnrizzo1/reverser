import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { cn } from "@/lib/utils";
import { FindingRow } from "@/components/FindingRow";
import { ReportTab } from "@/panes/ReportTab";

type Tab = "findings" | "hypotheses" | "hosts" | "services" | "credentials" | "report";

const TABS: Tab[] = ["findings", "hypotheses", "hosts", "services", "credentials", "report"];

export function KBTabbedView({
  target,
  onClickEvidence,
}: {
  target: string | null;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("findings");
  const { data, isLoading } = useTargetKB(target);

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;

  return (
    <div className="flex flex-col h-full">
      <div className="flex gap-3 px-3 border-b border-neutral-800 text-[10px] uppercase tracking-wide h-7 items-center">
        {TABS.map((t) => {
          const count = t === "report" ? null : ((data?.[t as keyof typeof data] ?? []) as unknown[]).length;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "transition-colors",
                t === tab ? "text-neutral-200" : "text-neutral-500 hover:text-neutral-300",
              )}
            >
              {t}{count !== null ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {tab === "report" ? (
          <ReportTab target={target} />
        ) : isLoading ? (
          <p className="p-3 text-xs text-neutral-500">loading…</p>
        ) : (
          <div className="p-2">
            <TabContent
              tab={tab}
              data={data}
              target={target}
              onClickEvidence={onClickEvidence}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function TabContent({
  tab,
  data,
  target,
  onClickEvidence,
}: {
  tab: Tab;
  data: any;
  target: string;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  if (tab === "report") return null;
  const rows = (data?.[tab] ?? []) as Array<Record<string, unknown>>;
  if (rows.length === 0) return <p className="text-xs text-neutral-500">empty</p>;
  if (tab === "findings") {
    const sorted = rows.slice().sort((a, b) => {
      const SEV_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
      const av = SEV_ORDER[String(a.severity ?? "info").toLowerCase()] ?? 99;
      const bv = SEV_ORDER[String(b.severity ?? "info").toLowerCase()] ?? 99;
      return av - bv;
    });
    return (
      <div className="space-y-2 text-xs">
        {sorted.map((f, i) => (
          <FindingRow
            key={i}
            target={target}
            finding={f}
            onClickEvidence={onClickEvidence}
          />
        ))}
      </div>
    );
  }
  if (tab === "hypotheses") {
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
  // Generic fallback for hosts/services/credentials.
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
