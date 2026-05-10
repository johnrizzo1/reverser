# Stop & resume sessions — design

**Date:** 2026-05-09
**Status:** Design approved; ready for implementation plan
**Predecessor:** `docs/superpowers/specs/2026-05-09-manager-profile-design.md` (manager profile)
**Sibling work:** Live budget/turn adjustment — separate spec, deferred (the manager profile's "Budget" skill is a no-op until that work lands)

---

## 1. Goal

Add the ability to stop an interactive `reverser` session and resume it later — possibly in a different process or on a different day. Use case: long-running engagements (manager profile, multi-hour AD pentest) where the operator needs to step away and pick up where they left off, with budget tracking, conversation history, and UI state intact.

## 2. Non-goals (v1)

- **Live budget/turn adjustment** — separate work; tracked as Feature A.
- **Resume across hosts** — snapshots are filesystem-bound; cross-host resume requires manual file copying (documented, not productized).
- **Multi-user collision detection beyond PID liveness** — concurrent sessions on the same target overwrite each other's snapshots.
- **Pruning / cleanup commands** — no `reverser sessions prune` or auto-cleanup; user manages with `rm`.
- **Snapshot encryption / sensitive-data scrubbing** — the snapshot may contain credentials/tokens; trust posture matches the KB (host-trusted).
- **Resume into a different profile** — refused; start a new session if you want to switch profiles.
- **Multi-host coordination** — no file locking; concurrent write to the same snapshot is documented as "don't do that without `--force`."
- **Migration tooling for v1 → v2 schema bumps** — added when v2 ships, not pre-built.

## 3. Architectural decisions (with rationale)

| # | Decision | Rationale |
|---|---|---|
| D1 | Resume scope = "full snapshot" (operator state + conversation history + UI hints). | User chose this level explicitly. KB is the durable memory but conversation continuity matters for non-trivial multi-day work. |
| D2 | UI state level = "minimal operator surface" (focused panel, scroll position, last skill key, input buffer) + chat pane re-rendered from session log. | Avoids coupling to Textual internals. Survives Textual upgrades. The operator-surface fields are stable shapes we own. |
| D3 | Persistence location = `targets/<target>/sessions/<id>.json`. | Matches the per-target organization established by the AD pack. Filesystem-based for arbitrary fields without schema migration. Easy to enumerate per target. |
| D4 | Session ID format = ISO timestamp `YYYY-MM-DDTHH-MM-SS`. | Sortable, human-readable, unique-enough at human pace. Filename-safe (colons replaced with hyphens). |
| D5 | New launch = new session always; resume is explicit via `--resume [SESSION_ID]`. | User decision. No accidental resume; explicit opt-in. |
| D6 | Snapshot timing = autosave per turn + explicit stop button + guaranteed snapshot on TUI exit (atexit + SIGTERM handlers). | Crash recovery for free; explicit stop sets the lifecycle flag; atexit covers between-turn exits. |
| D7 | Lifecycle states = `active`, `stopped`, `completed`. State transitions: `active → stopped` (user stop), `active → completed` (user `/done` or manager wrap-up), `stopped → active` (resume), `completed` is terminal. | Simple state machine; clear semantics for the resume listing UX. |
| D8 | Conversation history scope = "store all + recent N for prompting." Single source of truth (the snapshot's `conversation` list); prompt builder picks the last 8. | Avoids divergence between rendering data and prompting data. |
| D9 | In-flight dispatch handling = abandon with 5-second grace + record in `in_flight` field. | Sub-agent state isn't serializable. Manager profile sees the abandoned dispatch on resume and decides to re-dispatch or pivot. The cost-to-date counts (we already paid). |
| D10 | Listing CLI = top-level `reverser --list-sessions` (across all targets); target-scoped if a target arg is given. | Matches user's stated preference; useful for global "what sessions do I have" view. |
| D11 | Concurrency = check `pid` field via `os.kill(pid, 0)`; refuse resume if alive; `--force` overrides with a warning. | Pragmatic — we can't do real coordination without cross-process locks; PID liveness covers the common footgun. |
| D12 | Module = new `src/reverser/sessions.py` (peer of `src/reverser/session_log.py`). | One owns mutable session state; the other owns append-only audit. Different responsibilities. |
| D13 | Per-exchange cost = stored in `ConversationEntry.cost`. | Enables per-turn cost annotations in UI; survives resume independent of `stats.total_cost`. |

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ reverser i -p manager 10.10.10.5 [--resume [SESSION_ID]]         │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ Session (top-level claude_agent_sdk.query loop)          │   │
│   │                                                          │   │
│   │   stats: total_cost, turns                               │   │
│   │   exchanges: [Exchange(user, agent, turn, ts, cost), …]  │   │
│   │   _snapshot: SessionSnapshot ◄── owns the on-disk state │   │
│   │   _stop_requested: bool                                  │   │
│   │   _cancel: bool (existing)                               │   │
│   │                                                          │   │
│   │   stop()         → state=stopped, save, exit             │   │
│   │   mark_completed() → state=completed, save, exit         │   │
│   │   _run_one_turn  → after exchange: autosave              │   │
│   └──────────────────────────────────────────────────────────┘   │
│                          ▲                                       │
│                          │ ContextVar('current_session')         │
│                          │                                       │
│   ┌──────────────────────┴───────────────────────────────────┐   │
│   │ dispatch_specialist tool                                 │   │
│   │   on start: session._snapshot.in_flight = {…}; save      │   │
│   │   on cancel: hard-cancel within 5s, return cancelled     │   │
│   │   on done:  in_flight = None; save                       │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ TUI (tui/app.py)                                         │   │
│   │   f3 / /stop  → modal → Session.stop()                   │   │
│   │   /done       → modal → Session.mark_completed()         │   │
│   │   f5 / /info  → read-only modal: session info            │   │
│   │   atexit + SIGTERM → emergency snapshot save             │   │
│   │   --resume   → replay snapshot.conversation in chat pane │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ targets/10.10.10.5/sessions/                             │   │
│   │   2026-05-09T14-23-00.json   ◄─ atomic writes            │   │
│   │   2026-05-08T09-12-44.json                               │   │
│   │   2026-05-07T13-00-12.json                               │   │
│   └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

## 5. Snapshot schema

```python
from dataclasses import dataclass, field
from typing import Literal, Optional

SessionState = Literal["active", "stopped", "completed"]


@dataclass
class SessionConfig:
    profile: str
    backend: str = "claude"
    model: Optional[str] = None
    api_base: Optional[str] = None
    budget: float = 5.0
    max_turns: int = 50
    max_parallel: int = 1


@dataclass
class SessionStats:
    total_cost: float = 0.0
    turns: int = 0


@dataclass
class ConversationEntry:
    user: str
    agent: str
    turn: int
    timestamp: str   # ISO-8601
    cost: float      # USD spent on this exchange


@dataclass
class UIState:
    focused_panel: str = "chat"     # chat | log | status
    chat_scroll_position: int = 0
    last_skill_key: Optional[str] = None
    input_buffer: str = ""


@dataclass
class InFlightDispatch:
    kind: Literal["dispatch"]
    specialty: str
    hypothesis_id: Optional[int]
    sub_goal: str
    started_at: str


@dataclass
class SessionSnapshot:
    session_id: str                 # 2026-05-09T14-23-00
    target: str
    log_path: str                   # relative to repo root
    state: SessionState
    started_at: str
    last_active_at: str
    stopped_at: Optional[str] = None

    config: SessionConfig = field(default_factory=lambda: SessionConfig(profile="general"))
    stats: SessionStats = field(default_factory=SessionStats)
    conversation: list[ConversationEntry] = field(default_factory=list)
    ui: UIState = field(default_factory=UIState)

    in_flight: Optional[InFlightDispatch] = None
    pid: Optional[int] = None       # set while running; cleared on clean stop

    schema_version: int = 1
```

**On-disk format:** JSON, indented, dataclass-encoded via `asdict`. Snapshot path: `targets/<target>/sessions/<session_id>.json`.

**Atomic write:** write to `<path>.tmp`, then `os.replace(<path>.tmp, <path>)`. POSIX `rename` is atomic; partially written snapshots never appear at the canonical path. Crash mid-write at worst leaves a `.tmp` orphan; `list_*` helpers ignore `.tmp` files.

## 6. Module API

```python
# src/reverser/sessions.py

def snapshot_path(target: str, session_id: str) -> Path: ...
def make_session_id() -> str: ...
def new_snapshot(*, target: str, log_path: str, config: SessionConfig) -> SessionSnapshot: ...

def save(snapshot: SessionSnapshot) -> None:
    """Atomic write. Updates last_active_at to now before serializing."""

def load(target: str, session_id: str) -> SessionSnapshot:
    """Raises SessionNotFoundError if missing, SchemaError if version unknown."""

def list_for_target(target: str, *, exclude_completed: bool = False) -> list[SessionSnapshot]:
    """Sorted by last_active_at desc."""

def list_all(*, exclude_completed: bool = False) -> list[SessionSnapshot]: ...
def latest_for_target(target: str, *, exclude_completed: bool = True) -> SessionSnapshot | None: ...
def latest_global(*, exclude_completed: bool = True) -> SessionSnapshot | None: ...

def is_session_alive(snapshot: SessionSnapshot) -> bool:
    """True iff snapshot.pid is set AND os.kill(pid, 0) succeeds."""


class SessionNotFoundError(Exception): ...
class SessionStateError(Exception): ...   # resuming completed, or live takeover w/o --force
class SchemaError(Exception): ...
```

## 7. Session integration

```python
class Session:
    def __init__(
        self, target: str, profile: Profile, backend: Backend, *,
        budget: float = 5.0, max_turns: int = 50, max_parallel: int = 1,
        log_path: str | None = None,
        resume_from: SessionSnapshot | None = None,
    ):
        if resume_from is not None:
            self._init_resumed(resume_from, profile, backend)
        else:
            self._init_new(target, profile, backend, budget, max_turns,
                           max_parallel, log_path)

    def stop(self) -> None:
        """User-initiated stop. State → stopped; persists; logs session_end."""

    def mark_completed(self) -> None:
        """Terminal — session won't be offered for resume by default."""
```

**Per-turn autosave hook** runs at the end of `_run_one_turn`:

```python
self._snapshot.stats.total_cost = self.stats.total_cost
self._snapshot.stats.turns = self.stats.turns
self._snapshot.conversation = self._build_conversation_entries()
self._snapshot.last_active_at = _now_iso()
sessions.save(self._snapshot)
```

**`Session.findings` refactor:** the current `findings: list[str]` becomes `exchanges: list[Exchange]` where:

```python
@dataclass
class Exchange:
    user: str
    agent: str
    turn: int
    timestamp: str
    cost: float
```

Anywhere the existing prompt builder reads `findings`, it now reads `exchanges` and projects to strings. The snapshot's `conversation` field is built from `exchanges`.

**ContextVar** for session-aware tools:

```python
# src/reverser/sessions.py (or a new src/reverser/_context.py)
from contextvars import ContextVar
current_session: ContextVar["Session | None"] = ContextVar("current_session", default=None)
```

`Session._init_new` and `_init_resumed` set this; `dispatch_specialist` reads it to access `session._snapshot.in_flight`.

## 8. CLI surface

```sh
# List sessions
reverser --list-sessions                    # all sessions, all targets
reverser <target> --list-sessions           # all sessions for a target
reverser --list-sessions --include-completed # show completed too (default already shows)

# Start
reverser i -p manager 10.10.10.5            # new session

# Resume
reverser i 10.10.10.5 --resume              # resume latest for target
reverser i --resume                         # resume latest globally (no target needed)
reverser i --resume 2026-05-09T14-23-00     # resume specific session by ID

# Resume override edge cases
reverser i --resume 2026-05-09T14-23-00 --force   # take over a live session
```

**Argument resolution rules:**

- `--resume` with ID and target arg: target arg must match snapshot.target (else error). Snapshot wins for everything else (profile, budget, max_turns) UNLESS the corresponding CLI flag was explicitly given (`-p`/`--budget`/`--max-turns`).
- `--resume` without ID: target arg → `latest_for_target`. No target → `latest_global`. Same override rules.
- `-p` flag is silently ignored on resume unless it differs from the snapshot's profile, in which case error: "Resume must use the same profile (snapshot uses 'manager'; got '-p ad'). Drop -p to use the snapshot's profile, or start a new session."
- `--budget` / `--max-turns` on resume: explicit override, takes effect from the next turn forward. Useful for "give myself more budget on resume."
- `--list-sessions` is a top-level flag; parsed before subcommand dispatch.

**`--list-sessions` output:**

```
Sessions across all targets:
  TARGET           ID                       STATE       PROFILE   STARTED              LAST ACTIVE          TURNS  COST
  10.10.10.5       2026-05-09T14-23-00      active*     manager   2026-05-09 14:23     2026-05-09 18:47     47     $1.84
  10.10.10.5       2026-05-08T09-12-44      stopped     manager   2026-05-08 09:12     2026-05-08 17:33     112    $4.21
  10.10.10.7       2026-05-08T11-00-00      completed   ad        2026-05-08 11:00     2026-05-08 14:18     38     $1.12

* state="active" with stale last_active_at means the session was probably crashed/killed; safe to resume.
Resume the latest session for a target with: reverser i <target> --resume
Resume a specific session with:              reverser i --resume <ID>
```

By default `completed` sessions are shown but visually deemphasized (greyed in TTYs that support it). `--no-completed` hides them.

## 9. TUI surface

**Keybindings (added in `tui/app.py`):**

| Key | Action |
|---|---|
| `f3` | Stop & save (modal confirmation) |
| `f4` | Reserved for live-budget-adjustment (Feature A); no-op stub for now |
| `f5` | Show session info (read-only modal) |

**Slash commands** (intercepted before forwarding to agent):

| Command | Behavior |
|---|---|
| `/stop` | Same as `f3`. |
| `/done` | Mark session completed (modal confirmation; terminal). |
| `/info` | Same as `f5`. |
| `/help` | Lists slash commands and keybindings. |

**Header:** `Reverser Agent — Profile: Manager  Session: 2026-05-09T14-23-00 (resumed, 47/50 turns, $1.84/$5.00)`

**Resume-time chat pane behavior:**

1. On startup with `--resume`, before the input prompt becomes active, the chat pane re-renders each entry in `snapshot.conversation` in order.
2. For long sessions (200+ entries), show a thin progress bar `Replaying conversation: 47/47`.
3. If `snapshot.in_flight` is set, append a system note: `⚠ Previous session was stopped during dispatch to <specialty> (hypothesis #N). Hypothesis status is still 'testing'.`

**Modals** under `tui/modals/`:

- `stop_confirm.py` — "Stop session? Snapshot will be saved as resumable. [Yes] [No]"
- `done_confirm.py` — "Mark session as completed? Won't appear in resume list by default. [Yes] [No]"
- `info.py` — read-only display of session_id, target, profile, started_at, last_active_at, total_cost vs budget, turns vs max_turns, state.

## 10. Stop / resume / crash flows

### Stop flow

1. User triggers stop (f3, /stop). Modal confirms.
2. On confirm: `Session.stop()` sets `_cancel=True` and `_stop_requested=True`, sets snapshot `state="stopped"`, `stopped_at=now`, `pid=None`, saves.
3. Currently-in-flight turn finishes its current SDK round-trip if one's in flight (existing `cancel()` mechanism breaks the loop on next event boundary).
4. If `in_flight` was set (mid-dispatch), the dispatch sub-agent is hard-cancelled after a 5-second grace; partial cost counts.
5. `SessionLog.log_session_end(subtype="stopped_by_user")` writes the final audit event.
6. TUI unmounts cleanly and exits.

### Resume flow

1. CLI parses `--resume [SESSION_ID]`. Resolves to a snapshot via `load()` / `latest_*` helpers.
2. Liveness check: if `is_session_alive(snapshot)` true → refuse with "Session is currently running in PID X. Use --force to take over." `--force` proceeds with a warning.
3. State check: if `snapshot.state == "completed"` → refuse with "Session is completed and cannot be resumed." `--force` does NOT override (terminal).
4. `Session(... resume_from=snapshot)` constructor restores state. Snapshot's `pid` is set to current PID, `state` flipped back to `active`, `last_active_at` updated.
5. TUI's chat pane replays `snapshot.conversation` (with progress bar if long).
6. `SessionLog.log_session_resumed(session_id, prior_turn=stats.turns, prior_cost=stats.total_cost)` event is appended to the existing JSONL log.
7. If `in_flight` was set: chat pane shows the system note; the manager profile's first prompt includes a notice in its `kb_show` output (manager sees the abandoned dispatch in the hypothesis tree's `dispatched_to` field with `status='testing'`).

### Crash flow

1. Per-turn autosave means the most-recent completed turn already wrote a snapshot with `state="active"`.
2. `atexit.register(_emergency_snapshot)` and `signal.signal(SIGTERM, ...)` give us best-effort save on Python interpreter shutdown.
3. If Python itself crashes before either runs (segfault, OOM-killer SIGKILL): the most recent per-turn snapshot is the recovery point.
4. On next launch, `--resume` finds the snapshot in `state="active"` with stale pid; liveness check fails; treats as resumable; flips state to `active` with own PID.

## 11. File change set

### Add

| Path | Purpose |
|---|---|
| `src/reverser/sessions.py` | Snapshot dataclass + save/load + listing helpers + ContextVar |
| `src/reverser/tui/modals/stop_confirm.py` | f3 / /stop confirmation |
| `src/reverser/tui/modals/done_confirm.py` | /done confirmation |
| `src/reverser/tui/modals/info.py` | f5 / /info display |
| `src/reverser/tui/modals/__init__.py` | package init |
| `tests/test_sessions_module.py` | sessions.py unit tests |
| `tests/test_session_resume.py` | Session resume integration |
| `tests/test_session_lifecycle.py` | state machine transitions |
| `tests/test_cli_sessions.py` | CLI behavior |
| `tests/test_dispatch_in_flight.py` | dispatch tool in_flight hook + cancel |
| `tests/manual/resume_smoke.md` | human-in-loop walkthrough |

### Modify

| Path | Change |
|---|---|
| `src/reverser/tui/session.py` | Refactor `findings` → `exchanges` (with cost); add `resume_from` to __init__; add `stop()` and `mark_completed()`; per-turn autosave; ContextVar wiring |
| `src/reverser/tui/app.py` | New keybindings (f3, f4, f5); slash-command interceptor; atexit + SIGTERM handlers; header + chat-pane resume rendering |
| `src/reverser/cli.py` | Top-level `--list-sessions` flag; `--resume [SESSION_ID]` on interactive; resume-aware argument resolution |
| `src/reverser/session_log.py` | New event kinds: `session_resumed`, `session_stopped`, `session_completed`; corresponding `log_*` methods |
| `src/reverser/tools/dispatch.py` | Read `current_session` ContextVar; mutate `in_flight`; honor cancel signal (5-second grace) |
| `src/reverser/profiles/manager.py` | `SKILL_WRAPUP` prompt: append "When done, tell the user to type `/done` to mark the session completed and exit." |

### Does not change

- KB schema (sessions live on the filesystem, not in SQLite)
- The 13 other profile modules
- Backends (`backends/claude.py`, `backends/base.py`)
- `kb/` package
- `devenv.nix`

## 12. Testing strategy

### Unit (no SDK, no TUI)

- `test_sessions_module.py` — round-trip save→load identity; atomic write under simulated crash; `is_session_alive` (own PID true, fake PID false); `list_*` sort order; `latest_*` excludes completed by default; `SchemaError` on bad version.
- `test_session_lifecycle.py` — fresh session → active; `stop()` → stopped; `mark_completed()` → completed; per-turn autosave is idempotent; transitioning out of completed is rejected.
- `test_session_resume.py` — `Session.__init__(resume_from=snap)` restores all fields; resumed session continues writing to the same JSONL log; pid is reset on resume; conversation list survives roundtrip.
- `test_dispatch_in_flight.py` — `dispatch_specialist` sets `in_flight` before SDK call and clears after; mid-dispatch cancel honored within 5s; cancelled dispatch returns `status="cancelled_by_user"`.

### Integration (mocked SDK)

- `test_cli_sessions.py` — `--list-sessions` produces expected output for fixture sessions; `--resume` without arg picks correct latest; `--resume <id>` loads correct snapshot; `--resume` against completed errors; `--force` takes over live (simulated by setting `pid=os.getpid()` of a known-dead pid).

### Manual smoke (out-of-suite)

`tests/manual/resume_smoke.md` — 20-minute walkthrough:
1. Start a session, do a few exchanges, verify snapshot file exists and updates.
2. Hit `f3`, confirm exit, verify `state="stopped"`.
3. Re-launch with `--resume`, verify chat pane re-renders, continue working, verify cost continues from prior total.
4. Crash simulation: start session, kill the TUI process (Ctrl+\\), re-launch with `--resume`, verify recovery.
5. `--list-sessions` shows all three correctly.
6. `/done` to mark completed; verify `--resume` no longer offers it; `--list-sessions --include-completed` (or default — completed shown by default) does.

## 13. Edge cases

| Edge case | Behavior |
|---|---|
| Snapshot file missing on resume | `SessionNotFoundError` → CLI errors with hint |
| Snapshot file corrupted (bad JSON) | `SchemaError` or `json.JSONDecodeError` → CLI errors with backup-and-restart hint |
| Snapshot has `pid` set, that PID belongs to a different process (PID reuse) | Best-effort: we can't tell; accept takeover with `--force`; documented |
| Multiple TUIs concurrently writing the same snapshot | Last write wins (atomic per file). No locking; documented as "don't do that" |
| Resume after `tools_allowlist` changed (manager profile updated between sessions) | Recompute from current profile on resume; snapshot doesn't store it |
| Resume after profile addendum changed | Recompute from current profile on resume; conversation history may reference behaviors no longer in the prompt — agent reads KB to ground itself |
| Resume across reverser version upgrade (schema_version mismatch) | `SchemaError`. v1 → v2 will need a migrator written when v2 ships |
| Resume across machine moves | Snapshot uses relative `log_path`; works as long as `targets/` and `logs/` are present |
| `--resume` with no target and no sessions exist anywhere | Clear error: "No sessions to resume. Start with: reverser i -p <profile> <target>" |
| Resume profile mismatch (`-p ad --resume <manager-session>`) | Error before TUI starts; suggests dropping -p |

## 14. Risks & open questions

| Risk | Mitigation |
|---|---|
| Per-turn autosave adds latency to every turn | Snapshot is a few KB JSON; atomic-rename is fast. Expected cost is sub-100ms — measure during implementation; if it becomes a bottleneck, throttle to every-N-turns or move to a background thread. Negligible at LLM-turn timescales (turns are seconds). |
| Conversation replay on resume "warms" context with stale data | Manager profile reads KB on first turn after resume to re-ground. Non-manager profiles get a worse experience; v2 could add a "resume summary" injection. |
| In-flight dispatch's partial cost is lost if SDK doesn't report it on cancel | The SDK's ResultMessage on cancellation should include partial cost; if it doesn't, we lose that accounting. Documented; v1 accepts the slight under-count. |
| Two operators resuming the same session (file conflict) | PID liveness check + `--force` warning; documented as user responsibility. |
| Snapshot bloats with very long sessions (1000+ turns) | Per-turn autosave keeps writing. Snapshot grows linearly with conversation. At ~1KB/exchange, 1000 turns = 1MB JSON file — still fast to load. v2 could compress or tier-out old exchanges. |

## 15. Future work (explicitly v2+)

- Conversation summary injection on resume (LLM-generated "here's what we did in the prior session" instead of raw replay).
- File locking for concurrent-writer safety.
- Snapshot compression for very long sessions.
- `reverser sessions prune` cleanup command.
- Cross-host session export/import (tarball of snapshot + log + KB slice).
- TUI session picker: launch `reverser i` with no args, get a picker showing all resumable sessions across targets.
- Configurable per-target session limits (auto-archive / delete after N).
- Resume-into-different-profile (with explicit migration of state).
