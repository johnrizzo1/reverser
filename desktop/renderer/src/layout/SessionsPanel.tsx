import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useSessions } from "@/api/queries";
import { SessionRow } from "@/components/SessionRow";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Filter =
  | "all"
  | "active"
  | "stopped"
  | "completed"
  | "abandoned"
  | "archived";

const FILTERS: Filter[] = [
  "all", "active", "stopped", "completed", "abandoned", "archived",
];

export function SessionsPanel() {
  const { id: routeId } = useParams<{ id: string }>();
  const sessions = useSessions();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const all = sessions.data?.sessions ?? [];

  // "all" excludes archived by default — archived has its own tab.
  const visible = useMemo(
    () => all.filter((s) => s.archived_at === null),
    [all],
  );

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: visible.length,
      active: 0, stopped: 0, completed: 0, abandoned: 0,
      archived: 0,
    };
    for (const s of all) {
      if (s.archived_at !== null) c.archived += 1;
      else c[s.state] += 1;
    }
    return c;
  }, [all, visible.length]);

  const filtered = useMemo(() => {
    let rows: typeof all;
    if (filter === "archived") {
      rows = all.filter((s) => s.archived_at !== null);
    } else if (filter === "all") {
      rows = visible;
    } else {
      rows = visible.filter((s) => s.state === filter);
    }
    const q = query.trim().toLowerCase();
    if (q) {
      rows = rows.filter((s) =>
        s.target.toLowerCase().includes(q) ||
        s.profile.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q),
      );
    }
    return rows.slice().sort((a, b) => {
      if (a.state === "active" && b.state !== "active") return -1;
      if (b.state === "active" && a.state !== "active") return 1;
      return (b.stopped_at ?? "").localeCompare(a.stopped_at ?? "");
    });
  }, [all, visible, filter, query]);

  return (
    <div className="h-full flex flex-col bg-neutral-950 border-r border-neutral-800">
      <div className="p-3 border-b border-neutral-800">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Sessions
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] mb-2">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "transition-colors",
                f === filter
                  ? "text-neutral-100 border-b border-neutral-100"
                  : "text-neutral-500 hover:text-neutral-300",
              )}
            >
              {f} ({counts[f]})
            </button>
          ))}
        </div>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter target / profile / id…"
          className="h-7 text-xs"
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {filtered.length === 0 ? (
          <p className="p-3 text-xs text-neutral-500">
            {all.length === 0 ? "no sessions yet" : "no matches"}
          </p>
        ) : (
          filtered.map((s) => (
            <SessionRow key={s.id} session={s} isActive={s.id === routeId} />
          ))
        )}
      </div>
    </div>
  );
}
