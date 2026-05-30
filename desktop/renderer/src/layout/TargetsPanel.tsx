import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Archive, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";
import {
  useArchiveTarget,
  useDeleteTarget,
  useSessions,
  useTargets,
  useUnarchiveTarget,
} from "@/api/queries";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ArchiveConfirmModal } from "@/modals/ArchiveConfirmModal";
import { DeleteConfirmModal } from "@/modals/DeleteConfirmModal";

type Sort = "activity" | "name";

type Row = {
  name: string;
  archived: boolean;
  sessions: number;
  total_cost: number;
  last_activity: string;
  any_active: boolean;
};

function TargetRow({
  r,
  active,
}: {
  r: Row;
  active: boolean;
}) {
  const archiveMutation = useArchiveTarget();
  const unarchiveMutation = useUnarchiveTarget();
  const deleteMutation = useDeleteTarget();

  const [showArchive, setShowArchive] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className={cn("relative group", menuOpen && "z-20")}>
      <Link
        to={`/target/${encodeURIComponent(r.name)}`}
        className={cn(
          "block px-3 py-2 border-l-2 transition-colors",
          active
            ? "border-neutral-300 bg-neutral-800/60"
            : "border-transparent hover:bg-neutral-900",
          r.archived && "bg-neutral-950/60 opacity-75",
        )}
      >
        <div className="text-xs text-neutral-200 truncate">{r.name}</div>
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
          <span className={r.any_active ? "text-green-400" : ""}>
            {r.any_active ? "● active" : r.archived ? "■" : "○"}
          </span>
          <span>·</span>
          <span>
            {r.sessions} session{r.sessions === 1 ? "" : "s"}
          </span>
          <span>·</span>
          <span>${r.total_cost.toFixed(2)}</span>
          {r.archived && (
            <>
              <span>·</span>
              <span className="text-neutral-400">archived</span>
            </>
          )}
        </div>
      </Link>

      <div
        className={cn(
          "absolute right-2 top-1/2 -translate-y-1/2 z-10 flex items-center gap-1",
          "opacity-0 group-hover:opacity-100 transition-opacity",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {r.archived ? (
          <button
            title="Restore"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              unarchiveMutation.mutate(r.name);
            }}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            title={r.any_active ? "Stop the session first" : "Archive"}
            disabled={r.any_active}
            className={cn(
              "p-1 rounded text-neutral-300",
              r.any_active
                ? "opacity-30 cursor-not-allowed"
                : "hover:bg-neutral-700",
            )}
            onClick={(e) => {
              e.preventDefault();
              if (!r.any_active) setShowArchive(true);
            }}
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}

        <div className="relative">
          <button
            title="More"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              setMenuOpen((v) => !v);
            }}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-10 min-w-[180px] rounded border border-neutral-700 bg-neutral-900 shadow-lg text-xs"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                disabled={r.any_active}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left",
                  r.any_active
                    ? "text-neutral-500 cursor-not-allowed"
                    : "text-red-400 hover:bg-neutral-800",
                )}
                onClick={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  if (!r.any_active) setShowDelete(true);
                }}
                title={r.any_active ? "Stop the session first" : undefined}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete permanently
              </button>
            </div>
          )}
        </div>
      </div>

      <ArchiveConfirmModal
        open={showArchive}
        onOpenChange={setShowArchive}
        title="Archive this target?"
        description="This hides it from the default view. You can find it via the 'Show archived' toggle and restore at any time."
        onConfirm={() => archiveMutation.mutateAsync(r.name)}
      />
      <DeleteConfirmModal
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this target?"
        description="The directory will be moved to targets/.trash/ for 30 days. It won't appear in the UI. Recovery requires filesystem access; after 30 days the trash entry is pruned."
        onConfirm={() => deleteMutation.mutateAsync(r.name)}
      />
    </div>
  );
}

export function TargetsPanel({
  targetNames,
  emptyMessage = "no targets yet",
}: {
  targetNames?: readonly string[];
  emptyMessage?: string;
}) {
  const { name: routeName } = useParams<{ name: string }>();
  const targets = useTargets();
  const sessions = useSessions();
  const [sort, setSort] = useState<Sort>("activity");
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const rows: Row[] = useMemo(() => {
    const list = targets.data?.targets ?? [];
    const sess = sessions.data?.sessions ?? [];

    const targetNameSet = targetNames === undefined ? null : new Set(targetNames);
    const scoped = targetNameSet === null
      ? list
      : list.filter((t) => targetNameSet.has(t.name));

    const summarized: Row[] = scoped.map((t) => {
      const ts = sess.filter((s) => s.target === t.name || s.target_name === t.name);
      const last =
        ts
          .map((s) => s.stopped_at ?? "")
          .filter(Boolean)
          .sort()
          .at(-1) ?? "";
      const totalCost = ts.reduce((acc, s) => acc + (s.total_cost ?? 0), 0);
      const anyActive = ts.some((s) => s.state === "active");
      return {
        name: t.name,
        archived: t.archived,
        sessions: ts.length,
        total_cost: totalCost,
        last_activity: last,
        any_active: anyActive,
      };
    });

    const visible = showArchived
      ? summarized
      : summarized.filter((r) => !r.archived);

    const q = query.trim().toLowerCase();
    let filtered = q
      ? visible.filter((r) => r.name.toLowerCase().includes(q))
      : visible;

    filtered = filtered.slice().sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      return (b.last_activity ?? "").localeCompare(a.last_activity ?? "");
    });

    return filtered;
  }, [targets.data, sessions.data, sort, query, showArchived, targetNames]);

  return (
    <div className="h-full flex flex-col bg-neutral-950 border-r border-neutral-800">
      <div className="p-3 border-b border-neutral-800">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Targets
        </div>
        <div className="flex items-center gap-3 text-[10px] mb-2">
          <button
            onClick={() => setSort("activity")}
            className={cn(
              sort === "activity"
                ? "text-neutral-100 border-b border-neutral-100"
                : "text-neutral-500 hover:text-neutral-300",
            )}
          >
            by activity
          </button>
          <button
            onClick={() => setSort("name")}
            className={cn(
              sort === "name"
                ? "text-neutral-100 border-b border-neutral-100"
                : "text-neutral-500 hover:text-neutral-300",
            )}
          >
            by name
          </button>
          <label className="ml-auto flex items-center gap-1 text-neutral-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="h-3 w-3"
            />
            Show archived
          </label>
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
          <p className="p-3 text-xs text-neutral-500">{emptyMessage}</p>
        ) : (
          rows.map((r) => (
            <TargetRow key={r.name} r={r} active={r.name === routeName} />
          ))
        )}
      </div>
    </div>
  );
}
