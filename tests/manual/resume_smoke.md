# Stop & resume smoke test

A 20-minute walkthrough of the stop/resume feature against any reverser
profile. Use the manager profile for the most complete coverage; otherwise
`general` or `pentest` for a simpler test.

## Preconditions

- `devenv shell` is active
- `REVERSER_PENTEST_AUTHORIZED=1` exported (if using a pentest profile)
- A scratch target — any IP or hostname; doesn't need to be reachable
  for the lifecycle tests

## Steps

### 1. Start a session and verify snapshot creation

```sh
reverser i -p general 10.10.10.5
```

Send a couple of messages. After each turn, in another terminal:

```sh
ls targets/10.10.10.5/sessions/
jq '.session_id, .state, .stats, .conversation | length' targets/10.10.10.5/sessions/*.json
```

**Expected:** A `<session_id>.json` file exists. The `state` is `active`,
`pid` matches the running TUI process, `stats.turns` increments per turn,
`conversation` array grows per exchange.

### 2. Stop via F6 / /stop + verify state transition

In the TUI, press `F6` (or type `/stop`). Confirm "Yes" in the modal.

**Expected:** TUI exits cleanly with a "session stopped and snapshot saved"
message. The snapshot file now shows `state: "stopped"`,
`stopped_at: "<timestamp>"`, `pid: null`.

### 3. Resume + verify chat replay

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** TUI starts with a "[Resumed session ...]" line in the chat
pane. The chat pane re-renders all prior exchanges. Send a new message.

In another terminal, verify the snapshot updated:

```sh
jq '.state, .stats' targets/10.10.10.5/sessions/*.json
```

**Expected:** `state` is `active` again, `stats.turns` continues from
where you left off (not reset).

### 4. Test /status slash command

In the TUI, type `/status`. **Expected:** prints session metadata
including session_id, state, started timestamp, cost, turns.

### 5. Test /help

Type `/help`. **Expected:** lists slash commands including `/stop`,
`/done`, and `/status`. F6 should appear in the keybinding hint.

### 6. Crash simulation

Start fresh:

```sh
rm -rf targets/10.10.10.5/sessions/
reverser i -p general 10.10.10.5
```

Send a message. While the TUI is running, find the PID and kill it:

```sh
ps | grep reverser
kill -9 <pid>
```

**Expected:** TUI dies. Snapshot file still exists; `state` is still
`active`, `pid` is set to the now-dead PID.

Resume:

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Liveness check sees the dead PID, treats the session as
resumable, flips state back to `active` with the new PID. Chat pane
re-renders the partial conversation. New messages continue building on it.

### 7. List sessions

```sh
reverser --list-sessions
```

**Expected:** Table listing the session(s) you've created. Stopped sessions
are shown alongside active ones.

### 8. Mark completed via /done

In a running TUI, type `/done`. Confirm "Yes".

**Expected:** TUI exits cleanly. Snapshot now `state: "completed"`.

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Errors with "Session ... is completed and cannot be resumed."

```sh
reverser --list-sessions
```

**Expected:** The completed session still appears (we don't hide completed
by default).

### 9. Force-take-over (optional)

Start a session:

```sh
reverser i -p general 10.10.10.5
```

In another terminal, while the first is still running:

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Errors with "Session ... is currently running in PID X."

```sh
reverser i 10.10.10.5 --resume --force
```

**Expected:** Takes over with a warning that the original process's writes
will conflict.

### 10. Manager-profile in_flight tracking (optional, requires manager work)

If the manager profile is available, start a manager session against an
HTB AD lab and dispatch a specialist. While the dispatch is in flight,
press `F6` to stop. The snapshot should show `in_flight` populated:

```sh
jq '.in_flight' targets/<ip>/sessions/*.json
```

**Expected:** Object with `kind: "dispatch"`, `specialty`, `hypothesis_id`,
`sub_goal`, `started_at`. After stop, in_flight is cleared (the dispatch
unwinds and the finally block runs).

On resume, the chat pane shows a yellow ⚠ note about the abandoned
dispatch.

## Success criteria

- All 9 (or 10) steps complete without unexpected errors
- Snapshot files persist correctly across stop/resume/crash cycles
- `--list-sessions` shows accurate state
- `/done` is terminal — completed sessions are not resumable
- `--force` correctly overrides liveness check (with warning)

## Cleanup

```sh
rm -rf targets/10.10.10.5/
rm -rf logs/10.10.10.5_*
```
