# Manager profile smoke test

A 30-minute end-to-end walkthrough of the `manager` profile against a real
HTB AD lab box. This is a human-in-loop checklist — run when validating a
release of the manager-profile work or after major changes to dispatch
infrastructure.

## Preconditions

- An HTB box with AD exposed (e.g. Forest, Sauna, Active, Cascade) is
  reachable from your test machine
- VPN connected; the box's IP is responsive to ping
- `devenv shell` is active; `nxc --version` works (NetExec installed
  via the devenv venv)
- Neo4j is available (the bloodhound stack will spin it up for sub-agent
  collection if dispatched)
- `REVERSER_PENTEST_AUTHORIZED=1` exported
- A scratch `targets/<ip>/` directory will be created automatically

Optionally place a `targets/<ip>/scope.toml` with `in_scope_cidrs = ["<the-box-ip>/32"]`
to confirm scope enforcement in dispatched specialists.

## Steps

### 1. Launch the manager session

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager <ip>
```

**Expected:** TUI loads with `Profile: Manager` in the header. Initial
system prompt panel mentions hypothesis-driven methodology and the 5
specialty options.

### 2. Trigger Kickoff

Press `k` (or type the kickoff prompt manually).

**Expected:**
- Manager calls `kb_show` (empty KB initially, returns "no facts").
- Manager calls `nmap_scan` with default options.
- Manager calls `kb_add_hypothesis` 3–5 times for likely attack paths
  (e.g. "DC has SMB signing disabled", "ASREP-roastable accounts present",
  "Anonymous LDAP enum possible").
- Manager picks one hypothesis and calls `dispatch_specialist(specialty='ad', ...)`.
- The "Specialist's report" section appears in the chat with TL;DR, Findings,
  and Hypothesis outcome.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT id, status, statement FROM hypotheses;"
```
Should show ~3–5 rows. The dispatched one is in `testing` status during
dispatch and `confirmed`/`refuted`/`inconclusive` after.

### 3. Verify hypothesis update lands in KB

After the dispatch returns, the manager should call `kb_update_hypothesis`
to record the outcome.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT id, status, dispatched_to, evidence_refs FROM hypotheses WHERE status != 'proposed';"
```
At least one row should show non-proposed status with `dispatched_to='ad'`
and possibly an `evidence_refs` JSON array.

### 4. Trigger Status

Press `s`.

**Expected:** Manager prints the hypothesis tree (using `kb_list_hypotheses
include_tree=True`), with status glyphs (✅ confirmed, ❌ refuted, 🔄 testing,
💭 proposed) and a recommended next action.

### 5. Test the interrupt path

While a dispatch is in flight (during step 2 or after triggering Kickoff
again), press `Ctrl+C` (or the TUI's interrupt key).

**Expected:** The dispatch aborts cleanly. The manager session is still
alive. The hypothesis being tested may stay in `testing` status — that's
OK; the manager can re-update it.

### 6. Trigger Pivot

Press `p`.

**Expected:** Manager re-reads the tree, abandons any hypotheses that are
no longer worth pursuing (with reason in `kb_update_hypothesis(status='abandoned')`),
and proposes new child hypotheses based on what we've learned.

### 7. Trigger Report

Press `r`.

**Expected:**
- Manager calls `kb_export_report`.
- Markdown report is written to `pentest_report_<ip>.md`.
- Report includes an `## Attack tree` section with nested-bullet hypothesis
  structure and status glyphs.
- Executive summary above the auto-generated body.

**Verify:**
```sh
head -50 pentest_report_<ip>.md
grep "## Attack tree" pentest_report_<ip>.md
```

### 8. Trigger Wrap up

Press `w`.

**Expected:** Manager marks all unresolved hypotheses as `abandoned` with
reasons, generates the final report, and prints a wrap-up message.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT status, COUNT(*) FROM hypotheses GROUP BY status;"
```
No rows should be in `proposed` or `testing` status.

## Success criteria

- All 8 steps complete without crashes
- Hypothesis tree persists across the session and is visible in the report
- At least one `dispatch_specialist` call succeeds end-to-end (real sub-agent
  ran, report parsed, KB updated)
- Manager never invokes a heavy offensive tool directly — only via dispatch
  (verify by scanning the session log for tool calls; only kb_*, dispatch_specialist,
  nmap_scan, dns_recon, whatweb_scan, nbtscan, bash should appear)
- Final report file exists and contains the `## Attack tree` section

## Cleanup

```sh
rm -rf targets/<ip>/
rm pentest_report_<ip>.md
```
