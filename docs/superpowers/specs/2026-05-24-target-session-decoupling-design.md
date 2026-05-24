# Target / Session Decoupling

**Date:** 2026-05-24
**Status:** Design — pending implementation
**Author:** brainstormed with Claude

## Problem

Today the IP, URL, or binary path provided when starting a session *is* the session's identity. It drives the on-disk directory name (`targets/<target_key>/`), which owns the persistent KB (`state.db`), scope envelope (`scope.toml`), session snapshots, and logs.

This couples three things that change at different rates:

1. The **logical asset** under analysis (an Active Directory DC, a web app, a binary under reverse-engineering) — stable for the duration of an engagement.
2. The **address** by which the asset is reached (IP, URL, file path) — can change at any time: DHCP, DNS, new build of a binary, VPN routing.
3. The **session** — a unit of agent work against the asset.

When the address changes, the user loses the KB and session continuity for what is conceptually the same asset, or has to manually copy state across `targets/<dir>/` directories.

## Goal

A session is bound to a stable **target** (a named logical asset). The IP/URL/binary value becomes one of potentially many **addresses** on that target, swappable without disrupting the session, KB, or scope.

Today's one-shot ergonomics are preserved: `reverser session start 10.0.0.5` still works without ceremony and creates a target named `10.0.0.5` under the hood.

## Non-goals

- Cross-target relationships (linking a binary target to its deployed network target). Deferred — can be added later as edges without breaking this model.
- Mixed-kind targets (a single target that has both binary file paths and network addresses). Out of scope; kind is fixed at target creation.
- Per-finding version tagging in the KB ("this finding came from binary version X"). The history of binary hashes on a target gives a human-readable audit trail; per-finding versioning is a future enhancement if needed.
- Automatic detection of binary content changes on disk. Hashes are only computed when an address is added or explicitly re-hashed.

## Architecture

A new **`Target`** entity becomes the stable identity that owns the KB, scope envelope, and sessions. The IP/URL/binary path becomes one **`Address`** attached to a target — mutable, swappable, and historical.

```
Target (name, kind, addresses[], primary_address_id)
├── KB (state.db)                  ← unchanged location
├── scope.toml                     ← unchanged location
├── target.json                    ← NEW: target metadata
└── sessions/
    └── <session_id>.json          ← gains target_name, active_address_id
```

A target has exactly one **kind** (`network` or `binary`), fixed at creation. A target has one or more **addresses**; one is marked **primary**. Sessions are bound to the target by name, not to a specific address. Tool call sites that today read `sess.target` instead read `sess.target.primary_address.value` (the agent can ask the target for non-primary addresses when it has a reason to).

`target_key()` continues to derive the on-disk directory name, but now from the target's **name** rather than from a raw address string. Names default to the first address when not specified, preserving today's `reverser session start <ip>` ergonomics.

## Data Model

### `Target` (persisted as `targets/<target_key>/target.json`)

| Field | Type | Notes |
|---|---|---|
| `name` | str | Human identity. Drives `target_key`. Mutable via `rename_target`, which renames the on-disk directory atomically and is refused if any session on the target is in the `"active"` lifecycle state. |
| `kind` | `"network"` \| `"binary"` | Immutable after creation. |
| `addresses` | list[`Address`] | Append-only ordering; addresses are never deleted, only retired. |
| `primary_address_id` | str | UUID pointing at one entry in `addresses` whose `status == "active"`. |
| `created_at`, `updated_at` | iso8601 string | |
| `notes` | str (optional) | Free-text. |

### `Address`

| Field | Type | Notes |
|---|---|---|
| `id` | str | Stable UUID; referenced by sessions and `primary_address_id`. |
| `kind` | `"ip"` \| `"url"` \| `"binary"` | Must be compatible with the target's kind (`ip`/`url` for `network`; `binary` for `binary`). |
| `value` | str | The raw IP/URL/path. |
| `sha256` | str (optional) | Populated only for `binary` addresses, captured when the address is added or explicitly re-hashed. |
| `status` | `"active"` \| `"retired"` | Retired addresses stay in history and cannot be promoted to primary. |
| `added_at` | iso8601 string | |
| `retired_at` | iso8601 string (optional) | |
| `label` | str (optional) | e.g. `"internal"`, `"external"`, `"VPN"`. |

### `SessionSnapshot` changes

- **Add** `target_name: str` and `active_address_id: str` (the primary address at the moment the session started).
- **Keep** `target: str` populated from the resolved address value, for backward read-compat in any consumer that hasn't been migrated. New writes treat `target_name` and `active_address_id` as the source of truth.

### Invariants

- `primary_address_id` always resolves to an `active` address.
- Retiring the current primary requires first promoting another active address (or it fails).
- A target always has ≥1 active address. The last active address cannot be retired (would orphan sessions).
- Address `value` is unique within a target.
- `kind` on each address must match its target's `kind` (network targets accept `ip`/`url`; binary targets accept `binary`).

## Operations

### CLI

```
reverser target create <name> --kind {network|binary} [--address <value>] [--label <label>]
reverser target list
reverser target show <name>
reverser target rename <old-name> <new-name>
reverser target add-address <name> <value> [--label <label>] [--primary]
reverser target set-primary <name> <address-id-or-value>
reverser target retire-address <name> <address-id-or-value>

reverser session start <target-or-address> [--address <value>]
reverser session resume <session-id>
```

### `session start` resolution rules

1. If the positional arg matches an existing target name → use it.
2. Else if it matches the `value` of an active address on any existing target → use that target.
3. Else → create a new target on the fly. Name defaults to the arg (sanitized through `target_key`). Kind is inferred (URL/IP → `network`; file path → `binary`). The arg becomes the first address and the primary.

If `--address <value>` is supplied and the target already exists, the address is added (if new) and promoted to primary for this session and onward. This is the per-session override path: it covers "I noticed the address changed at the moment I'm starting a new session."

Rebinding to an out-of-scope address fails the scope check at session start with a clear error pointing at `scope.toml`.

### Programmatic API

A new `src/reverser/targets.py` module owns the `Target`/`Address` dataclasses and on-disk read/write (mirroring how `sessions.py` owns `SessionSnapshot`). Key functions:

```
load_target(name) -> Target
save_target(target) -> None
list_targets() -> list[Target]
create_target(name, kind, initial_address=None) -> Target
add_address(target, value, kind, label=None, make_primary=False) -> Address
set_primary(target, address_id) -> None
retire_address(target, address_id) -> None
rename_target(old_name, new_name) -> None  # renames directory atomically
rehash_binary_address(target, address_id) -> None
```

`agent_session.py` swaps its current `target: str` plumbing for a `Target` object held on the session **plus** a resolved `active_address: Address` (the address this session is actually working against). Fresh sessions set `active_address = target.primary_address` at start. Resumed sessions load `active_address` by looking up the snapshot's `active_address_id` on the target — so a session that started before a rebind continues to use its original address, even if the target's primary has since changed.

The equivalent of today's `sess.target` for tool dispatch is `sess.active_address.value`. Code that needs to reason about the target as a whole (e.g., the agent looking for alternative addresses) reads `sess.target`.

If the snapshot's `active_address_id` no longer exists on the target (e.g., the user retired *and* removed it — not currently supported, but a future possibility), resume fails with a clear error rather than silently falling back to the primary.

### Tool dispatch

- `tools/dispatch.py` (around line 295): replace `sess.target` reads with `sess.active_address.value` (the session's pinned address — see Programmatic API above for why this is not always the target's current primary). Same semantics for live sessions; correct semantics for resumed sessions.
- `tools/web_browser.py`'s "target mismatch reset" logic continues to compare the resolved address value, so rebinding to a new URL correctly invalidates the cached browser singleton.
- Scope envelope (`scope.toml`) is checked against the currently-resolved primary address. A scope-failing rebind refuses session start with an actionable error.

### Hashing (binary targets)

`sha256` is computed at the moment an address is added, when `add_address` is called with a binary value, or when `rehash_binary_address` is explicitly invoked. No background watcher; no detection of "the file changed underneath." If the user wants a fresh hash they re-add the address or call `rehash`.

## Persistence

### On-disk layout (per target)

```
targets/<target_key>/
├── target.json        # NEW — Target + Address metadata
├── state.db           # unchanged — KB (hosts, services, hypotheses, findings, ...)
├── scope.toml         # unchanged — scope envelope
└── sessions/
    └── <session_id>.json   # SessionSnapshot, now with target_name + active_address_id
```

`target_key()` continues to be a filesystem-safe slug, but is now derived from `Target.name` instead of from a raw address string. The sanitization rules (max 64 chars, lowercase, strip `_.-`) are unchanged.

### `target.json` schema

```jsonc
{
  "name": "acme-dc1",
  "kind": "network",
  "primary_address_id": "01HZ...",
  "addresses": [
    {
      "id": "01HZ...",
      "kind": "ip",
      "value": "10.0.0.5",
      "status": "active",
      "label": "internal",
      "added_at": "2026-05-24T14:23:00Z"
    },
    {
      "id": "01HX...",
      "kind": "url",
      "value": "https://acme.example.com",
      "status": "retired",
      "label": "external",
      "added_at": "2026-05-20T09:00:00Z",
      "retired_at": "2026-05-24T14:23:00Z"
    }
  ],
  "created_at": "2026-05-20T09:00:00Z",
  "updated_at": "2026-05-24T14:23:00Z",
  "notes": null
}
```

Writes are atomic via the same `.tmp` rename pattern used by `sessions.py:save()`.

### Migration

**No migration.** The user has confirmed no important state needs to be preserved. The cutover is clean: the new code reads `target.json`; if a `targets/<dir>/` directory lacks one, that directory is ignored by the new model. The user is expected to clear or archive old target directories before adopting the new build.

This is captured here so the implementation plan does not introduce migration code that won't be exercised.

## Desktop UI Changes

### Session creation flow

The "new session" form in the desktop renderer gains a target picker with two modes:

- **Existing target**: dropdown of target names. Below it, a read-only line shows the current primary address. An optional "Use a different address for this session" toggle reveals an address input (per-session override).
- **New target**: current single-field "target" input, plus an optional "name" field that defaults to the address as you type. Kind is inferred from the input format and shown as a read-only chip.

Server-side, the `CreateSession` request model in `src/reverser/gui_service/routes/sessions.py` gains optional `target_name` and `address` fields. The legacy single `target` field continues to work — it is run through the same `session start` resolution rules described above.

### New "Targets" pane

A new top-level pane (`desktop/renderer/src/panes/TargetsPane.tsx`) lists all targets with their kind, primary address, total addresses, session count, and last-active time. Selecting a target shows:

- The address list (active + retired, with labels and `sha256` for binaries)
- Actions: "Add address," "Set primary," "Retire," "Rename"
- The session list filtered to this target
- A KB summary card reusing the existing `useTargetKB` data

This is additive — no existing pane is removed.

### Existing panes — minimal updates

- `HypothesesPane.tsx` and any other pane that currently does the `useSessions()` → find session → extract `target` two-step: switch to reading `target_name` directly off the session, then call a new `useTarget(name)` to get the full target object. `useTargetKB` continues to work — it is called with `target.primary_address.value`, or refactored to take a target name and resolve internally.
- `SessionState` in `desktop/renderer/src/state/session-store.ts` gains a `targetName: string` field, sourced from the new server fields. A new `targets` slice mirrors the `sessions` slice.

### New API endpoints

- `GET /api/targets` — list targets (name, kind, primary address summary, counts)
- `GET /api/targets/<name>` — full target with all addresses
- `POST /api/targets` — create
- `PATCH /api/targets/<name>` — rename, update notes
- `POST /api/targets/<name>/addresses` — add address
- `PATCH /api/targets/<name>/addresses/<id>` — set primary, retire, relabel
- `POST /api/targets/<name>/addresses/<id>/rehash` — re-hash binary

## Error Handling

- **Adding a duplicate address value to a target** → 400, message names the existing address.
- **Setting a retired address as primary** → 400, message tells the user to re-add it.
- **Retiring the only active address** → 400, message tells the user to add another first.
- **Adding an address whose kind doesn't match the target kind** (e.g., a URL on a binary target) → 400, names both kinds.
- **Rebinding to an out-of-scope address** → session start refused, points at `scope.toml` and the failing CIDR/URL.
- **`session start` against an unknown name with no inferred kind** → 400, asks user to specify kind or use a valid IP/URL/path.
- **Rename collision** (renaming to a name already in use) → 400, names the existing target.
- **Rename with active sessions** (any session on the target in lifecycle state `"active"`) → 400, lists the active session ids and tells the user to stop them first.

## Testing

### Unit tests

- `targets.py`: round-trip serialization, invariant enforcement (primary always active, kind compatibility, retirement rules), atomic writes.
- `target_key` derivation when name is/isn't an address.
- Address resolution rules in `session start` (name match, address match, on-the-fly create).
- SHA256 hashing on binary address add and rehash.

### Integration tests

- Create target, add second address, set primary, start session, verify session records the right `active_address_id` and that tool dispatch sees the new primary's value.
- Rebind to out-of-scope address → session start fails with scope error.
- Retire primary without promoting another → fails; promote then retire → succeeds.
- Rename target → directory moved atomically; in-flight session pointers still resolve.
- `session start <known-address>` resolves to the existing target (no duplicate creation).

### Manual test plan

- Walk through the desktop "new session" form in both modes (existing target / new target).
- Create a binary target, swap its file to a different build, verify the addresses list shows both hashes.
- Verify that resuming a session that was started before a rebind reuses the snapshot's `active_address_id` (not the current primary), so the resumed run continues against the address it was originally working on.

## Open Questions / Future Work

- Should resuming a session re-resolve to the *current* primary (auto-follow rebinds) or stay pinned to the address it started with? Current design says **stay pinned to `active_address_id`** for predictability; a `--follow-primary` resume flag could be added later.
- Cross-target relationships ("this binary deploys at that network target") — deferred.
- Per-finding version tagging for binary targets — deferred.
- Bulk import of targets from a scope file — deferred.
