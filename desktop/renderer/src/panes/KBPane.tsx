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
    <div className="space-y-3 p-3 text-xs">
      {sections.map(([label, rows]) => (
        <section key={label} className="rounded-md border border-neutral-800 bg-neutral-900/35 p-2">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-neutral-500">
            {label} · {rows?.length ?? 0}
          </div>
          <div className="space-y-2">
            {(rows ?? []).slice(0, 50).map((r, i) => (
              <KBRecord key={i} section={label} row={r as Record<string, unknown>} />
            ))}
            {(rows?.length ?? 0) === 0 && (
              <p className="text-xs text-neutral-600">empty</p>
            )}
          </div>
        </section>
      ))}
    </div>
  );
}

function KBRecord({
  section,
  row,
}: {
  section: string;
  row: Record<string, unknown>;
}) {
  if (section === "hosts") {
    return (
      <RecordCard
        title={String(row.ip ?? "host")}
        subtitle={compactJoin([row.hostname, row.os, row.domain])}
        fields={[
          ["domain", row.domain],
          ["os", row.os],
          ["dc", typeof row.is_dc === "boolean" ? (row.is_dc ? "yes" : "no") : row.is_dc],
          ["smb signing", row.smb_signing],
        ]}
      />
    );
  }
  if (section === "services") {
    return (
      <RecordCard
        title={`${String(row.host_ip ?? "host")}:${String(row.port ?? "?")}/${String(row.proto ?? "tcp")}`}
        subtitle={compactJoin([row.service, row.version])}
        fields={[
          ["service", row.service],
          ["version", row.version],
          ["banner", row.banner],
          ["source", row.scan_source],
        ]}
      />
    );
  }
  if (section === "credentials") {
    return (
      <RecordCard
        title={compactJoin([row.domain, row.username], "\\") || String(row.username ?? "credential")}
        subtitle={String(row.status ?? "untested")}
        fields={[
          ["status", row.status],
          ["password", row.password],
          ["nt hash", row.nt_hash],
          ["kerberos ticket", row.kerberos_ticket],
          ["source", compactJoin([row.source_tool, row.source_context], " · ")],
        ]}
      />
    );
  }
  if (section === "artifacts") {
    return (
      <RecordCard
        title={String(row.path ?? "artifact")}
        subtitle={String(row.kind ?? "")}
        fields={[
          ["kind", row.kind],
          ["sha256", row.sha256],
          ["source", row.source_tool],
        ]}
      />
    );
  }
  if (section === "notes") {
    return (
      <div className="rounded-md border border-neutral-800 bg-neutral-950/80 p-2 text-neutral-300">
        <p className="whitespace-pre-wrap break-words leading-5">
          {String(row.body ?? row.note ?? formatValue(row))}
        </p>
      </div>
    );
  }
  return <RecordCard title="row" fields={Object.entries(row)} />;
}

function compactJoin(values: unknown[], separator = " · "): string {
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
