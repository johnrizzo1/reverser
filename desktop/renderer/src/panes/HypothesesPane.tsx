import { useEffect, useMemo, useState } from "react";
import { useStore } from "zustand";
import { useTargetKB, useSessions } from "@/api/queries";
import { useTarget } from "@/state/targets-store";
import { getSessionStore, type HypothesisRow } from "@/state/session-store";
import { cn } from "@/lib/utils";
import { FlaskConical } from "lucide-react";

const STATUS_COLOR: Record<string, string> = {
  confirmed: "text-green-400",
  testing: "text-amber-400",
  proposed: "text-neutral-400",
  refuted: "text-red-400",
  abandoned: "text-neutral-600",
};

type TreeNode = {
  id: string;
  numericId: number;
  row: HypothesisRow;
  children: TreeNode[];
};

function _buildTree(rows: HypothesisRow[]): TreeNode[] {
  const byId = new Map<number, TreeNode>();
  for (const r of rows) {
    byId.set(r.id, { id: String(r.id), numericId: r.id, row: r, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = node.row.parent_id;
    if (pid != null && byId.has(pid)) {
      byId.get(pid)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortRec = (ns: TreeNode[]) => {
    ns.sort((a, b) => a.numericId - b.numericId);
    for (const n of ns) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

function _ancestorIds(rows: HypothesisRow[], id: number): number[] {
  const byId = new Map<number, HypothesisRow>();
  for (const r of rows) byId.set(r.id, r);
  const out: number[] = [];
  let cur: HypothesisRow | undefined = byId.get(id);
  while (cur && cur.parent_id != null) {
    out.push(cur.parent_id);
    cur = byId.get(cur.parent_id);
  }
  return out;
}

function HypothesisRowView({
  node,
  depth,
  openIds,
  setOpen,
}: {
  node: TreeNode;
  depth: number;
  openIds: Record<string, boolean>;
  setOpen: (id: string, open: boolean) => void;
}) {
  const r = node.row;
  const status = (r.status ?? "proposed").toLowerCase();
  const isRefuted = status === "refuted";
  const childCount = node.children.length;
  const hasChildren = childCount > 0;
  const isOpen = !!openIds[node.id];
  return (
    <>
      <div
        className="flex items-start gap-1 border-b border-neutral-900/80 text-xs cursor-pointer py-1.5 hover:bg-neutral-900/55"
        style={{ paddingLeft: 8 + depth * 16, paddingRight: 8 }}
        onClick={() => hasChildren && setOpen(node.id, !isOpen)}
      >
        <span className="text-neutral-500 w-3 text-center shrink-0 leading-5">
          {hasChildren ? (isOpen ? "▼" : "▶") : ""}
        </span>
        <span className={cn("shrink-0 leading-5", STATUS_COLOR[status] ?? "text-neutral-400")}>
          ●
        </span>
        <span
          className={cn(
            "text-neutral-200 min-w-0 flex-1 break-words leading-5",
            isRefuted && "line-through opacity-60",
          )}
        >
          {r.statement || "—"}
        </span>
        <span className="text-[10px] text-neutral-500 font-mono shrink-0 leading-5 whitespace-nowrap">
          {(r.dispatch_count ?? 0) > 0 && `${r.dispatch_count} disp · `}
          {childCount > 0 && `${childCount} child${childCount === 1 ? "" : "ren"}`}
          {childCount === 0 && status}
        </span>
      </div>
      {isOpen &&
        node.children.map((c) => (
          <HypothesisRowView
            key={c.id}
            node={c}
            depth={depth + 1}
            openIds={openIds}
            setOpen={setOpen}
          />
        ))}
    </>
  );
}

export function HypothesesPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const hypothesesMap = useStore(store, (s) => s.hypotheses);
  const seedHypotheses = useStore(store, (s) => s.seedHypotheses);
  const sessions = useSessions();
  const sessionRow = sessions.data?.sessions.find((s) => s.id === sessionId);
  // Prefer the logical target name (new field); fall back to the raw target string.
  const targetName = sessionRow?.target_name || sessionRow?.target || null;
  const targetQuery = useTarget(targetName);
  const targetDetail = targetQuery.data;
  // Resolve the primary address value for the KB lookup.
  const primaryAddressValue = targetDetail
    ? (targetDetail.addresses.find((a) => a.id === targetDetail.primary_address_id)?.value ?? null)
    : targetName;
  const kb = useTargetKB(primaryAddressValue);

  useEffect(() => {
    const kbHypotheses = (kb.data?.hypotheses ?? []) as HypothesisRow[];
    if (kbHypotheses.length > 0) seedHypotheses(kbHypotheses);
  }, [kb.data, seedHypotheses]);

  const rows = useMemo(() => Array.from(hypothesesMap.values()), [hypothesesMap]);
  const tree = useMemo(() => _buildTree(rows), [rows]);

  const [openIds, setOpenIds] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const toOpen: Record<string, boolean> = {};
    for (const r of rows) {
      if (r.status === "testing" || r.status === "confirmed") {
        for (const aid of _ancestorIds(rows, r.id)) {
          toOpen[String(aid)] = true;
        }
      }
    }
    if (Object.keys(toOpen).length > 0) {
      setOpenIds((prev) => ({ ...prev, ...toOpen }));
    }
  }, [rows]);

  const setOpen = (id: string, open: boolean) =>
    setOpenIds((prev) => ({ ...prev, [id]: open }));

  if (rows.length === 0) {
    return (
      <div className="flex h-full min-h-[14rem] items-center justify-center p-4 text-center">
        <div className="max-w-xs">
          <FlaskConical className="mx-auto mb-2 h-5 w-5 text-cyan-300/70" />
          <p className="text-sm font-medium text-neutral-200">No hypotheses yet</p>
          <p className="mt-1 text-xs leading-5 text-neutral-500">
            Agent-generated assumptions and test branches will appear here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto py-1">
      {tree.map((n) => (
        <HypothesisRowView
          key={n.id}
          node={n}
          depth={0}
          openIds={openIds}
          setOpen={setOpen}
        />
      ))}
    </div>
  );
}
