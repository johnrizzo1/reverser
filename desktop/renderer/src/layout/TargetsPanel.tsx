import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTargets, useSessions } from "@/api/queries";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Sort = "activity" | "name";

export function TargetsPanel() {
  const { name: routeName } = useParams<{ name: string }>();
  const targets = useTargets();
  const sessions = useSessions();
  const [sort, setSort] = useState<Sort>("activity");
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    const list = targets.data?.targets ?? [];
    const sess = sessions.data?.sessions ?? [];

    const summarized = list.map((t) => {
      const ts = sess.filter((s) => s.target === t.name);
      const last = ts
        .map((s) => s.stopped_at ?? "")
        .filter(Boolean)
        .sort()
        .at(-1) ?? "";
      const totalCost = ts.reduce((acc, s) => acc + (s.total_cost ?? 0), 0);
      const anyActive = ts.some((s) => s.state === "active");
      return {
        name: t.name,
        sessions: ts.length,
        total_cost: totalCost,
        last_activity: last,
        any_active: anyActive,
      };
    });

    const q = query.trim().toLowerCase();
    let filtered = q
      ? summarized.filter((r) => r.name.toLowerCase().includes(q))
      : summarized;

    filtered = filtered.slice().sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      return (b.last_activity ?? "").localeCompare(a.last_activity ?? "");
    });

    return filtered;
  }, [targets.data, sessions.data, sort, query]);

  return (
    <div className="h-full flex flex-col bg-neutral-950 border-r border-neutral-800">
      <div className="p-3 border-b border-neutral-800">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Targets
        </div>
        <div className="flex gap-3 text-[10px] mb-2">
          <button
            onClick={() => setSort("activity")}
            className={cn(sort === "activity"
              ? "text-neutral-100 border-b border-neutral-100"
              : "text-neutral-500 hover:text-neutral-300")}
          >by activity</button>
          <button
            onClick={() => setSort("name")}
            className={cn(sort === "name"
              ? "text-neutral-100 border-b border-neutral-100"
              : "text-neutral-500 hover:text-neutral-300")}
          >by name</button>
        </div>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter…"
          className="h-7 text-xs"
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {rows.length === 0 ? (
          <p className="p-3 text-xs text-neutral-500">no targets yet</p>
        ) : (
          rows.map((r) => (
            <Link
              key={r.name}
              to={`/target/${encodeURIComponent(r.name)}`}
              className={cn(
                "block px-3 py-2 border-l-2 transition-colors",
                r.name === routeName
                  ? "border-neutral-300 bg-neutral-800/60"
                  : "border-transparent hover:bg-neutral-900",
              )}
            >
              <div className="text-xs text-neutral-200 truncate">{r.name}</div>
              <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
                <span className={r.any_active ? "text-green-400" : ""}>
                  {r.any_active ? "● active" : "○"}
                </span>
                <span>·</span>
                <span>{r.sessions} session{r.sessions === 1 ? "" : "s"}</span>
                <span>·</span>
                <span>${r.total_cost.toFixed(2)}</span>
              </div>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}
