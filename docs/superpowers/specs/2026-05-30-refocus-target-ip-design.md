# Refocus Target IP — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-30
> Origin: when a target's IP changes (e.g. a Hack The Box machine is reset and gets a new IP) the
> operator needs to re-point the engagement at the new address without losing the per-target KB.
> Today there is no way to edit/add a target's IP from the GUI, refocus a running session, or have
> the agent switch IPs on request.

## Goals

- Change a target's current IP and have **subsequent tool calls use the new IP** — from the GUI, from
  a running session (live, no restart), and via an agent tool the operator can invoke in chat.
- **Preserve all KB knowledge** (same logical target) and **remap** existing host/service rows from
  the old IP to the new one so prior recon stays attached to the current address.
- Optionally update the `hostname → IP` line in `/etc/hosts`.
- Respect scope: never silently refocus onto an out-of-scope IP.

## Non-goals

- No change to target *identity* (the KB dir is keyed by `Target.name`, not IP — already true).
- No automatic re-running of recon against the new IP (the agent does that as normal).
- Not interrupting an in-flight dispatch (it finishes against the old IP; the next action uses the new).

## Context (current model)

- `Target` (`src/reverser/targets.py`) already decouples a stable `name` from an append-only list of
  UUID'd `Address` records with a `primary_address_id`. KB lives at `targets/<name-slug>/state.db`.
  Primitives exist: `add_address(..., make_primary=True)`, `set_primary()`, retire; plus endpoints
  `POST /api/targets/<name>/addresses` and `PATCH .../addresses/<id>`.
- `AgentSession.from_target` pins `active_address` / `active_address_id`; tools/dispatch read the
  engagement string `self.target`.
- KB schema keys on IP: `hosts (PK target_id, ip)`, `services (PK target_id, host_ip, port, proto)`,
  `cred_results.target_host` (TEXT). Findings/artifacts/notes/credentials/hypotheses key on
  `target_id`, not IP.
- `scope.py` validates an IP against `scope.toml` CIDRs/exclusions.

## Decisions (from brainstorming)

- **Surfaces:** GUI edit/add IP **+** refocus a running session **+** agent KB tool.
- **KB records:** **remap** old→new IP (rewrite rows), keep the old `Address` in history (not retired),
  record a KB note.
- **Hostname:** **also offer** to update `/etc/hosts` (opt-in, needs sudo, best-effort).
- **Architecture:** Approach A — a shared `refocus_target()` core with thin surfaces.

## Design

### 1. Core — `src/reverser/refocus.py`

```
refocus_target(target_name, new_ip, *, update_etc_hosts=False, hostname=None, force_scope=False)
    -> RefocusResult
```
Sequence:
1. Resolve `Target` + current primary (`old_ip`). If `new_ip == old_ip` → no-op success.
2. Scope: if `scope.toml` exists and `new_ip` out of scope → abort (clear message naming the IP)
   unless `force_scope=True`. No scope file → proceed.
3. Address: `add_address(new_ip, kind="ip", make_primary=True)` — reuse + re-promote if `new_ip`
   already exists in history. Old address stays in history (non-primary, not retired).
4. KB remap: `KB(target).remap_address(old_ip, new_ip)` (§2) + note "Refocused <old>→<new>".
5. `/etc/hosts` (only if `update_etc_hosts`): rewrite `hostname → old_ip` to `new_ip` (§4); best-effort.
6. Return `RefocusResult { target, old_ip, new_ip, rows_remapped, hostname_updated, scope_warning,
   session_refocused }` (dataclass; surfaces render a consistent summary).

Durable steps (address, KB) commit before the best-effort `/etc/hosts` step.

### 2. KB remap — `KB.remap_address(old_ip, new_ip) -> dict(counts)` in `kb/store.py`

Single transaction; handles PK conflicts:
- **hosts** (`PK target_id, ip`): no `new_ip` row → `UPDATE ip`; else merge non-null fields from old
  into existing new row, delete old row.
- **services** (`PK target_id, host_ip, port, proto`): per old row → if `(new_ip, port, proto)` absent
  update `host_ip`; else keep existing new row, delete old duplicate.
- **cred_results.target_host** (TEXT): `UPDATE ... WHERE target_host=old`.
- findings/artifacts/notes/credentials/hypotheses: untouched (keyed on `target_id`; free-text history).
- Records a note "Refocused <old>→<new>; remapped N hosts, M services" and returns counts.
- Emits KB events so GUI Hosts/Services panes refresh live.

### 3. Live-session refocus — `AgentSession.refocus_address(new_address)` in `agent_session.py`

- Update `self.active_address` and `self.target` (legacy string read by tools/dispatch).
- Set `self._snapshot.active_address_id = new_address.id`; save snapshot (resume stays on new IP).
- Append a conversation/system marker: "Engagement refocused: target IP is now <new_ip> (was
  <old_ip>). Use <new_ip> for all subsequent tool calls." — this is what refocuses the model.
- Emit a session event so the GUI active-address badge updates.

Reached by: the agent tool (via `current_session`), and the GUI endpoint (via `session_manager`
looking up an active session for the target; none → `session_refocused=false`).

### 4. Surfaces

**GUI** — `POST /api/targets/<name>/refocus` (`gui_service/routes/targets.py`): body
`{ new_ip, update_etc_hosts?, hostname?, force_scope? }` → `refocus_target(...)` → refocus any active
session → return `RefocusResult`. Control: a "Refocus / Change IP" form in `TargetOverview` (+ target
row action in `TargetsPanel`): new IP, "update /etc/hosts" checkbox (only when a hostname is known),
`force` checkbox surfaced only after a scope failure; renders the result summary.

**Agent tool** — `kb_refocus_target` (`tools/kb.py`): args `target`, `new_ip`, `update_etc_hosts?`,
`hostname?`, validated via a small Pydantic model; calls `refocus_target` + `current_session
.refocus_address`; returns a `format_tool_result` summary. (Network/manager allowlists are `None` =
all-allowed; no change needed.)

**/etc/hosts** — pure helper rewrites the `hostname → old_ip` line to `new_ip` (adds if absent),
applied via `sudo`; sudo unavailable/declined → `hostname_updated=false` with the exact line to add.
Never fails the refocus.

**Scope** — `refocus_target` calls `assert_in_scope(new_ip)` when `scope.toml` exists; out-of-scope
aborts (no mutation), surfacing `force` (GUI) / the error (tool).

## Error handling

- Unknown target / malformed IP → validation error before any mutation (tool `is_error`; endpoint 400).
- `new_ip == old_ip` → no-op success.
- Out-of-scope → abort, nothing mutated (unless `force_scope`).
- KB remap transactional → mid-remap conflict rolls back; KB intact.
- `/etc/hosts` failure → non-fatal; address + KB changes stand; `hostname_updated=false` + manual line.
- No active session (GUI) → target updated, `session_refocused=false`; next session uses new IP.

## Testing strategy

- `KB.remap_address` (temp KB): simple rename; host conflict merge-then-delete; service
  `(host_ip,port,proto)` conflict skips dup; `cred_results` updated; note + counts.
- `refocus_target` (unit): add+promote; same-IP no-op; out-of-scope abort + `force_scope` override;
  reuse existing address; `RefocusResult` fields.
- `AgentSession.refocus_address` (unit): updates active_address/target/snapshot id, marker, save.
- `kb_refocus_target` (async): valid → summary; bad IP → `is_error`; refocuses `current_session`.
- `POST /api/targets/<name>/refocus` (httpx): refocus + active-session update; out-of-scope → 4xx + force hint.
- `/etc/hosts` helper (temp file): old→new rewrite; add-if-missing. Sudo path mocked.
- Frontend (vitest): refocus action in the session store + the control component.
- Full `pytest` + `vitest` green.

## Affected files

- New: `src/reverser/refocus.py`; tests `tests/test_refocus.py`, `tests/test_kb_remap.py`,
  `tests/gui_service/test_refocus_route.py`.
- Modify: `src/reverser/kb/store.py` (`remap_address`), `src/reverser/agent_session.py`
  (`refocus_address`), `src/reverser/tools/kb.py` (`kb_refocus_target`),
  `src/reverser/gui_service/routes/targets.py` (refocus endpoint),
  `src/reverser/gui_service/session_manager.py` (active-session lookup).
- Frontend: `desktop/renderer/src/` — refocus control in `TargetOverview`/`TargetsPanel`, session-store
  action + API client; component/store tests.
