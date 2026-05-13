# Live Engagement Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the renderer side of a live engagement: new-engagement wizard, IDE-style session view with chat / tool-timeline / KB / findings panes, F-key modals (skills, sudo, stop/done), live `AgentEvent` stream over WebSocket, status bar with budget/turns, auth-gate confirmation, and a minimal "resume latest stopped session for this target" button. Frontend half of spec Phase 1.

**Architecture:** A `useSessionStream(sessionId)` hook opens one WebSocket per session and dispatches frames into a per-session Zustand store. Panes subscribe via typed selectors. REST mutations go through TanStack Query against the Plan-3a endpoints. IDE-style multi-pane layout uses `react-resizable-panels`.

**Depends on:** [`Plan 1`](2026-05-13-plan-1-gui-service-foundation.md), [`Plan 2`](2026-05-13-plan-2-electron-shell.md), [`Plan 3a`](2026-05-13-plan-3a-live-engagement-backend.md).

**Reference spec:** [`docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md`](../specs/2026-05-13-electron-desktop-ui-design.md) — section 4 (renderer architecture).

---

## File map

```
desktop/package.json                                              modify  (add react-resizable-panels)
desktop/renderer/src/api/client.ts                                modify  (add Session types + mutations)
desktop/renderer/src/api/queries.ts                               modify  (add session hooks)
desktop/renderer/src/state/session-stream.ts                      create
desktop/renderer/src/state/session-store.ts                       create
desktop/renderer/src/hooks/useSessionStream.ts                    create
desktop/renderer/src/components/ui/dialog.tsx                     create  (shadcn primitive)
desktop/renderer/src/components/ui/input.tsx                      create
desktop/renderer/src/components/ui/select.tsx                     create
desktop/renderer/src/components/ui/textarea.tsx                   create
desktop/renderer/src/pages/NewEngagement.tsx                      create
desktop/renderer/src/pages/Session.tsx                            create
desktop/renderer/src/layout/SessionLayout.tsx                     create  (IDE multi-pane)
desktop/renderer/src/panes/ChatPane.tsx                           create
desktop/renderer/src/panes/ToolTimelinePane.tsx                   create
desktop/renderer/src/panes/KBPane.tsx                             create
desktop/renderer/src/panes/FindingsPane.tsx                       create
desktop/renderer/src/panes/HypothesesPane.tsx                     create  (read-only list; tree comes in Phase 3)
desktop/renderer/src/modals/AuthGateModal.tsx                     create
desktop/renderer/src/modals/SudoModal.tsx                         create
desktop/renderer/src/modals/SkillPickerModal.tsx                  create
desktop/renderer/src/modals/StopModal.tsx                         create
desktop/renderer/src/modals/DoneModal.tsx                         create
desktop/renderer/src/layout/SessionStatusBar.tsx                  create  (live budget/turns)
desktop/renderer/src/App.tsx                                      modify  (add /new, /session/:id routes)
desktop/renderer/src/layout/Shell.tsx                             modify  (sessions list in activity bar)
desktop/tests/e2e/engagement.spec.ts                              create
```

---

## Task 1: Add `react-resizable-panels` dep

**Files:**
- Modify: `desktop/package.json`

- [ ] **Step 1: Add the dep**

Edit `desktop/package.json` and add to `dependencies`:

```json
"react-resizable-panels": "^2.1.4"
```

Run: `cd desktop && npm install`
Expected: completes without errors.

- [ ] **Step 2: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "build(desktop): add react-resizable-panels"
```

---

## Task 2: Session-event types + Zustand store

The store holds, per session, all the data the panes need: messages, tool calls, KB diffs, hypotheses, findings, status, and a budget snapshot. Frames flowing in from the WebSocket get dispatched into the right slot.

**Files:**
- Create: `desktop/renderer/src/state/session-store.ts`

- [ ] **Step 1: Write `session-store.ts`**

Create `desktop/renderer/src/state/session-store.ts`:

```ts
import { create } from "zustand";

/**
 * WS frame shapes — keep aligned with src/reverser/gui_service/session_adapter.py
 * and docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md §3.3.
 */
export type WSFrame =
  | { type: "text"; role: "assistant"; delta: string }
  | { type: "thinking"; delta: string; redacted: boolean }
  | { type: "tool_call"; name: string; args: string }
  | { type: "tool_result"; ok: boolean; preview: string }
  | { type: "kb_update"; kind: string; row: unknown }
  | { type: "hypothesis"; action: string; row: unknown }
  | { type: "finding"; row: unknown }
  | { type: "dispatch"; specialist: string; child_session_id: string; phase: string }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

export type ChatMessage =
  | { role: "user"; text: string; turn?: number }
  | { role: "assistant"; text: string; turn?: number };

export type ToolCall = {
  id: string; // synthesized — tool_call frame has no id today
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
  startedAt: number;
};

export type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  messages: ChatMessage[];
  /** Text currently being streamed in by the assistant for the current turn. */
  pendingAssistantText: string;
  toolCalls: ToolCall[];
  hypotheses: unknown[];
  findings: unknown[];
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  /** Bounded log buffer for the bottom panel. */
  log: { level: string; msg: string; ts: number }[];
};

type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
};

const _initialState = (): SessionState => ({
  status: "idle",
  messages: [],
  pendingAssistantText: "",
  toolCalls: [],
  hypotheses: [],
  findings: [],
  budget: null,
  connBreakerTripped: false,
  log: [],
});

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),
    appendUserMessage: (text) =>
      set((s) => ({ messages: [...s.messages, { role: "user", text }] })),
    reset: () => set(_initialState()),
    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text":
            return { pendingAssistantText: s.pendingAssistantText + frame.delta };
          case "tool_call":
            return {
              toolCalls: [
                ...s.toolCalls,
                { id: `${frame.name}-${Date.now()}-${s.toolCalls.length}`,
                  name: frame.name, args: frame.args, startedAt: Date.now() },
              ],
            };
          case "tool_result": {
            const tc = [...s.toolCalls];
            for (let i = tc.length - 1; i >= 0; i--) {
              if (!tc[i].result) {
                tc[i] = { ...tc[i], result: { ok: frame.ok, preview: frame.preview } };
                break;
              }
            }
            return { toolCalls: tc };
          }
          case "finding":
            return { findings: [...s.findings, frame.row] };
          case "hypothesis":
            return { hypotheses: [...s.hypotheses, frame.row] };
          case "budget":
            return { budget: { spent: frame.spent, remaining: frame.remaining, turn: frame.turn } };
          case "conn_breaker":
            return { connBreakerTripped: frame.tripped };
          case "log":
            return { log: [...s.log.slice(-499), { level: frame.level, msg: frame.msg, ts: Date.now() }] };
          case "status": {
            const next: Partial<SessionState> = { status: frame.phase as SessionState["status"] };
            // When a turn closes, flush pending text into a final assistant message.
            if ((frame.phase === "awaiting_input" || frame.phase === "stopped" || frame.phase === "completed")
                && s.pendingAssistantText) {
              next.messages = [
                ...s.messages,
                { role: "assistant", text: s.pendingAssistantText, turn: frame.turns },
              ];
              next.pendingAssistantText = "";
            }
            return next;
          }
          case "kb_update":
          case "dispatch":
          case "thinking":
            // Phase 1: KB pane re-fetches from /api/targets/{t}/kb on
            // demand; we don't try to apply diffs in-memory.
            // Dispatch + thinking frames are not surfaced in Phase 1 UI.
            return {};
        }
      }),
  }));

/**
 * A registry of stores keyed by session_id. Created lazily; cleared on
 * navigate-away via `clearSessionStore`.
 */
const _stores = new Map<string, ReturnType<typeof makeSessionStore>>();

export function getSessionStore(sessionId: string) {
  let s = _stores.get(sessionId);
  if (!s) {
    s = makeSessionStore();
    _stores.set(sessionId, s);
  }
  return s;
}

export function clearSessionStore(sessionId: string) {
  _stores.delete(sessionId);
}
```

- [ ] **Step 2: Compile-check**

Run: `cd desktop && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts
git commit -m "feat(desktop): per-session Zustand store + WSFrame ingest reducer"
```

---

## Task 3: `useSessionStream` hook — one WebSocket → store ingest

**Files:**
- Create: `desktop/renderer/src/hooks/useSessionStream.ts`

- [ ] **Step 1: Write the hook**

Create `desktop/renderer/src/hooks/useSessionStream.ts`:

```ts
import { useEffect } from "react";
import { useConnection } from "@/state/connection";
import { getSessionStore, type WSFrame } from "@/state/session-store";

/**
 * Open exactly one WebSocket per session_id. The connection lives for as
 * long as the hook is mounted. Reconnect on disconnect is intentionally
 * NOT implemented in Phase 1 — the session ends with the UI per spec
 * lifecycle A. (Phase 4 may add reconnect for crash-recovery UX.)
 */
export function useSessionStream(sessionId: string | null) {
  const port = useConnection((s) => s.port);
  const token = useConnection((s) => s.token);
  const ready = useConnection((s) => s.status === "ready");

  useEffect(() => {
    if (!sessionId || !ready || !port || !token) return;
    const url = `ws://127.0.0.1:${port}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    const store = getSessionStore(sessionId);
    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data) as WSFrame;
        store.getState().ingest(frame);
      } catch {
        // Server only sends JSON; if we got something else, log to the store.
        store.getState().ingest({ type: "log", level: "warn", msg: "non-JSON WS frame" });
      }
    };
    ws.onclose = () => {
      store.getState().ingest({ type: "log", level: "info", msg: "websocket closed" });
    };
    return () => {
      try { ws.close(); } catch { /* ignore */ }
    };
  }, [sessionId, ready, port, token]);
}
```

- [ ] **Step 2: Compile-check**

Run: `cd desktop && npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/hooks/useSessionStream.ts
git commit -m "feat(desktop): useSessionStream — WebSocket per session, dispatches to store"
```

---

## Task 4: API client — Session types + mutations

**Files:**
- Modify: `desktop/renderer/src/api/client.ts`
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Add types + mutations to `client.ts`**

Append to `desktop/renderer/src/api/client.ts`:

```ts
// ---- Sessions ----

export type SessionRow = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  turns: number;
  total_cost: number;
  stopped_at: string | null;
  budget?: number;
  max_turns?: number;
};

export type SessionsResponse = { sessions: SessionRow[] };

export type CreateSessionRequest = {
  target: string;
  profile: string;
  backend: string;
  model: string | null;
  api_base: string | null;
  budget: number;
  max_turns: number;
};

export type CreateSessionResponse = {
  id: string;
  state: "active";
  target: string;
  profile_key: string;
  turns: number;
  total_cost: number;
  budget: number;
  max_turns: number;
};

// ---- Targets / KB ----

export type TargetRow = { name: string; has_kb: boolean; has_scope: boolean };
export type TargetsResponse = { targets: TargetRow[] };

export type KBResponse = {
  hosts: unknown[];
  services: unknown[];
  credentials: unknown[];
  findings: unknown[];
  hypotheses: unknown[];
  artifacts: unknown[];
  notes: unknown[];
};
```

- [ ] **Step 2: Add hooks to `queries.ts`**

Replace `desktop/renderer/src/api/queries.ts` with:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type HealthResponse,
  type ProfilesResponse,
  type BackendsResponse,
  type SessionsResponse,
  type CreateSessionRequest,
  type CreateSessionResponse,
  type TargetsResponse,
  type KBResponse,
} from "./client";
import { useConnection } from "@/state/connection";

function useReady() {
  return useConnection((s) => s.status === "ready");
}

export function useHealth() {
  const ready = useReady();
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/api/health"),
    enabled: ready,
    refetchInterval: 10_000,
  });
}

export function useProfiles() {
  const ready = useReady();
  return useQuery({
    queryKey: ["profiles"],
    queryFn: () => api.get<ProfilesResponse>("/api/profiles"),
    enabled: ready,
    staleTime: 60_000,
  });
}

export function useBackends() {
  const ready = useReady();
  return useQuery({
    queryKey: ["backends"],
    queryFn: () => api.get<BackendsResponse>("/api/backends"),
    enabled: ready,
    staleTime: 60_000,
  });
}

export function useSessions() {
  const ready = useReady();
  return useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.get<SessionsResponse>("/api/sessions"),
    enabled: ready,
    refetchInterval: 5_000,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) =>
      api.post<CreateSessionResponse>("/api/sessions", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useSendMessage(sessionId: string) {
  return useMutation({
    mutationFn: (text: string) =>
      api.post<void>(`/api/sessions/${sessionId}/messages`, { text }),
  });
}

export function useTriggerSkill(sessionId: string) {
  return useMutation({
    mutationFn: (skillKey: string) =>
      api.post<void>(`/api/sessions/${sessionId}/skills/${skillKey}`),
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => api.post<void>(`/api/sessions/${sessionId}/stop`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useMarkDone() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => api.post<void>(`/api/sessions/${sessionId}/done`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useResumeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) =>
      api.post<CreateSessionResponse>(`/api/sessions/${sessionId}/resume`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useSetSudo(sessionId: string) {
  return useMutation({
    mutationFn: (password: string) =>
      api.post<void>(`/api/sessions/${sessionId}/sudo`, { password }),
  });
}

export function useTargets() {
  const ready = useReady();
  return useQuery({
    queryKey: ["targets"],
    queryFn: () => api.get<TargetsResponse>("/api/targets"),
    enabled: ready,
    staleTime: 30_000,
  });
}

export function useTargetKB(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["kb", target],
    queryFn: () => api.get<KBResponse>(`/api/targets/${encodeURIComponent(target!)}/kb`),
    enabled: ready && !!target,
    refetchInterval: 8_000,
  });
}
```

- [ ] **Step 3: Compile-check + commit**

```bash
cd desktop && npx tsc -b
```

Expected: no errors.

```bash
git add desktop/renderer/src/api/
git commit -m "feat(desktop): session + targets API types + TanStack Query hooks"
```

---

## Task 5: Bare shadcn-style primitives (Dialog, Input, Select, Textarea)

These are minimal stand-ins for the shadcn equivalents — enough for Phase 1. We can swap to the full shadcn copies later if we want the polish.

**Files:**
- Create: `desktop/renderer/src/components/ui/dialog.tsx`
- Create: `desktop/renderer/src/components/ui/input.tsx`
- Create: `desktop/renderer/src/components/ui/select.tsx`
- Create: `desktop/renderer/src/components/ui/textarea.tsx`

- [ ] **Step 1: `dialog.tsx`**

Create `desktop/renderer/src/components/ui/dialog.tsx`:

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export function Dialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onOpenChange(false); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [open, onOpenChange]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
         onClick={() => onOpenChange(false)}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-neutral-900 border border-neutral-700 rounded shadow-xl min-w-[400px] max-w-[640px] p-5"
      >
        {children}
      </div>
    </div>
  );
}

export function DialogHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("mb-3", className)}>{children}</div>;
}

export function DialogTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h3 className={cn("text-sm font-medium text-neutral-100", className)}>{children}</h3>;
}

export function DialogDescription({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-neutral-400 mt-1">{children}</p>;
}

export function DialogFooter({ children }: { children: React.ReactNode }) {
  return <div className="flex justify-end gap-2 mt-5">{children}</div>;
}
```

- [ ] **Step 2: `input.tsx`**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded border border-neutral-700 bg-neutral-950 px-2 text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-500",
        className
      )}
      {...p}
    />
  )
);
Input.displayName = "Input";
```

- [ ] **Step 3: `select.tsx`** (native `<select>`, styled)

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...p }, ref) => (
    <select
      ref={ref}
      className={cn(
        "h-9 w-full rounded border border-neutral-700 bg-neutral-950 px-2 text-sm text-neutral-100 focus:outline-none focus:border-neutral-500",
        className
      )}
      {...p}
    />
  )
);
Select.displayName = "Select";
```

- [ ] **Step 4: `textarea.tsx`**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-500 resize-none",
        className
      )}
      {...p}
    />
  )
);
Textarea.displayName = "Textarea";
```

- [ ] **Step 5: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/components/ui/
git commit -m "feat(desktop): UI primitives — Dialog, Input, Select, Textarea"
```

---

## Task 6: Auth-gate confirmation modal

The Plan-3a `SessionManager` rejects network profiles unless `REVERSER_PENTEST_AUTHORIZED=1` or `.reverser-authorized` exists. We show a one-time modal that, on accept, writes the marker file via a new IPC channel to the Electron main.

**Files:**
- Modify: `desktop/electron/ipc.ts`
- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/preload.ts`
- Create: `desktop/renderer/src/modals/AuthGateModal.tsx`

- [ ] **Step 1: Add IPC channel**

Edit `desktop/electron/ipc.ts`:

```ts
export const IPC = {
  GET_CONNECTION_INFO: "connection:get-info",
  OPEN_EXTERNAL: "shell:open-external",
  OPEN_FILE_DIALOG: "dialog:open-file",
  CONNECTION_STATUS_CHANGED: "connection:status-changed",
  PYTHON_LOG_LINE: "python:log-line",
  WRITE_AUTH_MARKER: "authz:write-marker",
} as const;
```

- [ ] **Step 2: Implement the handler in `main.ts`**

Append to `desktop/electron/main.ts`:

```ts
import fs from "fs/promises";

ipcMain.handle(IPC.WRITE_AUTH_MARKER, async () => {
  const root = defaultProjectRoot();
  const marker = path.join(root, ".reverser-authorized");
  await fs.writeFile(marker, "", { flag: "w" });
  return marker;
});
```

(Place the `import fs from "fs/promises"` near the other imports, and the handler near the other `ipcMain.handle` calls.)

- [ ] **Step 3: Expose on the preload**

Edit `desktop/electron/preload.ts`. Add to the `contextBridge.exposeInMainWorld("desktop", {...})` block:

```ts
writeAuthMarker: (): Promise<string> => ipcRenderer.invoke(IPC.WRITE_AUTH_MARKER),
```

And to the `declare global { interface Window { desktop: { ... } } }`:

```ts
writeAuthMarker: () => Promise<string>;
```

- [ ] **Step 4: Modal**

Create `desktop/renderer/src/modals/AuthGateModal.tsx`:

```tsx
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

const ACK = "I have written authorization to test this target.";

export function AuthGateModal({
  open, onOpenChange, onAcknowledged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAcknowledged: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Pentest authorization required</DialogTitle>
        <DialogDescription>
          Network-touching profiles (pentest, webpentest, ad, manager, exploit, …)
          require explicit authorization. Confirming below writes a
          {" "}<code className="font-mono">.reverser-authorized</code> marker file
          in this project root.
        </DialogDescription>
      </DialogHeader>

      <p className="text-xs text-neutral-300 my-3 leading-relaxed">
        By continuing you affirm: <em>{ACK}</em>
      </p>

      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="destructive"
          onClick={async () => {
            await window.desktop.writeAuthMarker();
            onAcknowledged();
            onOpenChange(false);
          }}
        >
          I confirm
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 5: Compile-check + commit**

```bash
cd desktop && npx tsc -b && npx tsc -p tsconfig.electron.json
git add desktop/electron/ desktop/renderer/src/modals/AuthGateModal.tsx
git commit -m "feat(desktop): AuthGateModal — write .reverser-authorized on confirm"
```

---

## Task 7: New-engagement wizard

**Files:**
- Create: `desktop/renderer/src/pages/NewEngagement.tsx`

- [ ] **Step 1: Write the page**

Create `desktop/renderer/src/pages/NewEngagement.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBackends, useCreateSession, useProfiles, useResumeSession, useSessions } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { AuthGateModal } from "@/modals/AuthGateModal";
import { ApiError } from "@/api/client";

const NETWORK_PROFILES = new Set([
  "pentest", "webpentest", "webapi", "webrecon", "ad", "manager", "exploit",
]);

export function NewEngagement() {
  const navigate = useNavigate();
  const profiles = useProfiles();
  const backends = useBackends();
  const sessions = useSessions();
  const create = useCreateSession();
  const resume = useResumeSession();

  const [target, setTarget] = useState("");
  const [profile, setProfile] = useState("general");
  const [backend, setBackend] = useState("claude");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [budget, setBudget] = useState(5);
  const [maxTurns, setMaxTurns] = useState(50);
  const [authGateOpen, setAuthGateOpen] = useState(false);
  const [pendingSubmit, setPendingSubmit] = useState(false);

  const latestStoppedForTarget = sessions.data?.sessions
    .filter((s) => s.target === target && s.state === "stopped")
    .sort((a, b) => (b.stopped_at ?? "").localeCompare(a.stopped_at ?? ""))[0];

  async function submit() {
    setPendingSubmit(false);
    try {
      const res = await create.mutateAsync({
        target,
        profile,
        backend,
        model: model || null,
        api_base: apiBase || null,
        budget,
        max_turns: maxTurns,
      });
      navigate(`/session/${res.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setPendingSubmit(true);
        setAuthGateOpen(true);
      } else {
        // Surface the message in a basic alert; better UX comes later.
        alert((e as Error).message);
      }
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <h2 className="text-base font-medium mb-4">New engagement</h2>

      <Card>
        <CardHeader>
          <CardTitle>Target</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="block text-xs text-neutral-500 mb-1">Path or URL</label>
            <Input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="/path/to/binary or https://example.com or 10.10.10.5"
            />
            {latestStoppedForTarget && (
              <button
                className="mt-1 text-xs text-blue-400 hover:underline"
                onClick={async () => {
                  const res = await resume.mutateAsync(latestStoppedForTarget.id);
                  navigate(`/session/${res.id}`);
                }}
              >
                resume {latestStoppedForTarget.id} (stopped {latestStoppedForTarget.stopped_at})
              </button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Profile</label>
              <Select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {profiles.data?.profiles.map((p) => (
                  <option key={p.key} value={p.key}>{p.name} · {p.key}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Backend</label>
              <Select value={backend} onChange={(e) => setBackend(e.target.value)}>
                {backends.data?.backends.map((b) => (
                  <option key={b.key} value={b.key}>{b.name}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Model (optional for Claude)</label>
              <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. qwen3.5:35b" />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">API base URL (optional)</label>
              <Input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://localhost:11434/v1" />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Budget (USD)</label>
              <Input type="number" step="0.1" value={budget} onChange={(e) => setBudget(parseFloat(e.target.value))} />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 mb-1">Max turns</label>
              <Input type="number" value={maxTurns} onChange={(e) => setMaxTurns(parseInt(e.target.value, 10))} />
            </div>
          </div>

          <Button
            disabled={!target || create.isPending}
            onClick={submit}
          >
            {create.isPending ? "Starting…" : "Start engagement"}
          </Button>

          {NETWORK_PROFILES.has(profile) && (
            <p className="text-[11px] text-amber-400 mt-2">
              This profile touches the network. You must have written authorization to test the target.
            </p>
          )}
        </CardContent>
      </Card>

      <AuthGateModal
        open={authGateOpen}
        onOpenChange={setAuthGateOpen}
        onAcknowledged={() => { if (pendingSubmit) submit(); }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/pages/NewEngagement.tsx
git commit -m "feat(desktop): new-engagement wizard + auth-gate flow + resume button"
```

---

## Task 8: SessionLayout — IDE-style multi-pane shell for `/session/:id`

**Files:**
- Create: `desktop/renderer/src/layout/SessionLayout.tsx`
- Create: `desktop/renderer/src/layout/SessionStatusBar.tsx`

- [ ] **Step 1: `SessionStatusBar.tsx`**

Create `desktop/renderer/src/layout/SessionStatusBar.tsx`:

```tsx
import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";
import { useSessions } from "@/api/queries";

export function SessionStatusBar({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const status = useStore(store, (s) => s.status);
  const budget = useStore(store, (s) => s.budget);
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);

  return (
    <header className="h-9 border-b border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-xs gap-4 font-mono">
      <span className={
        status === "running" ? "text-amber-400" :
        status === "awaiting_input" ? "text-green-400" :
        status === "stopped" ? "text-neutral-500" :
        status === "completed" ? "text-blue-400" : "text-neutral-300"
      }>● {status}</span>
      <span className="text-neutral-300">{row?.target ?? "—"}</span>
      <span>profile: <span className="text-neutral-300">{row?.profile ?? "—"}</span></span>
      <span className="ml-auto text-neutral-400">
        {budget
          ? <>${budget.spent.toFixed(2)} / ${(budget.spent + budget.remaining).toFixed(2)} · turn {budget.turn}/{row?.max_turns ?? "?"}</>
          : <>budget —</>}
      </span>
    </header>
  );
}
```

- [ ] **Step 2: `SessionLayout.tsx`**

Create `desktop/renderer/src/layout/SessionLayout.tsx`:

```tsx
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SessionStatusBar } from "./SessionStatusBar";
import { ChatPane } from "@/panes/ChatPane";
import { ToolTimelinePane } from "@/panes/ToolTimelinePane";
import { KBPane } from "@/panes/KBPane";
import { FindingsPane } from "@/panes/FindingsPane";
import { HypothesesPane } from "@/panes/HypothesesPane";
import { Footer } from "./Footer";
import { Button } from "@/components/ui/button";
import { useSessions, useStopSession, useMarkDone } from "@/api/queries";

export function SessionLayout() {
  const { id } = useParams<{ id: string }>();
  useSessionStream(id ?? null);
  const [rightTab, setRightTab] = useState<"hypotheses" | "findings" | "kb">("hypotheses");
  const stop = useStopSession();
  const done = useMarkDone();
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === id);
  const target = row?.target ?? null;

  if (!id) return null;

  return (
    <div className="h-full flex flex-col bg-neutral-950 text-neutral-100">
      <SessionStatusBar sessionId={id} />
      <div className="flex-1 min-h-0">
        <PanelGroup direction="vertical">
          <Panel defaultSize={70} minSize={30}>
            <PanelGroup direction="horizontal">
              <Panel defaultSize={68} minSize={40}>
                <ChatPane sessionId={id} />
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
            <ToolTimelinePane sessionId={id} />
          </Panel>
        </PanelGroup>
      </div>
      <div className="h-9 border-t border-neutral-800 bg-neutral-950/80 px-3 flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => stop.mutate(id)}>Stop (F6)</Button>
        <Button size="sm" variant="ghost" onClick={() => done.mutate(id)}>Mark done</Button>
        <div className="ml-auto"><Footer /></div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Compile-check + commit**

```bash
cd desktop && npx tsc -b
```

(Will error on missing pane imports — that's fine; the next tasks create them. Skip the commit until panes exist.)

---

## Task 9: ChatPane

**Files:**
- Create: `desktop/renderer/src/panes/ChatPane.tsx`

- [ ] **Step 1: Write the pane**

Create `desktop/renderer/src/panes/ChatPane.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";

export function ChatPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const messages = useStore(store, (s) => s.messages);
  const pending = useStore(store, (s) => s.pendingAssistantText);
  const status = useStore(store, (s) => s.status);
  const send = useSendMessage(sessionId);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pending]);

  const submit = async () => {
    if (!input.trim() || send.isPending) return;
    store.getState().appendUserMessage(input);
    const text = input;
    setInput("");
    await send.mutateAsync(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-auto p-4 space-y-3">
        {messages.length === 0 && !pending && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={
            m.role === "user"
              ? "max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap"
              : "max-w-[85%] text-neutral-200 text-sm whitespace-pre-wrap"
          }>
            {m.text}
          </div>
        ))}
        {pending && (
          <div className="max-w-[85%] text-neutral-300 text-sm whitespace-pre-wrap italic">
            {pending}
          </div>
        )}
      </div>
      <div className="border-t border-neutral-800 p-2 flex items-end gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder="type a message — ⌘/Ctrl+Enter to send"
        />
        <Button onClick={submit} disabled={!input.trim() || send.isPending || status === "running"}>
          Send
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit (deferred until all panes exist)**

---

## Task 10: ToolTimelinePane

**Files:**
- Create: `desktop/renderer/src/panes/ToolTimelinePane.tsx`

- [ ] **Step 1: Write the pane**

Create `desktop/renderer/src/panes/ToolTimelinePane.tsx`:

```tsx
import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";
import { Check, Loader2, X } from "lucide-react";

export function ToolTimelinePane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const toolCalls = useStore(store, (s) => s.toolCalls);

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 h-7 flex items-center border-b border-neutral-800 text-[10px] uppercase tracking-wide text-neutral-500">
        tool timeline
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2 space-y-1 text-xs font-mono">
        {toolCalls.length === 0 && (
          <p className="text-neutral-500 px-2">no tools called yet</p>
        )}
        {toolCalls.map((c) => (
          <div key={c.id} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              {!c.result ? (
                <Loader2 className="w-3 h-3 animate-spin text-amber-400" />
              ) : c.result.ok ? (
                <Check className="w-3 h-3 text-green-400" />
              ) : (
                <X className="w-3 h-3 text-red-400" />
              )}
              <span className="text-neutral-200">{c.name}</span>
              <span className="text-neutral-500 truncate">{c.args.slice(0, 80)}</span>
            </div>
            {c.result && (
              <pre className="mt-1 text-neutral-400 text-[10px] whitespace-pre-wrap break-all line-clamp-4">
                {c.result.preview}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## Task 11: KBPane, FindingsPane, HypothesesPane

**Files:**
- Create: `desktop/renderer/src/panes/KBPane.tsx`
- Create: `desktop/renderer/src/panes/FindingsPane.tsx`
- Create: `desktop/renderer/src/panes/HypothesesPane.tsx`

- [ ] **Step 1: `KBPane.tsx`**

```tsx
import { useTargetKB } from "@/api/queries";

export function KBPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;
  const sections = [
    ["hosts", data?.hosts],
    ["services", data?.services],
    ["credentials", data?.credentials],
    ["artifacts", data?.artifacts],
    ["notes", data?.notes],
  ] as const;
  return (
    <div className="p-2 text-xs font-mono space-y-3">
      {sections.map(([label, rows]) => (
        <div key={label}>
          <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1">
            {label} · {rows?.length ?? 0}
          </div>
          <div className="space-y-1">
            {(rows ?? []).slice(0, 50).map((r, i) => (
              <pre key={i} className="text-neutral-400 truncate" title={JSON.stringify(r)}>
                {JSON.stringify(r)}
              </pre>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: `FindingsPane.tsx`**

```tsx
import { useTargetKB } from "@/api/queries";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-blue-400",
  info: "text-neutral-500",
};

export function FindingsPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;
  const findings = (data?.findings ?? []) as Array<Record<string, unknown>>;
  if (findings.length === 0) return <p className="p-3 text-xs text-neutral-500">no findings yet</p>;
  return (
    <div className="p-2 text-xs space-y-1 font-mono">
      {findings.map((f, i) => {
        const sev = String(f.severity ?? "info").toLowerCase();
        return (
          <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              <span className={SEVERITY_COLOR[sev] ?? "text-neutral-500"}>● {sev}</span>
              <span className="text-neutral-200">{String(f.title ?? "—")}</span>
            </div>
            {f.description && (
              <p className="text-neutral-400 mt-1 line-clamp-3">{String(f.description)}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 3: `HypothesesPane.tsx`** (uses live stream + initial fetch)

```tsx
import { useStore } from "zustand";
import { getSessionStore } from "@/state/session-store";

const STATUS_COLOR: Record<string, string> = {
  confirmed: "text-green-400",
  testing: "text-amber-400",
  proposed: "text-neutral-400",
  refuted: "text-red-400",
  abandoned: "text-neutral-600",
};

export function HypothesesPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const hypotheses = useStore(store, (s) => s.hypotheses) as Array<Record<string, unknown>>;
  if (hypotheses.length === 0) {
    return <p className="p-3 text-xs text-neutral-500">no hypotheses yet</p>;
  }
  return (
    <div className="p-2 text-xs space-y-1 font-mono">
      {hypotheses.map((h, i) => {
        const status = String(h.status ?? "proposed").toLowerCase();
        return (
          <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
            <div className="flex items-center gap-2">
              <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>● {status}</span>
              <span className="text-neutral-200 truncate">{String(h.statement ?? h.title ?? "—")}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Compile-check + commit all panes**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/layout/SessionLayout.tsx \
        desktop/renderer/src/layout/SessionStatusBar.tsx \
        desktop/renderer/src/panes/
git commit -m "feat(desktop): SessionLayout (IDE multi-pane) + chat/timeline/kb/findings/hypotheses panes"
```

---

## Task 12: F-key modals — Skill picker, Stop, Done, Sudo

**Files:**
- Create: `desktop/renderer/src/modals/SkillPickerModal.tsx`
- Create: `desktop/renderer/src/modals/SudoModal.tsx`
- Create: `desktop/renderer/src/modals/StopModal.tsx`
- Create: `desktop/renderer/src/modals/DoneModal.tsx`
- Modify: `desktop/renderer/src/layout/SessionLayout.tsx` (mount + F-key bindings)

- [ ] **Step 1: `SkillPickerModal.tsx`**

```tsx
import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useProfiles, useTriggerSkill, useSessions } from "@/api/queries";

export function SkillPickerModal({
  sessionId, open, onOpenChange,
}: {
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const profiles = useProfiles();
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);
  const profile = profiles.data?.profiles.find((p) => p.key === row?.profile);
  const trigger = useTriggerSkill(sessionId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Skills — {profile?.name}</DialogTitle>
      </DialogHeader>
      <div className="space-y-1 max-h-80 overflow-auto">
        {(profile?.skills ?? []).map((s) => (
          <button
            key={s.key}
            className="block w-full text-left p-2 rounded hover:bg-neutral-800"
            onClick={async () => {
              await trigger.mutateAsync(s.key);
              onOpenChange(false);
            }}
          >
            <div className="text-sm text-neutral-200">{s.name} · {s.key}</div>
            <div className="text-xs text-neutral-500">{s.description}</div>
          </button>
        ))}
      </div>
      <DialogFooter><Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button></DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 2: `SudoModal.tsx`**

```tsx
import { useState } from "react";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSetSudo } from "@/api/queries";

export function SudoModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [password, setPassword] = useState("");
  const setSudo = useSetSudo(sessionId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Sudo password</DialogTitle>
        <DialogDescription>
          Stored in memory on the Python service only — never written to disk.
          Required for nmap/netexec privileged scans.
        </DialogDescription>
      </DialogHeader>
      <Input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="••••••"
        autoFocus
      />
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          onClick={async () => {
            await setSudo.mutateAsync(password);
            setPassword("");
            onOpenChange(false);
          }}
        >Save</Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 3: `StopModal.tsx`**

```tsx
import { useNavigate } from "react-router-dom";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useStopSession } from "@/api/queries";

export function StopModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const stop = useStopSession();
  const navigate = useNavigate();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Stop engagement?</DialogTitle>
        <DialogDescription>
          The session is snapshotted and can be resumed later from the
          New-engagement page.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="destructive"
          onClick={async () => {
            await stop.mutateAsync(sessionId);
            onOpenChange(false);
            navigate("/");
          }}
        >Stop</Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 4: `DoneModal.tsx`**

```tsx
import { useNavigate } from "react-router-dom";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useMarkDone } from "@/api/queries";

export function DoneModal({
  sessionId, open, onOpenChange,
}: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const done = useMarkDone();
  const navigate = useNavigate();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Mark engagement done?</DialogTitle>
        <DialogDescription>
          This is terminal — the session can no longer be resumed.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          onClick={async () => {
            await done.mutateAsync(sessionId);
            onOpenChange(false);
            navigate("/");
          }}
        >Mark done</Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 5: Mount modals + F-key bindings in `SessionLayout.tsx`**

Edit `desktop/renderer/src/layout/SessionLayout.tsx`. Add state and an effect that binds F1 / F4 / F6 / Cmd+D:

```tsx
import { useEffect, useState } from "react";
// ... other imports unchanged
import { SkillPickerModal } from "@/modals/SkillPickerModal";
import { SudoModal } from "@/modals/SudoModal";
import { StopModal } from "@/modals/StopModal";
import { DoneModal } from "@/modals/DoneModal";

export function SessionLayout() {
  // ... existing state
  const [skillOpen, setSkillOpen] = useState(false);
  const [sudoOpen, setSudoOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);

  useEffect(() => {
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
  }, []);

  // ... existing JSX
  // Add at the end of the returned tree, just before the final </div>:
  // <SkillPickerModal sessionId={id!} open={skillOpen} onOpenChange={setSkillOpen} />
  // <SudoModal sessionId={id!} open={sudoOpen} onOpenChange={setSudoOpen} />
  // <StopModal sessionId={id!} open={stopOpen} onOpenChange={setStopOpen} />
  // <DoneModal sessionId={id!} open={doneOpen} onOpenChange={setDoneOpen} />
```

Update the bottom toolbar buttons to open modals instead of firing mutations directly:

```tsx
<Button size="sm" variant="ghost" onClick={() => setStopOpen(true)}>Stop (F6)</Button>
<Button size="sm" variant="ghost" onClick={() => setDoneOpen(true)}>Mark done</Button>
<Button size="sm" variant="ghost" onClick={() => setSkillOpen(true)}>Skills (F1)</Button>
<Button size="sm" variant="ghost" onClick={() => setSudoOpen(true)}>Sudo (F4)</Button>
```

- [ ] **Step 6: Compile-check + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/modals/ desktop/renderer/src/layout/SessionLayout.tsx
git commit -m "feat(desktop): F-key modals — skills (F1), sudo (F4), stop (F6), done (⌘D)"
```

---

## Task 13: Wire routes — `/new`, `/session/:id`, add Dashboard "New engagement" CTA

**Files:**
- Modify: `desktop/renderer/src/App.tsx`
- Modify: `desktop/renderer/src/pages/Dashboard.tsx` (add a CTA + recent sessions)

- [ ] **Step 1: Update `App.tsx`**

Replace the routes block in `desktop/renderer/src/App.tsx`:

```tsx
import { NewEngagement } from "@/pages/NewEngagement";
import { SessionLayout } from "@/layout/SessionLayout";
// ... (other imports unchanged)

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="/new" element={<NewEngagement />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      <Route path="/session/:id" element={<SessionLayout />} />
    </Routes>
  );
```

(Note: the `/session/:id` route is **outside** the `<Shell />` element so it gets its own full-screen IDE chrome instead of the dashboard chrome.)

- [ ] **Step 2: Update Dashboard to show recent sessions + CTA**

Replace `desktop/renderer/src/pages/Dashboard.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useProfiles, useSessions } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function Dashboard() {
  const profiles = useProfiles();
  const sessions = useSessions();
  const recent = (sessions.data?.sessions ?? [])
    .slice()
    .sort((a, b) => (b.stopped_at ?? "").localeCompare(a.stopped_at ?? ""))
    .slice(0, 8);

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center mb-6 gap-4">
        <h2 className="text-base font-medium">Dashboard</h2>
        <Link to="/new" className="ml-auto">
          <Button>New engagement</Button>
        </Link>
      </div>

      <Card>
        <CardHeader><CardTitle>Recent sessions</CardTitle></CardHeader>
        <CardContent className="text-xs space-y-1 font-mono">
          {recent.length === 0 && <p className="text-neutral-500">no sessions yet</p>}
          {recent.map((s) => (
            <Link key={s.id} to={`/session/${s.id}`}
                  className="flex gap-3 py-1 hover:bg-neutral-800 rounded px-2 transition-colors">
              <span className={
                s.state === "active" ? "text-green-400" :
                s.state === "stopped" ? "text-amber-400" :
                s.state === "completed" ? "text-blue-400" : "text-neutral-500"
              }>● {s.state}</span>
              <span className="text-neutral-300 truncate">{s.target}</span>
              <span className="text-neutral-500">· {s.profile}</span>
              <span className="text-neutral-500 ml-auto">{s.stopped_at ?? "—"}</span>
            </Link>
          ))}
        </CardContent>
      </Card>

      <div className="mt-6">
        <h3 className="text-sm font-medium mb-3">Profiles ({profiles.data?.profiles.length ?? 0})</h3>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {profiles.data?.profiles.map((p) => (
            <Card key={p.key}>
              <CardHeader>
                <CardTitle>{p.name}</CardTitle>
                <div className="text-[10px] uppercase tracking-wide text-neutral-500 mt-0.5">{p.key}</div>
              </CardHeader>
              <CardContent className="text-xs">
                <p className="text-neutral-400 line-clamp-3">{p.description || "—"}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Manual smoke**

```bash
cd desktop && npm run dev
```

Expected: Dashboard shows "New engagement" button + recent sessions section (empty initially) + profile grid. Clicking "New engagement" → wizard. Selecting a binary path + general + claude + small budget → "Start engagement" navigates to /session/<id>. The IDE layout renders, chat is empty, status bar shows the target.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/App.tsx desktop/renderer/src/pages/Dashboard.tsx
git commit -m "feat(desktop): wire /new and /session/:id routes; Dashboard CTA + recent sessions"
```

---

## Task 14: Playwright e2e — full new-engagement happy path

**Files:**
- Create: `desktop/tests/e2e/engagement.spec.ts`

- [ ] **Step 1: Write the spec**

Create `desktop/tests/e2e/engagement.spec.ts`:

```ts
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";
import fs from "fs/promises";
import os from "os";

test("new engagement → session view loads", async () => {
  // Real engagement creation needs a backend with credentials. To keep
  // this hermetic we'd point at an OpenAI-compat stub server, which is
  // out of scope here. Instead this test asserts the UI flow up to (and
  // including) the wizard "Start engagement" click; the actual session
  // page renders even before the first agent reply.
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: { ...process.env, NODE_ENV: "production", REVERSER_PENTEST_AUTHORIZED: "1" },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click("text=New engagement");
    await expect(w.locator("text=Path or URL")).toBeVisible({ timeout: 10_000 });

    // Use a throwaway temp file as the "target" — general profile, no API calls
    // happen until we click Start (and even then, the agent loop is what
    // needs a real backend; the wizard submission just creates a session).
    const tmpBinary = path.join(os.tmpdir(), `reverser-e2e-${Date.now()}.bin`);
    await fs.writeFile(tmpBinary, "stub");
    await w.fill("input[placeholder*='binary']", tmpBinary);

    // The Start button is disabled while empty; after filling the input it enables.
    await expect(w.locator("button:has-text('Start engagement')")).toBeEnabled();

    // We DON'T click Start in this test because session creation requires a
    // backend. The wizard rendering + form interaction is the smoke we care about.
  } finally {
    await app.close();
  }
});
```

- [ ] **Step 2: Build + run**

```bash
cd desktop && npm run build && npx playwright test
```

Expected: both `smoke.spec.ts` (from Plan 2) and `engagement.spec.ts` pass.

- [ ] **Step 3: Commit**

```bash
git add desktop/tests/
git commit -m "test(desktop): e2e — new-engagement wizard renders + form interactions"
```

---

## Verification

Full stack manual smoke (requires `devenv shell` + an Anthropic API key):

```bash
cd desktop && npm run dev
```

Expected:
1. Window opens, Dashboard loads, profile grid populates.
2. Click **New engagement** → wizard appears.
3. Fill target with a local binary path, profile = `general`, backend = `claude`, budget = `0.5`, max turns = `5`.
4. Click **Start engagement** → navigates to `/session/<id>`.
5. SessionStatusBar shows the target + profile + budget skeleton.
6. Type a message like "list the imports" and ⌘+Enter — assistant text streams in; tool calls show in the bottom timeline pane; budget updates after the result.
7. Click **Stop (F6)** → confirmation modal → confirm → returns to Dashboard, session marked stopped.
8. From Dashboard recent-sessions list, click the stopped session → opens it read-only.
9. Test F1 (skill picker) opens, F4 (sudo) opens, ⌘D (done) opens.
10. Test the auth-gate flow: with no `.reverser-authorized` marker and `REVERSER_PENTEST_AUTHORIZED` unset, create a session with profile `manager` → 403 caught → modal appears → confirm → marker file written → retry succeeds.

Automated:

```bash
cd desktop && npx tsc -b && npx playwright test    # both specs pass
```

## What this plan does NOT cover

Phase 2+ items, deferred:

- Full sessions sidebar with filtering (active/stopped/completed/abandoned cross-target). Phase 2.
- Per-target dashboard.
- Hypothesis tree as a real tree (Phase 3, react-arborist).
- BloodHound graph view (Phase 3, Cytoscape).
- Scope.toml editor (Phase 3).
- Screenshot evidence gallery (Phase 3 — depends on findings/screenshots endpoint in Plan 3a's "out of scope" list).
- Backend selection / API key management UI (Phase 4).
- Health dashboard polish (Phase 4).
- electron-builder packaging (Phase 4).
