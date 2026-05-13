# Phase 2 Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cross-target sessions browser, per-target dashboard, and read-only session detail. After this plan, the operator can click 📋 (or 🎯) on the activity bar to browse any session/target ever created, click into a stopped session to review the chat history read-only, and click "Resume engagement" to bring it back to active.

**Architecture:** Two new side-panel components (`SessionsPanel`, `TargetsPanel`) that live under the existing `Shell` layout. One new main-content page (`TargetOverview` at `/target/:name`). A small refactor to `SessionLayout` so it knows when to render in read-only mode. Plus the router restructure: `/sessions/:id` becomes the canonical session route; `/session/:id` is kept as a redirect for Phase 1 bookmarks.

**Tech Stack:** React 18, TypeScript, TanStack Query (existing patterns), Zustand (existing per-session store), react-resizable-panels (already a dep), Playwright (existing e2e harness).

**Depends on:** [Plan 4 — Phase 2 Backend](2026-05-13-plan-4-phase2-backend.md) must be merged (or coexist on the branch) so `/api/targets/{name}/summary` and `/api/sessions/conversation/{id}` exist.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-phase-2-sessions-targets-design.md`](../specs/2026-05-13-phase-2-sessions-targets-design.md).

---

## File map

```
desktop/renderer/src/
  api/client.ts                       modify  (add TargetSummary + Conversation types)
  api/queries.ts                      modify  (add useTargetSummary + useConversation)
  state/session-store.ts              modify  (add seedConversation action)
  components/
    SessionRow.tsx                    create  (shared row, used by SessionsPanel
                                               + TargetOverview Sessions section)
    KBTabbedView.tsx                  create  (Findings/Hyps/Hosts/Services/Creds tabs)
  layout/
    ActivityBar.tsx                   modify  (add Sessions + Targets icons + routes)
    SessionsPanel.tsx                 create
    TargetsPanel.tsx                  create
    SessionLayout.tsx                 modify  (read-only mode, gated WS, conversation seed)
  pages/
    Dashboard.tsx                     modify  (drop "Recent sessions" card)
    SessionsIndex.tsx                 create  ("select a session" placeholder)
    TargetsIndex.tsx                  create  ("select a target" placeholder)
    TargetOverview.tsx                create  (/target/:name page)
  App.tsx                             modify  (new routes, /session/:id redirect)
  tests/e2e/
    phase2.spec.ts                    create  (panel navigation + read-only mode)
```

No new top-level deps. No backend changes (Plan 4 owns those).

---

## Task 1: API types + query hooks for the new endpoints

**Files:**
- Modify: `desktop/renderer/src/api/client.ts`
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Add types to `client.ts`**

Append at the bottom of `desktop/renderer/src/api/client.ts`:

```ts
// ---- Phase 2: Target summary ----

export type TargetSummary = {
  target: string;
  sessions: {
    total: number;
    by_state: {
      active: number;
      stopped: number;
      completed: number;
      abandoned: number;
    };
  };
  spend: { total_usd: number };
  profiles_used: string[];
  first_activity: string | null;
  last_activity: string | null;
  kb_counts: {
    hosts: number;
    services: number;
    credentials: number;
    findings: number;
    hypotheses: number;
    artifacts: number;
    notes: number;
  };
};

// ---- Phase 2: Conversation replay ----

export type ConversationEntry = {
  user: string;
  agent: string;
  turn: number;
  timestamp: string;
  cost: number;
};

export type ConversationResponse = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  conversation: ConversationEntry[];
};
```

- [ ] **Step 2: Add hooks to `queries.ts`**

Append at the bottom of `desktop/renderer/src/api/queries.ts` (after `useTargetKB`):

```ts
import {
  // ... existing imports stay; add these two if not already present:
  type TargetSummary,
  type ConversationResponse,
} from "./client";

export function useTargetSummary(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["target-summary", target],
    queryFn: () =>
      api.get<TargetSummary>(`/api/targets/${encodeURIComponent(target!)}/summary`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useConversation(sessionId: string | null, target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["conversation", sessionId, target],
    queryFn: () =>
      api.get<ConversationResponse>(
        `/api/sessions/conversation/${encodeURIComponent(sessionId!)}` +
        `?target=${encodeURIComponent(target!)}`,
      ),
    enabled: ready && !!sessionId && !!target,
    // Snapshot history doesn't change after a session is stopped/completed,
    // so cache it generously. The hook is mounted only for non-active sessions.
    staleTime: 5 * 60_000,
  });
}
```

The existing `import { … } from "./client";` block at the top of the file may already cover the new types via the `// ... existing imports` convention; if not, add them to the existing import list rather than creating a second one.

- [ ] **Step 3: Compile-check**

Run: `cd desktop && npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/api/
git commit -m "feat(desktop): API types + TanStack Query hooks for /summary and /conversation"
```

---

## Task 2: `seedConversation` action on the session store

The read-only session detail seeds chat history from `useConversation`. The store needs an action to load an array of `ConversationEntry` into the `messages` slot.

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts`

- [ ] **Step 1: Add the action**

Find the `Actions` type and the `ingest`/`appendUserMessage`/`reset` block. Add a new action signature + implementation:

```ts
// In the Actions type:
type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
  seedConversation: (entries: { user: string; agent: string; turn: number }[]) => void;
};

// In the create<>() body, alongside the other actions:
seedConversation: (entries) =>
  set(() => {
    const messages: ChatMessage[] = [];
    for (const e of entries) {
      if (e.user) messages.push({ role: "user", text: e.user, turn: e.turn });
      if (e.agent) messages.push({ role: "assistant", text: e.agent, turn: e.turn });
    }
    return { messages };
  }),
```

Note: `seedConversation` *replaces* `messages` rather than appending, so consecutive seeds (e.g., navigating between two stopped sessions) don't accumulate stale history. The hook caller is responsible for calling it once per session (see Task 9).

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/state/session-store.ts
git commit -m "feat(desktop): session-store seedConversation replaces messages from snapshot"
```

---

## Task 3: `SessionRow` shared component

A compact row component used by both `SessionsPanel` and the Sessions section of `TargetOverview`.

**Files:**
- Create: `desktop/renderer/src/components/SessionRow.tsx`

- [ ] **Step 1: Write the component**

Create `desktop/renderer/src/components/SessionRow.tsx`:

```tsx
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { SessionRow as SessionRowData } from "@/api/client";

const STATE_DOT: Record<SessionRowData["state"], string> = {
  active: "text-green-400",
  stopped: "text-amber-400",
  completed: "text-blue-400",
  abandoned: "text-neutral-500",
};

const STATE_GLYPH: Record<SessionRowData["state"], string> = {
  active: "●",
  stopped: "⏸",
  completed: "✓",
  abandoned: "—",
};

function _formatTime(iso: string | null): string {
  if (!iso) return "—";
  // Strip seconds + timezone for compact display; keep "MM-DD HH:MM".
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!m) return iso;
  return `${m[1].slice(5)} ${m[2]}`;
}

export function SessionRow({
  session,
  isActive = false,
}: {
  session: SessionRowData;
  /** Render with a highlight (e.g., when this row is the current URL). */
  isActive?: boolean;
}) {
  const t = _formatTime(session.stopped_at ?? null);
  return (
    <Link
      to={`/sessions/${session.id}`}
      className={cn(
        "block px-3 py-2 border-l-2 transition-colors",
        isActive
          ? "border-neutral-300 bg-neutral-800/60"
          : "border-transparent hover:bg-neutral-900",
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <span className={STATE_DOT[session.state]}>{STATE_GLYPH[session.state]}</span>
        <span className="text-neutral-200 truncate">{session.target}</span>
      </div>
      <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
        <span>{session.profile}</span>
        <span>·</span>
        <span>{t}</span>
        <span>·</span>
        <span>${session.total_cost.toFixed(2)}</span>
      </div>
    </Link>
  );
}
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/components/SessionRow.tsx
git commit -m "feat(desktop): SessionRow shared component for sessions list rendering"
```

---

## Task 4: `SessionsPanel` (side panel with filter tabs + search)

**Files:**
- Create: `desktop/renderer/src/layout/SessionsPanel.tsx`

- [ ] **Step 1: Write the panel**

Create `desktop/renderer/src/layout/SessionsPanel.tsx`:

```tsx
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useSessions } from "@/api/queries";
import { SessionRow } from "@/components/SessionRow";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { SessionRow as SessionRowData } from "@/api/client";

type Filter = "all" | "active" | "stopped" | "completed" | "abandoned";

const FILTERS: Filter[] = ["all", "active", "stopped", "completed", "abandoned"];

export function SessionsPanel() {
  const { id: routeId } = useParams<{ id: string }>();
  const sessions = useSessions();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const all = sessions.data?.sessions ?? [];

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: all.length, active: 0, stopped: 0, completed: 0, abandoned: 0,
    };
    for (const s of all) c[s.state] += 1;
    return c;
  }, [all]);

  const filtered = useMemo(() => {
    let rows = filter === "all" ? all : all.filter((s) => s.state === filter);
    const q = query.trim().toLowerCase();
    if (q) {
      rows = rows.filter((s) =>
        s.target.toLowerCase().includes(q) ||
        s.profile.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q),
      );
    }
    // Sort: active first, then by stopped_at desc (most recent first).
    return rows.slice().sort((a, b) => {
      if (a.state === "active" && b.state !== "active") return -1;
      if (b.state === "active" && a.state !== "active") return 1;
      return (b.stopped_at ?? "").localeCompare(a.stopped_at ?? "");
    });
  }, [all, filter, query]);

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
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/layout/SessionsPanel.tsx
git commit -m "feat(desktop): SessionsPanel with filter tabs + substring search"
```

---

## Task 5: `TargetsPanel` (side panel for targets)

**Files:**
- Create: `desktop/renderer/src/layout/TargetsPanel.tsx`

- [ ] **Step 1: Write the panel**

Create `desktop/renderer/src/layout/TargetsPanel.tsx`:

```tsx
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
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/layout/TargetsPanel.tsx
git commit -m "feat(desktop): TargetsPanel with sort toggle + substring filter"
```

---

## Task 6: `KBTabbedView` for the TargetOverview right half

**Files:**
- Create: `desktop/renderer/src/components/KBTabbedView.tsx`

- [ ] **Step 1: Write the component**

Create `desktop/renderer/src/components/KBTabbedView.tsx`:

```tsx
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
            {f.description ? (
              <p className="text-neutral-400 mt-1 line-clamp-3">{String(f.description)}</p>
            ) : null}
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
  // Generic fallback: just show JSON one-line per row. Phase 3 can polish hosts/services/creds with proper columns.
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
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/components/KBTabbedView.tsx
git commit -m "feat(desktop): KBTabbedView — Findings / Hyps / Hosts / Services / Creds tabs"
```

---

## Task 7: `TargetOverview` page

**Files:**
- Create: `desktop/renderer/src/pages/TargetOverview.tsx`

- [ ] **Step 1: Write the page**

Create `desktop/renderer/src/pages/TargetOverview.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";
import { useSessions, useTargetSummary } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SessionRow } from "@/components/SessionRow";
import { KBTabbedView } from "@/components/KBTabbedView";

export function TargetOverview() {
  const { name: rawName } = useParams<{ name: string }>();
  const name = rawName ? decodeURIComponent(rawName) : null;
  const summary = useTargetSummary(name);
  const sessions = useSessions();
  const targetSessions = (sessions.data?.sessions ?? []).filter(
    (s) => s.target === name,
  );

  if (!name) return null;

  return (
    <div className="p-6 h-full overflow-auto">
      <div className="flex items-center mb-4 gap-3">
        <h2 className="text-base font-medium text-neutral-100">{name}</h2>
        <Link to={`/new?target=${encodeURIComponent(name)}`} className="ml-auto">
          <Button size="sm">New engagement</Button>
        </Link>
      </div>

      <Card className="mb-4">
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardContent className="text-xs font-mono">
          {summary.isLoading && <p className="text-neutral-500">loading…</p>}
          {summary.error && (
            <p className="text-red-400">
              {String((summary.error as Error).message)}
            </p>
          )}
          {summary.data && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <div>
                <span className="text-neutral-500">sessions: </span>
                <span className="text-neutral-200">
                  {summary.data.sessions.total}
                </span>
                <span className="text-neutral-500">
                  {" "}({summary.data.sessions.by_state.active} active ·{" "}
                  {summary.data.sessions.by_state.stopped} stopped ·{" "}
                  {summary.data.sessions.by_state.completed} done)
                </span>
              </div>
              <div>
                <span className="text-neutral-500">total spend: </span>
                <span className="text-neutral-200">
                  ${summary.data.spend.total_usd.toFixed(2)}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">first activity: </span>
                <span className="text-neutral-200">
                  {summary.data.first_activity ?? "—"}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">last activity: </span>
                <span className="text-neutral-200">
                  {summary.data.last_activity ?? "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">profiles used: </span>
                <span className="text-neutral-200">
                  {summary.data.profiles_used.join(", ") || "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">KB: </span>
                <span className="text-neutral-200">
                  {summary.data.kb_counts.hosts} hosts ·{" "}
                  {summary.data.kb_counts.services} services ·{" "}
                  {summary.data.kb_counts.credentials} creds ·{" "}
                  {summary.data.kb_counts.findings} findings ·{" "}
                  {summary.data.kb_counts.hypotheses} hypotheses
                </span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Sessions</CardTitle></CardHeader>
          <CardContent className="p-0">
            {targetSessions.length === 0 ? (
              <p className="p-3 text-xs text-neutral-500">no sessions for this target</p>
            ) : (
              targetSessions.map((s) => <SessionRow key={s.id} session={s} />)
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Knowledge base</CardTitle></CardHeader>
          <CardContent className="p-0 h-[480px]">
            <KBTabbedView target={name} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```

(`/new?target=…` is a future polish — the wizard doesn't read it yet; that's a Phase 4 nicety. Adding the link now means it'll Just Work when Phase 4 lands without revisiting the page.)

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/pages/TargetOverview.tsx
git commit -m "feat(desktop): TargetOverview page — summary card + sessions list + KB tabs"
```

---

## Task 8: Placeholder index pages

`/sessions` and `/targets` (no `:id` / `:name`) need a "select something" placeholder so the route exists and the activity-bar icon highlights correctly.

**Files:**
- Create: `desktop/renderer/src/pages/SessionsIndex.tsx`
- Create: `desktop/renderer/src/pages/TargetsIndex.tsx`

- [ ] **Step 1: `SessionsIndex.tsx`**

```tsx
export function SessionsIndex() {
  return (
    <div className="h-full flex items-center justify-center">
      <p className="text-sm text-neutral-500">
        Select a session from the panel on the left, or click "New engagement" on the Dashboard.
      </p>
    </div>
  );
}
```

- [ ] **Step 2: `TargetsIndex.tsx`**

```tsx
export function TargetsIndex() {
  return (
    <div className="h-full flex items-center justify-center">
      <p className="text-sm text-neutral-500">
        Select a target from the panel on the left.
      </p>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/pages/SessionsIndex.tsx desktop/renderer/src/pages/TargetsIndex.tsx
git commit -m "feat(desktop): SessionsIndex + TargetsIndex placeholder pages"
```

---

## Task 9: `SessionLayout` read-only mode + conversation seed + gated WS

The biggest single edit. Replaces the existing `SessionLayout.tsx` with a version that:

- Derives `isActive` from the session row's `state`.
- Calls `useSessionStream(id)` only when active.
- Calls `useConversation` only when *not* active, and seeds the store on data arrival.
- Hides chat input + F-key footer when not active; shows a Resume banner for stopped sessions.
- Removes the modal action-bar buttons when not active.

**Files:**
- Modify: `desktop/renderer/src/layout/SessionLayout.tsx`

- [ ] **Step 1: Rewrite `SessionLayout.tsx` in full**

Replace the file's contents:

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useStore } from "zustand";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SessionStatusBar } from "./SessionStatusBar";
import { ChatPane } from "@/panes/ChatPane";
import { ToolTimelinePane } from "@/panes/ToolTimelinePane";
import { KBPane } from "@/panes/KBPane";
import { FindingsPane } from "@/panes/FindingsPane";
import { HypothesesPane } from "@/panes/HypothesesPane";
import { Footer } from "./Footer";
import { Button } from "@/components/ui/button";
import { useNavigate } from "react-router-dom";
import { useSessions, useConversation, useResumeSession } from "@/api/queries";
import { SkillPickerModal } from "@/modals/SkillPickerModal";
import { SudoModal } from "@/modals/SudoModal";
import { StopModal } from "@/modals/StopModal";
import { DoneModal } from "@/modals/DoneModal";
import { getSessionStore } from "@/state/session-store";

export function SessionLayout() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === id);
  const target = row?.target ?? null;
  const isActive = row?.state === "active";
  const isStopped = row?.state === "stopped";

  // WebSocket is only open while the session is active.
  useSessionStream(isActive ? id ?? null : null);

  // Read-only seed: fetch and inject conversation when viewing a non-active session.
  const conversation = useConversation(!isActive ? id ?? null : null, target);
  useEffect(() => {
    if (!id || isActive || !conversation.data) return;
    getSessionStore(id).getState().seedConversation(conversation.data.conversation);
  }, [id, isActive, conversation.data]);

  // Right-rail tab + modal state (unchanged from Phase 1).
  const [rightTab, setRightTab] = useState<"hypotheses" | "findings" | "kb">("hypotheses");
  const [skillOpen, setSkillOpen] = useState(false);
  const [sudoOpen, setSudoOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);

  useEffect(() => {
    if (!isActive) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "F1") { e.preventDefault(); setSkillOpen(true); }
      if (e.key === "F4") { e.preventDefault(); setSudoOpen(true); }
      if (e.key === "F6") { e.preventDefault(); setStopOpen(true); }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "d") {
        e.preventDefault(); setDoneOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isActive]);

  const resume = useResumeSession();
  const onResume = async () => {
    if (!id) return;
    const res = await resume.mutateAsync(id);
    navigate(`/sessions/${res.id}`);
  };

  if (!id) return null;

  return (
    <div className="h-full flex flex-col bg-neutral-950 text-neutral-100">
      <SessionStatusBar sessionId={id} />

      {isStopped && (
        <div className="border-b border-emerald-900/60 bg-emerald-950/30 px-3 py-1.5 flex items-center gap-3 text-xs">
          <span className="text-emerald-400">●</span>
          <span className="text-neutral-300">
            This engagement is stopped. Resume to continue where you left off.
          </span>
          <Button
            size="sm" variant="outline"
            className="ml-auto"
            onClick={onResume}
            disabled={resume.isPending}
          >
            {resume.isPending ? "Resuming…" : "Resume engagement"}
          </Button>
        </div>
      )}

      <div className="flex-1 min-h-0">
        <PanelGroup direction="vertical">
          <Panel defaultSize={70} minSize={30}>
            <PanelGroup direction="horizontal">
              <Panel defaultSize={68} minSize={40}>
                <ChatPane sessionId={id} readOnly={!isActive} />
              </Panel>
              <PanelResizeHandle className="w-px bg-neutral-800 hover:bg-neutral-700" />
              <Panel defaultSize={32} minSize={20}>
                <div className="flex flex-col h-full">
                  <div className="flex gap-3 px-3 border-b border-neutral-800 text-[10px] uppercase tracking-wide text-neutral-500 h-7 items-center">
                    {(["hypotheses", "findings", "kb"] as const).map((t) => (
                      <button key={t}
                        className={t === rightTab ? "text-neutral-200" : "hover:text-neutral-300"}
                        onClick={() => setRightTab(t)}>{t}</button>
                    ))}
                  </div>
                  <div className="flex-1 min-h-0 overflow-auto">
                    {rightTab === "hypotheses" && <HypothesesPane sessionId={id} />}
                    {rightTab === "findings" && <FindingsPane target={target} />}
                    {rightTab === "kb" && <KBPane target={target} />}
                  </div>
                </div>
              </Panel>
            </PanelGroup>
          </Panel>
          <PanelResizeHandle className="h-px bg-neutral-800 hover:bg-neutral-700" />
          <Panel defaultSize={30} minSize={10}>
            <ToolTimelinePane sessionId={id} readOnly={!isActive} />
          </Panel>
        </PanelGroup>
      </div>

      {isActive ? (
        <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setSkillOpen(true)}>Skills (F1)</Button>
          <Button size="sm" variant="ghost" onClick={() => setSudoOpen(true)}>Sudo (F4)</Button>
          <Button size="sm" variant="ghost" onClick={() => setStopOpen(true)}>Stop (F6)</Button>
          <Button size="sm" variant="ghost" onClick={() => setDoneOpen(true)}>Mark done</Button>
          <div className="ml-auto"><Footer /></div>
        </div>
      ) : (
        <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center text-xs text-neutral-500">
          <span>view-only mode · session id: {id}</span>
          <div className="ml-auto"><Footer /></div>
        </div>
      )}

      {isActive && (
        <>
          <SkillPickerModal sessionId={id} open={skillOpen} onOpenChange={setSkillOpen} />
          <SudoModal sessionId={id} open={sudoOpen} onOpenChange={setSudoOpen} />
          <StopModal sessionId={id} open={stopOpen} onOpenChange={setStopOpen} />
          <DoneModal sessionId={id} open={doneOpen} onOpenChange={setDoneOpen} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update `ChatPane` and `ToolTimelinePane` to accept `readOnly`**

`ChatPane.tsx` already exists. Open it and add a `readOnly?: boolean` prop. When `readOnly` is true, hide the textarea + Send button at the bottom; the message-history rendering stays the same.

Replace the props type and the bottom input region. The diff is:

```tsx
// At the top of the file:
export function ChatPane({
  sessionId,
  readOnly = false,
}: { sessionId: string; readOnly?: boolean }) {
  // ... existing hooks stay the same.

  // ... existing JSX stays the same up through the messages map.

  // Replace the input region at the bottom with:
  return (
    <div className="flex flex-col h-full">
      {/* ... messages list as before ... */}
      {!readOnly && (
        <div className="border-t border-neutral-800 p-2 flex items-end gap-2">
          {/* ... existing Textarea + Send button ... */}
        </div>
      )}
    </div>
  );
}
```

(Don't duplicate the existing rendering — just gate the input block on `!readOnly`.)

`ToolTimelinePane.tsx` similarly accepts `readOnly` and, when true, renders a placeholder instead of the live timeline if the store has no `toolCalls`:

```tsx
export function ToolTimelinePane({
  sessionId,
  readOnly = false,
}: { sessionId: string; readOnly?: boolean }) {
  const store = getSessionStore(sessionId);
  const toolCalls = useStore(store, (s) => s.toolCalls);

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 h-7 flex items-center border-b border-neutral-800 text-[10px] uppercase tracking-wide text-neutral-500">
        tool timeline
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2 space-y-1 text-xs font-mono">
        {toolCalls.length === 0 ? (
          <p className="text-neutral-500 px-2">
            {readOnly
              ? "no recorded tool calls for this session (Phase 3 will replay from the session log)"
              : "no tools called yet"}
          </p>
        ) : (
          toolCalls.map((c) => (
            /* ... existing per-call rendering ... */
          ))
        )}
      </div>
    </div>
  );
}
```

(Both panes already accept `sessionId`; you're adding one optional boolean.)

- [ ] **Step 3: Compile-check**

```bash
cd desktop && npx tsc -b
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/layout/SessionLayout.tsx desktop/renderer/src/panes/ChatPane.tsx desktop/renderer/src/panes/ToolTimelinePane.tsx
git commit -m "feat(desktop): SessionLayout read-only mode + Resume banner + conversation seed

When a session's state isn't 'active', SessionLayout:
- Skips opening the WebSocket
- Fetches /api/sessions/conversation/{id} once and seeds the store
- Hides chat input, F-key footer, and modal action buttons
- Shows a Resume banner for stopped sessions (terminal states omit it)

ChatPane and ToolTimelinePane learn a readOnly prop; the panes render
their history identically and just hide live-input affordances."
```

---

## Task 10: ActivityBar + router restructure

Add the two new activity-bar icons, restructure the router so `/session/:id` lives under `Shell` (with the SessionsPanel side panel visible), and keep a redirect from the old `/session/:id` for any in-flight bookmarks.

**Files:**
- Modify: `desktop/renderer/src/layout/ActivityBar.tsx`
- Modify: `desktop/renderer/src/App.tsx`
- Modify: `desktop/renderer/src/layout/Shell.tsx` (so the side panel slot can render different content per route)

- [ ] **Step 1: Update `ActivityBar.tsx`**

Replace the icons array to add Sessions and Targets:

```tsx
import { NavLink } from "react-router-dom";
import { Home, Layers, Target, Heart, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS = [
  { to: "/", label: "Dashboard", icon: Home, match: (p: string) => p === "/" },
  { to: "/sessions", label: "Sessions", icon: Layers,
    match: (p: string) => p.startsWith("/sessions") || p.startsWith("/session/") },
  { to: "/targets", label: "Targets", icon: Target,
    match: (p: string) => p.startsWith("/targets") || p.startsWith("/target/") },
  { to: "/health", label: "Health", icon: Heart, match: (p: string) => p === "/health" },
  { to: "/settings", label: "Settings", icon: Settings, match: (p: string) => p === "/settings" },
];

export function ActivityBar() {
  return (
    <nav className="w-12 border-r border-neutral-800 bg-neutral-950 flex flex-col items-center py-2 gap-1">
      {ICONS.map(({ to, label, icon: Icon, match }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          className={({ isActive }) => {
            // react-router's isActive only checks the exact URL; we want a
            // prefix match for /sessions/:id → Sessions icon stays highlighted.
            const activeFromMatch = match(window.location.pathname);
            const lit = isActive || activeFromMatch;
            return cn(
              "w-9 h-9 flex items-center justify-center rounded transition-colors",
              lit
                ? "bg-neutral-800 text-neutral-100"
                : "text-neutral-500 hover:text-neutral-200 hover:bg-neutral-900",
            );
          }}
        >
          <Icon className="w-4 h-4" />
        </NavLink>
      ))}
    </nav>
  );
}
```

- [ ] **Step 2: Update `Shell.tsx` to render a side-panel slot per route**

Replace the file. The Shell now renders the activity bar, an optional side panel, and the main `<Outlet />`:

```tsx
import { Outlet, useLocation } from "react-router-dom";
import { StatusBar } from "./StatusBar";
import { ActivityBar } from "./ActivityBar";
import { Footer } from "./Footer";
import { SessionsPanel } from "./SessionsPanel";
import { TargetsPanel } from "./TargetsPanel";

function SidePanel() {
  const { pathname } = useLocation();
  if (pathname.startsWith("/sessions") || pathname.startsWith("/session/")) {
    return <SessionsPanel />;
  }
  if (pathname.startsWith("/targets") || pathname.startsWith("/target/")) {
    return <TargetsPanel />;
  }
  return null;
}

export function Shell() {
  return (
    <div className="h-full w-full flex flex-col bg-neutral-950 text-neutral-100">
      <StatusBar />
      <div className="flex-1 flex min-h-0">
        <ActivityBar />
        <div className="w-60 min-w-0 shrink-0"><SidePanel /></div>
        <main className="flex-1 min-w-0 overflow-hidden">
          <Outlet />
        </main>
      </div>
      <Footer />
    </div>
  );
}
```

Note: when `SidePanel` returns `null` (Dashboard, Health, Settings, New), the 240px slot stays as empty whitespace — visually it looks like the activity bar widens. If that bothers you, wrap the `<div className="w-60 ...">` in a `{pathname.startsWith("/sessions") || pathname.startsWith("/session/") || pathname.startsWith("/targets") || pathname.startsWith("/target/") && (...)}` ternary. For simplicity, leave it as-is and let Phase 4 polish handle the collapse animation.

- [ ] **Step 3: Update `App.tsx`**

Replace the routes block. The session route moves under Shell, and `/session/:id` redirects to `/sessions/:id`:

```tsx
import { useEffect } from "react";
import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { Shell } from "@/layout/Shell";
import { Dashboard } from "@/pages/Dashboard";
import { Health } from "@/pages/Health";
import { Settings } from "@/pages/Settings";
import { CrashScreen } from "@/pages/CrashScreen";
import { NewEngagement } from "@/pages/NewEngagement";
import { SessionLayout } from "@/layout/SessionLayout";
import { SessionsIndex } from "@/pages/SessionsIndex";
import { TargetsIndex } from "@/pages/TargetsIndex";
import { TargetOverview } from "@/pages/TargetOverview";
import { useConnection } from "@/state/connection";

function LegacySessionRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/sessions/${id}`} replace />;
}

export default function App() {
  const status = useConnection((s) => s.status);
  const setInfo = useConnection((s) => s.setInfo);

  useEffect(() => {
    window.desktop.getConnectionInfo().then(setInfo);
    return window.desktop.onConnectionStatusChanged(setInfo);
  }, [setInfo]);

  if (status === "exited") return <CrashScreen />;
  if (status === "starting") {
    return (
      <div className="h-full flex items-center justify-center text-sm text-neutral-500">
        starting backend…
      </div>
    );
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="/new" element={<NewEngagement />} />
        <Route path="/sessions" element={<SessionsIndex />} />
        <Route path="/sessions/:id" element={<SessionLayout />} />
        <Route path="/targets" element={<TargetsIndex />} />
        <Route path="/target/:name" element={<TargetOverview />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      {/* Legacy redirect — Phase 1 used /session/:id directly. */}
      <Route path="/session/:id" element={<LegacySessionRedirect />} />
    </Routes>
  );
}
```

- [ ] **Step 4: Compile + smoke + commit**

```bash
cd desktop && npx tsc -b && npx tsc -p tsconfig.electron.json
```

Expected: clean both passes.

```bash
git add desktop/renderer/src/layout/ActivityBar.tsx desktop/renderer/src/layout/Shell.tsx desktop/renderer/src/App.tsx
git commit -m "feat(desktop): /sessions and /targets routes + activity-bar icons + side panels"
```

---

## Task 11: Dashboard cleanup

Drop the "Recent sessions" card (the SessionsPanel now owns that surface) and keep the profile grid + CTA.

**Files:**
- Modify: `desktop/renderer/src/pages/Dashboard.tsx`

- [ ] **Step 1: Trim the page**

Replace `desktop/renderer/src/pages/Dashboard.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useProfiles } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function Dashboard() {
  const profiles = useProfiles();

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center mb-6 gap-4">
        <h2 className="text-base font-medium">Dashboard</h2>
        <Link to="/new" className="ml-auto">
          <Button>New engagement</Button>
        </Link>
      </div>

      <h3 className="text-sm font-medium mb-3">
        Profiles ({profiles.data?.profiles.length ?? 0})
      </h3>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {profiles.data?.profiles.map((p) => (
          <Card key={p.key}>
            <CardHeader>
              <CardTitle>{p.name}</CardTitle>
              <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">
                {p.key}
              </div>
            </CardHeader>
            <CardContent className="text-xs">
              <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/pages/Dashboard.tsx
git commit -m "feat(desktop): Dashboard slims to profile grid + CTA (sessions moved to panel)"
```

---

## Task 12: Playwright e2e

Cover the three new flows: navigate to `/sessions`, click a session, see SessionLayout; navigate to `/targets`, click a target, see TargetOverview; open a stopped session and confirm the read-only chrome.

The existing `engagement.spec.ts` covers the new-engagement happy path. This spec adds Phase 2 coverage.

**Files:**
- Create: `desktop/tests/e2e/phase2.spec.ts`

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build
```

Expected: clean build.

- [ ] **Step 2: Write the spec**

Create `desktop/tests/e2e/phase2.spec.ts`:

```ts
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

test("sessions panel: navigate /sessions and see the panel", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    // Click the Sessions activity-bar icon (title="Sessions").
    await w.click('[title="Sessions"]');

    // The SessionsPanel renders its header.
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 5_000 });
    // Filter tabs render.
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
    // The placeholder shows when no session is selected.
    await expect(
      w.locator("text=Select a session from the panel on the left"),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel: navigate /targets and see the panel", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    await w.click('[title="Targets"]');

    await expect(w.locator("text=Targets").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
    await expect(
      w.locator("text=Select a target from the panel on the left"),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("legacy /session/:id redirects to /sessions/:id", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });

    // Programmatically navigate (no real session id needed — the redirect
    // is route-level, not row-driven).
    await w.evaluate(() => {
      window.history.pushState({}, "", "/session/legacy-id");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    // Expect the URL to be rewritten to /sessions/legacy-id.
    await w.waitForFunction(
      () => window.location.pathname === "/sessions/legacy-id",
      { timeout: 5_000 },
    );
  } finally {
    await app.close();
  }
});
```

- [ ] **Step 3: Run all e2e tests**

```bash
cd desktop && npx playwright test 2>&1 | tail -15
```

Expected: 5 passed (2 existing + 3 new).

If a test fails with "service died before handshake", the supervisor probably can't find a Python with `reverser` importable. The `PATH` injection above mirrors the existing smoke.spec.ts pattern (the `bin/python` shim points at `python3` when `python` isn't on PATH).

- [ ] **Step 4: Commit**

```bash
git add desktop/tests/e2e/phase2.spec.ts
git commit -m "test(desktop): e2e — sessions panel, targets panel, legacy /session/:id redirect"
```

---

## Verification

After all 12 tasks:

```bash
# Frontend type-check + build
cd desktop && npx tsc -b && npx tsc -p tsconfig.electron.json && npm run build

# Frontend e2e
cd desktop && npx playwright test
# Expected: 5 passed.

# Manual smoke (in devenv shell, with the service running via reverser g):
# 1. Dashboard loads with profile grid + "New engagement" CTA.
# 2. Click 📋 (Layers icon) → SessionsPanel renders with filter tabs.
#    If you have prior sessions, they appear; otherwise placeholder.
# 3. Click 🎯 (Target icon) → TargetsPanel renders.
# 4. Click a target → /target/:name shows the summary card + sessions + KB tabs.
# 5. Click a stopped session → SessionLayout opens in read-only mode:
#    - status pill gray, Resume banner visible
#    - no chat input, no F-key footer
#    - chat history seeded from the snapshot
# 6. Click "Resume engagement" → session goes back to active, banner disappears,
#    chat input reappears, WebSocket connects.
```

## Risks observed

- **Side-panel slot always reserves 240px.** Even on Dashboard / Health / Settings (no panel), the layout has an empty 240px column. Acceptable for Phase 2; Phase 4 polish can collapse this.
- **TargetsPanel computes summaries client-side.** For 5–10 targets this is cheap. Past ~50 targets, switching to the per-target `/summary` endpoint for the row stats (or a future batch endpoint) is worth doing. Not a blocker now.
- **Conversation seed replaces the messages array.** If a user is mid-typing in another session and navigates to a stopped session, the textarea state in the *active* session's store is unaffected (per-session stores), but the messages slot of the just-opened store gets the snapshot. This is correct; documented for future reviewers.

## What this plan does NOT cover

- Full tool-call timeline replay from the session log — Phase 3.
- BloodHound graph, evidence gallery, scope.toml editor — Phase 3.
- `/new?target=…` query-param prefill — Phase 4 polish.
- Per-target cost limits + batch summary endpoint — Phase 4.