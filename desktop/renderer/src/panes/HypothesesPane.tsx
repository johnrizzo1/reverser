import { useEffect, useMemo, useState } from "react";
import { Tree } from "react-arborist";
import type { NodeRendererProps } from "react-arborist";
import { useStore } from "zustand";
import { useTargetKB, useSessions } from "@/api/queries";
import { getSessionStore, type HypothesisRow } from "@/state/session-store";
import { cn } from "@/lib/utils";

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

function HypothesisNode({ node, style, dragHandle }: NodeRendererProps<TreeNode>) {
  const r = node.data.row;
  const status = (r.status ?? "proposed").toLowerCase();
  const isRefuted = status === "refuted";
  const childCount = node.data.children.length;
  return (
    <div
      ref={dragHandle}
      style={style}
      className="flex items-center gap-1 text-xs cursor-pointer"
      onClick={() => node.toggle()}
    >
      <span className="text-neutral-500 w-3 text-center">
        {node.isLeaf ? "" : node.isOpen ? "▼" : "▶"}
      </span>
      <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>●</span>
      <span className={cn(
        "text-neutral-200 truncate",
        isRefuted && "line-through opacity-60",
      )}>
        {r.statement || "—"}
      </span>
      <span className="ml-auto text-[10px] text-neutral-500 font-mono">
        {(r.dispatch_count ?? 0) > 0 && `${r.dispatch_count} disp · `}
        {childCount > 0 && `${childCount} child${childCount === 1 ? "" : "ren"}`}
        {childCount === 0 && status}
      </span>
    </div>
  );
}

export function HypothesesPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const hypothesesMap = useStore(store, (s) => s.hypotheses);
  const seedHypotheses = useStore(store, (s) => s.seedHypotheses);
  const sessions = useSessions();
  const target = sessions.data?.sessions.find((s) => s.id === sessionId)?.target ?? null;
  const kb = useTargetKB(target);

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

  if (rows.length === 0) {
    return <p className="p-3 text-xs text-neutral-500">no hypotheses yet</p>;
  }

  return (
    <div className="h-full overflow-auto p-2">
      <Tree<TreeNode>
        data={tree}
        openByDefault={false}
        initialOpenState={openIds}
        rowHeight={28}
        indent={16}
        width="100%"
        height={600}
      >
        {HypothesisNode}
      </Tree>
    </div>
  );
}
