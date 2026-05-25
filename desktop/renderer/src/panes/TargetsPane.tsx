/**
 * TargetsPane — browse all targets and inspect their addresses.
 *
 * Shows the new Target model introduced by the target/session decoupling:
 * each target has a kind (network | binary), a list of mutable addresses,
 * and one marked primary.
 *
 * Rendered inside TargetsIndex when no specific target is selected.
 */
import { useState } from "react";
import { useTargetsSummary, useTarget, type AddressDto } from "@/state/targets-store";
import { cn } from "@/lib/utils";

export function TargetsPane(): React.JSX.Element {
  const { data: targets, isLoading, error } = useTargetsSummary();
  const [selected, setSelected] = useState<string | undefined>();

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-neutral-500">
        Loading targets…
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-red-400">
        Failed to load targets: {(error as Error).message}
      </div>
    );
  }

  if (!targets || targets.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-neutral-500">
        No targets yet. Create one from the New Engagement form.
      </div>
    );
  }

  return (
    <div className="h-full flex gap-0 overflow-hidden">
      {/* Left: target list */}
      <ul className="w-56 shrink-0 border-r border-neutral-800 overflow-auto">
        {targets.map((t) => (
          <li key={t.name}>
            <button
              className={cn(
                "w-full text-left px-3 py-2 text-xs border-l-2 transition-colors",
                selected === t.name
                  ? "border-neutral-300 bg-neutral-800/60 text-neutral-100"
                  : "border-transparent hover:bg-neutral-900 text-neutral-300",
                t.archived && "opacity-60",
              )}
              onClick={() => setSelected(t.name)}
            >
              <div className="font-medium truncate">{t.name}</div>
              <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                {t.kind ?? "—"} · {t.address_count} addr
                {t.archived && " · archived"}
              </div>
              {t.primary_address && (
                <div className="text-[10px] text-neutral-600 truncate">
                  {t.primary_address}
                </div>
              )}
            </button>
          </li>
        ))}
      </ul>

      {/* Right: detail */}
      <div className="flex-1 min-w-0 overflow-auto">
        {selected ? (
          <TargetDetail name={selected} />
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-neutral-500">
            Select a target to view its addresses.
          </div>
        )}
      </div>
    </div>
  );
}

function TargetDetail({ name }: { name: string }): React.JSX.Element {
  const { data: target, isLoading, error } = useTarget(name);

  if (isLoading) {
    return (
      <div className="p-4 text-sm text-neutral-500">Loading…</div>
    );
  }

  if (error || !target) {
    return (
      <div className="p-4 text-sm text-red-400">
        {error ? (error as Error).message : "Target not found."}
      </div>
    );
  }

  const primary = target.addresses.find((a) => a.id === target.primary_address_id);

  return (
    <div className="p-4">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-neutral-100 mb-1">{target.name}</h3>
        <div className="text-xs text-neutral-500 space-y-0.5">
          <div>Kind: <span className="text-neutral-300">{target.kind}</span></div>
          <div>
            Primary:{" "}
            <span className="text-neutral-300 font-mono">
              {primary?.value ?? "(none)"}
            </span>
          </div>
          {target.notes && (
            <div>Notes: <span className="text-neutral-300">{target.notes}</span></div>
          )}
        </div>
      </div>

      <h4 className="text-xs uppercase tracking-wide text-neutral-500 mb-2">Addresses</h4>
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-neutral-800 text-neutral-500">
            <th className="text-left py-1 pr-3 font-normal">Value</th>
            <th className="text-left py-1 pr-3 font-normal">Kind</th>
            <th className="text-left py-1 pr-3 font-normal">Status</th>
            <th className="text-left py-1 pr-3 font-normal">Label</th>
            <th className="text-left py-1 font-normal">SHA256</th>
          </tr>
        </thead>
        <tbody>
          {target.addresses.map((a: AddressDto) => (
            <tr
              key={a.id}
              className={cn(
                "border-b border-neutral-800/50",
                a.status === "retired" && "opacity-50",
              )}
            >
              <td className="py-1 pr-3 font-mono text-neutral-200">
                {a.value}
                {a.id === target.primary_address_id && (
                  <span className="ml-1 text-amber-400 text-[10px]">★ primary</span>
                )}
              </td>
              <td className="py-1 pr-3 text-neutral-400">{a.kind}</td>
              <td className="py-1 pr-3">
                <span
                  className={cn(
                    a.status === "active" ? "text-green-400" : "text-neutral-500",
                  )}
                >
                  {a.status}
                </span>
              </td>
              <td className="py-1 pr-3 text-neutral-400">{a.label ?? ""}</td>
              <td className="py-1 font-mono text-[10px] text-neutral-600">
                {a.sha256 ? `${a.sha256.slice(0, 12)}…` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
