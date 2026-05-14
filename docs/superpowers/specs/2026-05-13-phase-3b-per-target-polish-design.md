# Phase 3b — Per-Target Polish — Design Spec

**Date:** 2026-05-13
**Status:** Approved for planning
**Scope:** Scope.toml editor, report preview (rendered Markdown), and screenshot evidence gallery. Second slice of Phase 3 per the [parent UI design](2026-05-13-electron-desktop-ui-design.md). Builds on the per-target page introduced in [Phase 2](2026-05-13-phase-2-sessions-targets-design.md).

## 1. Goals & non-goals

### Goals

- Edit `targets/<name>/scope.toml` from inside the app (5 fixed fields).
- Render the report Markdown that `kb_export_report` produces, in the app, against the current KB state (no stale snapshot).
- Give the operator a "Export to disk" button that writes `targets/<name>/report.md` — the same artifact the agent produces.
- View screenshot evidence attached to findings without leaving the app, via a shared lightbox accessible from both the live `FindingsPane` and `TargetOverview`'s findings tab.

### Non-goals (this phase)

- Schema migration for older `scope.toml` shapes — we write the current 5-field set.
- Live auto-refresh of the rendered report on KB changes — tab refetch only.
- Screenshot editing, annotation, cropping — view-only.
- Bulk report export across targets — Phase 4 if needed.
- Diff view between two report exports — out.
- Drag-drop scope.toml import — out.
- BloodHound graph view — Phase 3c.

## 2. Architecture

Phase 3b is a frontend-heavy phase with five new backend endpoints (six if you count the image-bytes endpoint separately from the screenshots list).

### Backend (5 endpoints, all under existing `routes/targets.py`)

```
GET  /api/targets/{name}/scope                          parsed Scope or defaults
PUT  /api/targets/{name}/scope                          validate + write scope.toml
GET  /api/targets/{name}/report                         render fresh Markdown
POST /api/targets/{name}/report                         render + write to disk
GET  /api/targets/{name}/findings/{fid}/screenshots     list screenshots for one finding
GET  /api/targets/{name}/findings/{fid}/screenshots/{n} serve image bytes
```

Validation lives server-side (Python). Scope.toml writing is manual string assembly (5 fixed fields — no new TOML-writer dep).

### Frontend

```
┌─ TargetOverview header ──────────────┐
│ 10.10.10.5         [Edit scope]      │ ← new button opens ScopeEditorModal
├──────────────────────────────────────┤
│ Summary card                         │
├────────────────┬─────────────────────┤
│ Sessions       │  ┌─ tabs ─────────┐ │
│ ● 11:04 ad     │  │ Findings       │ │ ← existing
│ ⏸ 09:15 mgr    │  │ Hypotheses     │ │
│ ✓ secret.exe   │  │ Hosts          │ │
│                │  │ Services       │ │
│                │  │ Creds          │ │
│                │  │ Report         │ │ ← new tab body = ReportTab
│                │  └────────────────┘ │
└────────────────┴─────────────────────┘
```

Three new components:

- **`ScopeEditorModal`** — triggered by an "Edit scope" button in the TargetOverview header.
- **`ReportTab`** — body for the new "Report" tab in `KBTabbedView`.
- **`ScreenshotLightboxModal`** — single shared component; opened by the `📷 N` badge on a `FindingRow`. Same modal regardless of caller.

Plus one small shared component pulled out of two existing files:

- **`FindingRow`** — currently inlined in both `FindingsPane.tsx` and `KBTabbedView.tsx`. Extracting to `components/FindingRow.tsx` lets both callers gain the `📷 N` badge + lightbox callback without duplicating code.

One new npm dep: `react-markdown` + `remark-gfm` for the report tab.

## 3. Backend endpoint specs

### `GET /api/targets/{name}/scope`

Parses `targets/<name>/scope.toml` if present. Returns the parsed shape; returns defaults (all empty / false) if the file is absent.

```jsonc
{
  "exists": true,                              // false if file doesn't exist
  "in_scope_cidrs": ["10.10.10.0/24"],
  "out_of_scope_ips": ["10.10.10.99"],
  "allowed_hours": "09:00-17:00 UTC",          // string or null
  "no_dos": true,
  "no_account_lockout": true
}
```

A missing file is not an error — many targets don't have a scope envelope.

### `PUT /api/targets/{name}/scope`

Body matches GET (minus `exists`). Server-side validation:

- Each `in_scope_cidrs` entry must parse via `ipaddress.ip_network(strict=False)`.
- Each `out_of_scope_ips` entry must parse via `ipaddress.ip_address`.
- `allowed_hours` is a freeform string; the agent reads it as text (no time-window parser at server side).
- `no_dos` and `no_account_lockout` are booleans.

On invalid input, returns 400 with a per-field error map:

```jsonc
{ "errors": {
  "in_scope_cidrs[1]": "invalid CIDR: '10.10.10.999/24'",
  "out_of_scope_ips[0]": "invalid IP: 'not-an-ip'"
}}
```

On success, writes `targets/<name>/scope.toml` and returns 204. Always overwrites the entire file with the 5 fields. If a CIDR or IP list is empty, the field is still emitted (so the agent sees an explicit "no constraints" override rather than treating it as missing).

The KB layer's `load_scope(target)` reads scope on every offensive tool call, so changes are live immediately.

### `GET /api/targets/{name}/report`

Calls `_render_report(for_target(name))` — the same renderer `kb_export_report` uses. Returns:

```jsonc
{
  "target": "10.10.10.5",
  "markdown": "# Engagement report for 10.10.10.5\n\n## Summary\n…",
  "generated_at": "2026-05-13T14:33:21Z",
  "bytes": 4823
}
```

Always renders fresh from the current KB state. Never reads `report.md` from disk.

### `POST /api/targets/{name}/report`

Snapshot-to-disk. Renders fresh, writes `targets/<name>/report.md`, returns:

```jsonc
{
  "target": "10.10.10.5",
  "path": "/Users/jrizzo/.../targets/10.10.10.5/report.md",
  "bytes": 4823
}
```

The response body is a courtesy so the UI can show "Saved to .../report.md" feedback.

### `GET /api/targets/{name}/findings/{finding_id}/screenshots`

Scans `targets/<name>/findings/<finding_id>/` for files matching `screenshot-*.png`:

```jsonc
{
  "finding_id": "f-42",
  "screenshots": [
    { "index": 1, "size_bytes": 84321, "captured_at": "2026-05-12T22:14Z" },
    { "index": 2, "size_bytes": 91204, "captured_at": "2026-05-12T22:16Z" }
  ]
}
```

`captured_at` comes from file mtime (the agent doesn't stamp the file directly; mtime is close enough for an evidence gallery). 404 if the finding directory doesn't exist; empty list (200) if the directory exists but contains no screenshots.

### `GET /api/targets/{name}/findings/{finding_id}/screenshots/{n}`

Serves the PNG bytes for screenshot index `n` (1-indexed). `Content-Type: image/png`. 404 if not found.

All endpoints sit behind the existing bearer-token dependency.

## 4. Frontend components

### `ScopeEditorModal`

Triggered by an "Edit scope" button in the TargetOverview header. Form:

```
in_scope_cidrs:      textarea, one CIDR per line
out_of_scope_ips:    textarea, one IP per line
allowed_hours:       text input
no_dos:              checkbox
no_account_lockout:  checkbox
```

Textareas chosen over per-item add/remove rows because (a) data is small (rarely >10 entries), (b) copy/paste from a scope document is the common workflow, (c) less code.

State management: `useState` for the form draft. No global store (modal owns the draft until save). On Save: split textareas on `\n`, drop empty lines, send to PUT. On 400: surface server's per-field errors (e.g., `in_scope_cidrs[1]: invalid CIDR: '…'`) as inline messages above the offending textarea, with the bad entry highlighted.

### `ReportTab`

Body for the new "Report" tab in `KBTabbedView`. Layout:

```
┌──────────────────────────────────────────────────────────┐
│  # Engagement report for 10.10.10.5                      │
│                                                          │
│  (rendered Markdown — react-markdown + remark-gfm        │
│   for tables. Scrollable.)                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
  Generated 2026-05-13 14:33  •  4.8 KB     [Export to disk]
```

- Renders Markdown from `useReport(target)`.
- "Export to disk" button calls `useExportReport().mutate()` → POST → toast "Saved to .../report.md" with the absolute path returned from the server.
- Empty state for targets with no KB content: "no report content yet".

### `ScreenshotLightboxModal`

Shared component used from both `FindingsPane` (live session, right rail) and `KBTabbedView` (TargetOverview, Findings tab).

Props:

```tsx
{
  target: string;
  findingId: string;
  startIndex?: number;          // default 1
  open: boolean;
  onOpenChange: (open: boolean) => void;
}
```

On open: fetches `/findings/{id}/screenshots` for the list, displays current image at `/findings/{id}/screenshots/{n}` via a regular `<img>` tag (CSP already allows `127.0.0.1` for `img-src`).

Controls: prev/next arrows, "1 of 3" counter, ESC closes, click outside closes.

### `FindingRow` (shared)

Currently inlined in `FindingsPane.tsx` and `KBTabbedView.tsx` (Findings tab). Extract to `components/FindingRow.tsx`:

```tsx
{
  finding: FindingRow;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}
```

Renders severity dot, title, description (line-clamped). Adds a `📷 N` badge when the finding has ≥1 screenshot. The badge fetches the screenshot list via TanStack Query (cached with `staleTime: 60_000`). Clicking the badge calls `onClickEvidence(findingId, 1)`.

Both callers wire the same callback to open `ScreenshotLightboxModal` with the right finding id.

### Performance note

Each `FindingRow` makes one `/screenshots` call. For pages with many findings (>20), this is N HTTP calls per render. TanStack Query dedupes by query key, so subsequent renders are free. For a future-proof solution, a batch endpoint like `/api/targets/{t}/screenshots-summary` returning a map of finding_id → screenshot_count would be a Phase 4 optimization. Acceptable here because typical engagements have <20 findings.

## 5. File layout

### Backend

```
src/reverser/gui_service/routes/targets.py        modify  (5 new endpoints)
tests/gui_service/
  test_scope_routes.py                            create  (~6 tests)
  test_report_routes.py                           create  (~3 tests)
  test_screenshot_routes.py                       create  (~4 tests)
```

No new Python dep.

### Frontend

```
desktop/package.json                              modify  (+ react-markdown, + remark-gfm)
desktop/renderer/src/
  api/client.ts                                   modify  (+ Scope, ReportResponse,
                                                          ScreenshotsResponse types)
  api/queries.ts                                  modify  (+ useScope, useUpdateScope,
                                                          useReport, useExportReport,
                                                          useScreenshots)
  components/
    FindingRow.tsx                                create  (shared row)
  modals/
    ScopeEditorModal.tsx                          create
    ScreenshotLightboxModal.tsx                   create
  panes/
    ReportTab.tsx                                 create
    FindingsPane.tsx                              modify  (use shared FindingRow,
                                                          wire lightbox callback)
  components/KBTabbedView.tsx                     modify  (use shared FindingRow in
                                                          Findings tab; add Report tab;
                                                          wire lightbox callback)
  pages/TargetOverview.tsx                        modify  ("Edit scope" button in header;
                                                          ScopeEditorModal mounted)
desktop/tests/e2e/
  phase3b.spec.ts                                 create  (~4 Playwright tests)
```

## 6. Testing

### Backend (pytest)

**`test_scope_routes.py`** — 6 tests:
- GET when no `scope.toml` exists → defaults, `exists: false`.
- GET when `scope.toml` exists → parsed values, `exists: true`.
- PUT valid → 204, file content matches.
- PUT with invalid CIDR → 400 with field error.
- PUT with invalid IP in `out_of_scope_ips` → 400 with field error.
- PUT with all empty arrays + `no_dos: false`, `no_account_lockout: false` → file is still written with explicit empty values.

**`test_report_routes.py`** — 3 tests:
- GET on a target with hosts/findings → returns Markdown containing "Engagement report" header and expected sections.
- POST → writes `targets/<t>/report.md`; subsequent file read returns the same content as the GET would.
- GET on a target with no KB content → returns Markdown (empty sections), not an error.

**`test_screenshot_routes.py`** — 4 tests:
- list-screenshots for a finding with 2 PNG files → returns 2 entries with correct indices and `size_bytes`.
- list-screenshots for a finding directory that doesn't exist → 404.
- image-bytes returns `Content-Type: image/png` and the file content.
- image-bytes-404 when index out of range.

### Frontend (Playwright e2e, structural)

`tests/e2e/phase3b.spec.ts` — 4 tests, mirroring Phase 3a's structural-test pattern:

- ScopeEditorModal opens from the TargetOverview header and renders all 5 fields.
- Save with `not-a-cidr` in `in_scope_cidrs` surfaces an inline error.
- ReportTab is reachable from KBTabbedView's tabs and renders some Markdown.
- A finding row shows the `📷 N` badge when the test fixture writes a screenshot file.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Manual TOML writer for scope.toml could drift from the canonical format. | The Scope dataclass has exactly 5 fields and they're all scalars or string arrays. Manual `toml.dumps`-style assembly is straightforward. A round-trip test (write → read via `Scope.load`) guards this. |
| Per-`FindingRow` screenshot count fetch is N+1. | TanStack Query dedupes; typical engagement has <20 findings. Phase 4 can add a batch endpoint if profiling shows it. |
| Stale `targets/<t>/report.md` after agent edit conflicts with the operator's view. | GET always renders fresh from KB; the on-disk `report.md` exists only as the snapshot from the last manual export. Operator never sees stale data; the on-disk file is a deliberate snapshot artifact. |
| Image-bytes endpoint serves user-controlled file content. Could be exploited by writing arbitrary PNG content to a target's findings dir. | The service is loopback-only and bearer-token-gated, and the renderer is sandboxed. Path traversal is prevented by joining `targets/<name>/findings/<finding_id>/screenshot-<n>.png` with strict regex on `n` (digits only). Verify in the implementation. |
| `react-markdown` is a meaningful new dep. | Mature library, well-maintained. Same security posture as everything else in the renderer. Tailwind typography handles styling. |
| `KBTabbedView` and `FindingsPane` currently render finding rows inline (slight duplication today). The shared `FindingRow` refactor is the right cleanup. | Doing it now (Phase 3b) is the right time — adding the `📷 N` badge would otherwise duplicate the change. |

## 8. Out of scope (for 3b)

- BloodHound graph view — Phase 3c.
- Live report auto-refresh on KB changes — Phase 4 if needed.
- Screenshot editing/annotation — out.
- Bulk export across targets — Phase 4.
- Diff between two report exports — out.
- Drag-drop scope.toml import — out.
- Batch `/screenshots-summary` endpoint to replace N+1 fetches — Phase 4 if profiling justifies.

## 9. Open questions

None blocking.
