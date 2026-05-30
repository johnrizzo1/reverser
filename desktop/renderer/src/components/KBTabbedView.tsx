import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { cn } from "@/lib/utils";
import { FindingRow } from "@/components/FindingRow";
import { ReportTab } from "@/panes/ReportTab";

type Tab = "findings" | "hypotheses" | "hosts" | "services" | "credentials" | "artifacts" | "notes" | "report";

const TABS: Tab[] = [
  "findings",
  "hypotheses",
  "hosts",
  "services",
  "credentials",
  "artifacts",
  "notes",
  "report",
];

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
  if (tab === "hosts") {
    return (
      <div className="space-y-2 text-xs">
        {rows.map((r, i) => (
          <RecordCard
            key={i}
            title={String(r.ip ?? "host")}
            subtitle={compactJoin([r.hostname, r.os, r.domain])}
            fields={[
              ["domain", r.domain],
              ["os", r.os],
              ["dc", typeof r.is_dc === "boolean" ? (r.is_dc ? "yes" : "no") : r.is_dc],
              ["smb signing", r.smb_signing],
            ]}
          />
        ))}
      </div>
    );
  }
  if (tab === "services") {
    return (
      <div className="space-y-2 text-xs">
        {rows.map((r, i) => (
          <RecordCard
            key={i}
            title={`${String(r.host_ip ?? "host")}:${String(r.port ?? "?")}/${String(r.proto ?? "tcp")}`}
            subtitle={compactJoin([r.service, r.version])}
            fields={[
              ["service", r.service],
              ["version", r.version],
              ["banner", r.banner],
              ["source", r.scan_source],
            ]}
          />
        ))}
      </div>
    );
  }
  if (tab === "credentials") {
    return (
      <div className="space-y-2 text-xs">
        {rows.map((r, i) => (
          <RecordCard
            key={i}
            title={compactJoin([r.domain, r.username], "\\") || String(r.username ?? "credential")}
            subtitle={String(r.status ?? "untested")}
            fields={[
              ["status", r.status],
              ["password", r.password],
              ["nt hash", r.nt_hash],
              ["kerberos ticket", r.kerberos_ticket],
              ["source", compactJoin([r.source_tool, r.source_context], " · ")],
            ]}
          />
        ))}
      </div>
    );
  }
  if (tab === "artifacts") {
    return (
      <div className="space-y-2 text-xs">
        {rows.map((r, i) => (
          <RecordCard
            key={i}
            title={String(r.path ?? "artifact")}
            subtitle={String(r.kind ?? "")}
            fields={[
              ["kind", r.kind],
              ["sha256", r.sha256],
              ["source", r.source_tool],
            ]}
          />
        ))}
      </div>
    );
  }
  if (tab === "notes") {
    return (
      <div className="space-y-2 text-xs">
        {rows.map((r, i) => (
          <div
            key={i}
            className="rounded-md border border-neutral-800 bg-neutral-950/80 p-2 text-neutral-300"
          >
            <p className="whitespace-pre-wrap break-words leading-5">
              {String(r.body ?? r.note ?? r)}
            </p>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-2 text-xs">
      {rows.slice(0, 200).map((r, i) => (
        <RecordCard
          key={i}
          title={`row ${i + 1}`}
          fields={Object.entries(r)}
        />
      ))}
    </div>
  );
}

function compactJoin(
  values: unknown[],
  separator = " · ",
): string {
  return values
    .map((v) => (v === null || v === undefined ? "" : String(v)))
    .filter(Boolean)
    .join(separator);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function RecordCard({
  title,
  subtitle,
  fields,
}: {
  title: string;
  subtitle?: string;
  fields: Array<[string, unknown]>;
}) {
  const visibleFields = fields.filter(([, value]) => {
    if (value === null || value === undefined || value === "") return false;
    if (Array.isArray(value) && value.length === 0) return false;
    return true;
  });

  return (
    <div className="rounded-md border border-neutral-800 bg-neutral-950/80 p-2 shadow-sm">
      <div className="min-w-0">
        <div className="break-words font-medium leading-5 text-neutral-100">
          {title}
        </div>
        {subtitle && (
          <div className="mt-0.5 break-words leading-5 text-neutral-400">
            {subtitle}
          </div>
        )}
      </div>
      {visibleFields.length > 0 && (
        <dl className="mt-2 grid grid-cols-[minmax(5rem,8rem)_minmax(0,1fr)] gap-x-3 gap-y-1 text-[11px] leading-5">
          {visibleFields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-neutral-500">{label}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words font-mono text-neutral-300">
                {formatValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
