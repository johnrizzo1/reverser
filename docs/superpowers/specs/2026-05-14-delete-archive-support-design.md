# Delete & Archive Support — Design Spec

**Date:** 2026-05-14
**Status:** Approved for planning
**Scope:** Two new operations on sessions and targets: **archive** (hide from default views, restorable) and **delete** (remove from disk). Closes a real gap — the desktop UI currently has no way to clean up old engagements.

## 1. Goals & non-goals

### Goals

- Let the operator hide finished engagements from the default Sessions/Targets panels without losing the data (Archive).
- Let the operator permanently remove a session or target from the UI and disk (Delete), with a safety net for targets (soft delete to `.trash/`).
- Refuse archive/delete on any session that is currently `active` (server returns 409; UI disables the buttons with a tooltip).
- Preserve existing snapshot/lifecycle semantics — `archived_at` is orthogonal to `state` (a stopped session can be "stopped + archived").

### Non-goals (this phase)

- UI for browsing / restoring items from `.trash/` — manual filesystem recovery only.
- Soft delete for sessions — sessions are small and easy to recreate; hard delete is fine.
- Bulk archive / select-multiple — single-item actions only.
- Configurable trash retention — 30 days hardcoded.
- Process-PID-based active check beyond `state === "active"` — trust the state field.

## 2. Architecture

Both archive and delete are operations on the existing per-target filesystem layout. Archive is reversible metadata; delete removes files.

### Delete semantics (per-type asymmetry)

- **Sessions** → hard delete. Unlink `targets/<t>/sessions/<id>.json` and the log at `snap.log_path`. Symmetric with "the session is gone".
- **Targets** → soft delete. Move `targets/<name>/` → `targets/.trash/<ISO-timestamp>-<name>/`. Recoverable via filesystem; auto-pruned after 30 days on the next `/api/targets` scan.

### Archive semantics (uniform)

- **Sessions** — new `archived_at: str | None` field on `SessionSnapshot`. Round-trips through the existing JSON save/load. Default views filter out anything with non-null `archived_at`.
- **Targets** — marker file `targets/<name>/.archived` containing an ISO timestamp. Cheap to check during the `/api/targets` directory scan.

### Active-session guard

All three destructive endpoints (archive session, hard delete session, archive target, soft delete target) refuse with 409 Conflict if the relevant session(s) have `state === "active"`. The frontend disables the buttons with a tooltip; the server is the second line.

## 3. Backend endpoint specs

Six new endpoints, two existing endpoints get a small shape extension.

### `POST /api/sessions/{id}/archive?target={t}`

Mutates the snapshot: sets `archived_at` to the current ISO-8601 UTC timestamp. Returns:
- 204 on success.
- 409 if the session's current `state === "active"`.
- 404 if the snapshot doesn't exist.

### `DELETE /api/sessions/{id}/archive?target={t}`

Unarchives. Clears `archived_at` back to `None`. Returns:
- 204 on success (idempotent — calling on a non-archived session is fine).
- 404 if the snapshot doesn't exist.

### `DELETE /api/sessions/{id}?target={t}`

Hard delete.
- 204 on success.
- 409 if `state === "active"`.
- 404 if the snapshot doesn't exist.
- Unlinks `targets/<t>/sessions/<id>.json`.
- Unlinks the log file at `snap.log_path` if it exists. Best-effort — if the log is missing or unreadable, log a warning to the service log via `logging.getLogger(__name__).warning(...)` and continue. The snapshot delete is the primary effect; 204 is returned either way.

### `POST /api/targets/{name}/archive`

Writes the marker file `targets/<name>/.archived` containing the current ISO-8601 UTC timestamp.
- 204 on success.
- 409 if any session for this target has `state === "active"`.
- 404 if the target directory doesn't exist.

### `DELETE /api/targets/{name}/archive`

Removes the `.archived` marker file. Idempotent.
- 204 on success.
- 404 if the target directory doesn't exist.

### `DELETE /api/targets/{name}`

Soft delete. Moves the directory to `targets/.trash/<ISO-timestamp>-<name>/`.
- 204 on success.
- 409 if any session for this target has `state === "active"`.
- 404 if the target directory doesn't exist.
- If `targets/.trash/` doesn't exist, create it (mode 0700).

### Existing endpoints — shape additions

**`GET /api/sessions`** — each row in the existing response gains an `archived_at: string | null` field. The frontend owns the filtering UI; no `?include_archived=` query param.

**`GET /api/targets`** — each row gains an `archived: boolean` field (true if the `.archived` marker exists). Frontend filters client-side.

**Trash prune** — `GET /api/targets` runs a lazy sweep before returning. Iterates `targets/.trash/`, parses the leading ISO timestamp from each entry name (`<ISO-timestamp>-<name>/`), and `rm -rf`'s entries older than 30 days. Synchronous; bounded by filesystem speed.

## 4. Frontend UX

Archive is the primary one-click action; Delete lives in an overflow `...` menu (per Q5 — archive replaces delete as the headline behavior).

### Row layout (default — not archived)

Right-aligned controls appear on hover (clean default rendering):

```
●  10.10.10.5  manager · 11:04 · $0.42         📦  ⋯
                                                ↑    ↑
                                                │    └─ overflow menu: "Delete permanently"
                                                └────── Archive (one click → confirm modal)
```

### Row layout (archived row)

Same row, action set swaps:

```
■  10.10.10.5  manager · 11:04 · $0.42 (archived 2026-05-13)         ↺  ⋯
                                                                       ↑    ↑
                                                                       │    └─ "Delete permanently"
                                                                       └────── Restore (one click, no modal)
```

Visual variant: a small "(archived <date>)" suffix and a slightly desaturated background mark archived rows. Status dot becomes a square (■) to distinguish at a glance.

### Confirmation modals

Two generic components: `ArchiveConfirmModal` (default variant) and `DeleteConfirmModal` (destructive variant, red Confirm button). Both accept `title`, `description`, and `onConfirm` props so the four flows (archive/delete × session/target) share two modal components.

Copy:

- **Archive session/target:**
  > "Archive this <thing>? This hides it from the default view. You can find it in the 'archived' filter and restore at any time."

- **Delete session:**
  > "Delete this session permanently? The snapshot and its log file will be removed from disk. This can't be undone."

- **Delete target:**
  > "Delete this target? The directory will be moved to `targets/.trash/` for 30 days. It won't appear in the UI. Recovery requires filesystem access; after 30 days the trash entry is pruned."

### Active-session guard (UI)

The Archive button and the overflow Delete item are disabled with a tooltip ("Stop the session first") when:

- A session row has `state === "active"`.
- A target row has at least one session with `state === "active"` (the existing `any_active` flag in `TargetsPanel` already tracks this).

### Filter UI

- **SessionsPanel** — the existing filter tab strip (`all / active / stopped / completed / abandoned`) gains a sixth tab: `archived`. Always visible, shows `(0)` when empty. Selecting it filters to rows where `archived_at !== null`.
- **TargetsPanel** — current sort toggle stays. A new "Show archived" checkbox appears next to it. Off by default; archived targets are hidden from the default view.

### Restore action

- For archived **sessions**: `↺` button (no modal — restore is harmless) calls `DELETE /api/sessions/{id}/archive`. Row returns to the default view.
- For archived **targets**: same `↺` button calls `DELETE /api/targets/{name}/archive`.

## 5. File layout

### Backend

```
src/reverser/sessions.py                          modify  (+ archived_at field on SessionSnapshot,
                                                          + delete(target, session_id) helper,
                                                          + set_archived(target, session_id, archived) helper)
src/reverser/gui_service/routes/sessions.py       modify  (+ archive POST, archive DELETE, hard DELETE,
                                                          + archived_at in SessionRow shape)
src/reverser/gui_service/routes/targets.py        modify  (+ archive POST, archive DELETE, soft DELETE,
                                                          + trash prune on /api/targets,
                                                          + archived bool in TargetRow shape)
tests/test_session_archive_delete.py              create  (~6 tests: archive set/clear, delete unlinks
                                                          snapshot+log, archived_at round-trips, helpers
                                                          are exception-safe)
tests/gui_service/test_session_archive_routes.py  create  (~5 tests: archive 204, unarchive 204, delete 204,
                                                          409 on active session, 404 on missing snapshot)
tests/gui_service/test_target_archive_routes.py   create  (~6 tests: archive marker file written,
                                                          unarchive removes marker, soft delete moves to
                                                          .trash/, 409 when any session active, trash prune
                                                          sweeps >30-day entries, /api/targets row shape
                                                          includes archived)
```

### Frontend

```
desktop/renderer/src/
  api/client.ts                                   modify  (+ archived_at on SessionRow,
                                                          + archived on TargetRow)
  api/queries.ts                                  modify  (+ useArchiveSession, useUnarchiveSession,
                                                          useDeleteSession, useArchiveTarget,
                                                          useUnarchiveTarget, useDeleteTarget)
  modals/
    ArchiveConfirmModal.tsx                       create  (generic confirm modal, default variant)
    DeleteConfirmModal.tsx                        create  (generic confirm modal, destructive variant)
  components/SessionRow.tsx                       modify  (+ Archive button + overflow menu on hover;
                                                          + archived visual variant)
  layout/SessionsPanel.tsx                        modify  (+ "archived" filter tab)
  layout/TargetsPanel.tsx                         modify  (+ Show archived toggle;
                                                          + per-row hover archive + delete actions;
                                                          + visual variant for archived rows)
  tests/e2e/delete-archive.spec.ts                create  (~4 structural Playwright tests)
```

## 6. Testing

### Backend (pytest)

**Session helpers** — `tests/test_session_archive_delete.py`:
- `archived_at` field defaults to `None` on new snapshots.
- `set_archived(target, id, True)` writes the snapshot with a timestamp.
- `set_archived(target, id, False)` clears the timestamp.
- `delete(target, id)` unlinks snapshot + log; idempotent on missing log.
- `delete(target, id)` raises if `state === "active"`.
- Snapshot serialization round-trips `archived_at` through save/load.

**Session routes** — `tests/gui_service/test_session_archive_routes.py`:
- POST archive returns 204; subsequent GET /api/sessions row has `archived_at` populated.
- DELETE archive returns 204; row's `archived_at` is null again.
- DELETE session returns 204; snapshot file gone; log file gone.
- POST archive on active session returns 409.
- DELETE session on active session returns 409.
- All endpoints return 404 on missing snapshot.

**Target routes** — `tests/gui_service/test_target_archive_routes.py`:
- POST archive writes `.archived` marker; GET /api/targets row shows `archived: true`.
- DELETE archive removes the marker; row shows `archived: false`.
- DELETE target moves directory to `.trash/<timestamp>-<name>/`; subsequent GET doesn't include it.
- DELETE target with active session in it returns 409.
- Trash prune: pre-seed a `.trash/` entry with a 31-day-old timestamp, hit GET /api/targets, confirm the entry is gone afterward.
- `/api/targets` row shape includes the `archived` bool.

### Frontend (Playwright e2e, structural)

`tests/e2e/delete-archive.spec.ts` — 4 tests mirroring Phase 3a/3b pattern:
- SessionsPanel renders the "archived" filter tab.
- TargetsPanel renders the "Show archived" toggle.
- Hover on a session row reveals the Archive button.
- Existing 13 e2e tests still pass (smoke regression).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Race: archive + new agent loop starts in same second. | The active-session check reads `state` from the snapshot on disk. The agent loop sets `state` to `"active"` before doing any work; the archive endpoint loads the current snapshot at request time and refuses with 409 if `state == "active"`. Stale in-memory state on the client is corrected on the next list refresh. |
| Trash prune deletes entries someone wanted to recover. | 30 days is long. Operators can `mv` items back. Documented in the delete modal copy. Phase 4 polish could surface a "Manage trash" page if needed. |
| Hard delete loses the log forever. | This is the documented behavior. If audit-trail retention matters, that's a backup-policy concern outside the app. |
| Archive marker file conflicts with KB state on disk. | The `.archived` file is a hidden marker (leading dot). The implementer must confirm the existing `/api/targets` directory scan skips dot-prefixed children (the `.trash/` dir relies on the same behavior). If the scan does not skip dotfiles, add an explicit exclusion for both `.archived` and `.trash/` as part of the trash-prune task. |
| Two archived sessions for the same target — KBTabbedView/SessionsPanel show or hide correctly? | The `archived_at` is per-session, not per-target. Filtering happens row by row. No interaction issue. |
| Delete-permanently in the overflow menu on a non-archived row could be misclicked. | The confirm modal is the safety net. Delete is still in the overflow precisely because it requires two clicks even for the lightest case. |
| Trash entry timestamp parsing fragile if user renames the entry. | Parse via a regex that matches the prefix; if parsing fails, skip the entry (it won't be pruned, but it also won't crash the GET). Documented. |

## 8. Out of scope (for this phase)

- UI for browsing / restoring from `.trash/` — manual filesystem only.
- Bulk archive / multi-select.
- Soft delete for sessions.
- Custom trash retention period.
- "Empty trash now" UI action — Phase 4+ if needed.
- Process-PID-based active session detection — trust the state field.

## 9. Open questions

None blocking.
