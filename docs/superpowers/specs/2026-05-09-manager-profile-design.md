# Manager profile design — coordinating specialist sub-agents

**Date:** 2026-05-09
**Status:** Design approved; ready for implementation plan
**Predecessor:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` (AD capability pack)

---

## 1. Goal

Add a `manager` profile to the reverser harness. The manager is an expert at planning network/red-team engagements: it identifies attack patterns, maintains a hypothesis tree, and coordinates specialist sub-agents (running existing profiles like `ad`, `pentest`, `webpentest`) so each can exercise their specialty while the manager drives overall direction.

The manager itself does not perform offensive work beyond cheap recon. Anything heavy must go through dispatch.

## 2. Non-goals (v1)

- Coordinating binary-RE specialties (`linux`, `windows`, `android`, `chrome`, `managed`, `ctf`) — those serve a different workflow (analyzing one artifact) where a coordinator adds little value.
- Cross-target managers (one manager spanning multiple targets simultaneously) — single-target only.
- Auto-resumption of timed-out specialists — the manager decides whether to re-dispatch.
- Recursive managers (a manager spawning another manager) — not blocked technically; just untested.
- Persisting the manager's own conversation to the KB — `session_log` already captures it for audit.
- Approval-gated dispatches (per-dispatch confirmation prompts) — autopilot-with-interrupt only in v1.
- Phase-level budget envelopes — per-dispatch caps with a global pool only in v1.

## 3. Architectural decisions (with rationale)

| # | Decision | Rationale |
|---|---|---|
| D1 | Sub-agents run via the Claude Agent SDK's native `Task` primitive (real isolated context, own budget). | The SDK's intended pattern for hierarchical agents. Gives true context isolation, which matters for token budget on long engagements. Avoids reinventing what the SDK already provides. |
| D2 | Dispatchable specialty pool: `pentest`, `ad`, `webpentest`, `webapi`, `webrecon` (5 specialties). | Matches the natural arc of a network red-team engagement (recon → foothold → AD pivot). Excludes RE specialties as out of domain. |
| D3 | Manager's own toolkit = planner + lightweight recon (KB read/write, hypothesis tools, dispatch, nmap_scan/dns_recon/whatweb_scan/nbtscan, `bash` for whois/etc). Heavy/loud tools require dispatch. | Real lead operators do cheap glue work themselves and delegate the deep specialty work. Pure-planner (no offensive tools at all) adds friction; full-toolkit relies on prompt discipline that LLMs reliably violate. |
| D4 | Specialist return contract = markdown report + KB writes (hybrid). | Manager gets immediate signal (the report) for live planning; KB persists for cross-session/cross-dispatch state and audit. Cheap to implement: the SDK's Task tool already returns the sub-agent's final assistant message as a string. |
| D5 | Planning state = structured `hypotheses` table in KB with parent/child links. | The user's stated goal ("identifying attack patterns") most directly maps to a tracked hypothesis tree. Schema is small (one new table, four CRUD tools). Renders post-engagement as a real attack-tree artifact. |
| D6 | Concurrency: sequential default, `--max-parallel N` opt-in flag (and a `parallel: bool` per dispatch). | Sequential is the right default for engagements touching real infrastructure (one rate-limit trip ruins a session). Parallelism remains available for safe activities like external recon across distinct subnets. |
| D7 | Autonomy: autopilot with interrupt. No per-dispatch approval. Hard brakes are the global budget cap and TUI/SIGINT interrupt. | Approval-each (B) creates so much friction operators rubber-stamp without reading. Tiered approval (D) is appealing but the manager misclassifies edge cases. Budget + interrupt are honest brakes. |
| D8 | Budget allocation: defaulted per-dispatch caps (`$0.50` / `15` turns), overridable, drawing from the engagement's global pool. | Defaults catch runaway-loop dispatches automatically. Override path lets the manager invest more in known-deep work (e.g. a full SharpHound collection). Global pool stays the hard cap. |
| D9 | Profile module: split monolithic `src/reverser/profiles.py` (1047 lines, 13 profiles) into a package — one file per profile plus shared `_skills.py`. Done as task 0 of this work. | Adding a 14th profile to a 1047-line file is the natural moment to pay this debt. Keeps the manager profile clean and isolates per-profile review. |

## 4. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│ reverser i -p manager 10.10.10.5                                 │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ Manager (top-level claude_agent_sdk.query loop)          │   │
│   │                                                          │   │
│   │  Tools (17):                                             │   │
│   │   • KB read tools (6): kb_show, kb_list_{creds,hosts,    │   │
│   │     services,hypotheses}, kb_export_report               │   │
│   │   • Hypothesis tools (4 NEW): kb_*_hypothesis            │   │
│   │   • KB writes (2): kb_add_note, kb_add_finding           │   │
│   │   • Light recon (4): nmap_scan, dns_recon, whatweb_scan, │   │
│   │     nbtscan                                              │   │
│   │   • dispatch_specialist (1 NEW)                          │   │
│   │   • bash (1) — for whois/whoami/date/etc                 │   │
│   │                                                          │   │
│   │  ↓ dispatch_specialist(specialty="ad", ...)             │   │
│   │                                                          │   │
│   │  ┌────────────────────────────────────────────────────┐  │   │
│   │  │ Sub-agent (SDK Task)                              │  │   │
│   │  │  • own context window                             │  │   │
│   │  │  • own budget ($0.50 / 15 turns default)         │  │   │
│   │  │  • full tool surface (all tools except            │  │   │
│   │  │    dispatch_specialist — no recursive dispatch)   │  │   │
│   │  │  • specialty system_addendum + dispatch context   │  │   │
│   │  │  → returns markdown report                        │  │   │
│   │  └────────────────────────────────────────────────────┘  │   │
│   │           ↓ writes to                                    │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ Per-target KB: targets/<target>/state.db                 │   │
│   │  • hosts, services, credentials, findings, notes        │   │
│   │  • NEW: hypotheses (id, parent, statement, status,      │   │
│   │    confidence, evidence_refs, dispatched_to, ...)       │   │
│   │  • scope.toml (in-scope CIDRs, no_dos, allowed_hours)   │   │
│   └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

## 5. Hypothesis schema

```sql
CREATE TABLE hypotheses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
    statement       TEXT    NOT NULL,
    rationale       TEXT,
    status          TEXT    NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','testing','confirmed','refuted','abandoned','blocked')),
    confidence      INTEGER CHECK (confidence BETWEEN 0 AND 100),
    dispatched_to   TEXT,             -- specialty key when status='testing'
    dispatch_count  INTEGER DEFAULT 0,
    evidence_refs   TEXT,             -- JSON array: [{"kind":"finding","id":42}, ...]
    tags            TEXT,             -- JSON array of free-form tags
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_hypotheses_status ON hypotheses(status);
CREATE INDEX idx_hypotheses_parent ON hypotheses(parent_id);
```

**Status semantics:**

- `proposed` — written down but not tested
- `testing` — currently dispatched (`dispatched_to` set)
- `confirmed` — evidence supports it
- `refuted` — evidence against it
- `blocked` — can't test now (e.g. need creds we don't have)
- `abandoned` — manager dropped it as not worth pursuing

**Migration:** SCHEMA_VERSION bumps; one-shot migration creates the table on existing target DBs at first open. No data migration; old DBs get an empty table.

## 6. New tools

### 6.1 Hypothesis CRUD (added to `src/reverser/tools/kb.py`)

```
kb_add_hypothesis(target, statement, *, parent_id=None, rationale=None,
                  confidence=None, tags=None) -> {id, statement, status, parent_id}

kb_update_hypothesis(target, id, *, status=None, rationale=None,
                     confidence=None, dispatched_to=None,
                     evidence_refs=None, tags=None) -> {updated_fields}

kb_list_hypotheses(target, *, status=None, parent_id=None,
                   include_tree=False) -> [hypothesis...]
   # include_tree=True returns hierarchical structure

kb_get_hypothesis(target, id) -> {full record + computed children list}
```

All four go through the existing scope authz layer (`kb.authz`).

### 6.2 `dispatch_specialist` (new module `src/reverser/tools/dispatch.py`)

```
dispatch_specialist(
    specialty: Literal["pentest","ad","webpentest","webapi","webrecon"],
    sub_goal: str,                    # natural-language objective
    target: str,                      # defaults to engagement target
    *,
    target_subset: list[str] | None = None,
    hypothesis_id: int | None = None,
    rationale: str | None = None,
    budget_usd: float = 0.50,
    max_turns: int = 15,
    parallel: bool = False,
    extra_context: str | None = None,
) -> {
    status: "completed" | "budget_exhausted" | "turn_limit" | "error",
    report: str,                      # markdown report from specialist
    cost_usd: float,
    turns: int,
    hypothesis_outcome: "confirmed" | "refuted" | "inconclusive" | None,
    kb_writes: list[str],             # short summary of what got written
    error: str | None,
}
```

**Wrapper behavior:**

1. Loads `get_profile(specialty).system_addendum`.
2. Prepends a **dispatch context block** (see §6.3).
3. Spawns SDK sub-agent with that combined system prompt, the specialty's full tool surface (all tools *except* `dispatch_specialist` — recursive dispatch is blocked structurally, not just by prompt), and the specified caps.
4. Awaits completion, captures `cost_usd` and `turns` from the SDK result.
5. Parses `### Hypothesis outcome` from the returned markdown — graceful if malformed (defaults to `inconclusive` and includes a warning in the surfaced report).
6. Returns the structured envelope above.

### 6.3 Dispatch context block (prepended to the specialist's system prompt)

```
# Dispatch context (read first)

You are operating as a sub-agent of the engagement lead.

- Engagement target: <target>
- Sub-goal: <sub_goal>
- Target subset: <list or "entire target scope">
- Hypothesis under test (id=<id>): <statement>
- Rationale from lead: <rationale>
- Extra context: <extra_context>

## Scope envelope (do not exceed)
<scope.toml summary if present>

## Per-dispatch budget
- Max turns: <max_turns>
- Cost cap: $<budget_usd>

## Return contract
When you finish, your final assistant message MUST be a markdown report
with these sections:

### TL;DR
One sentence.

### Findings
What you discovered. Bullet list.

### Hypothesis outcome
One of: CONFIRMED, REFUTED, INCONCLUSIVE — followed by one-sentence justification.

### KB writes
Short list of what you persisted (creds added, findings added, hypotheses
spawned). The lead reads this to know what changed.

### Suggested follow-up
What you would test next if you had more budget. The lead decides whether
to act on it.
```

Scope is enforced both via this prompt block and via the existing per-tool scope checks specialists already make (`kb.scope.load_scope`).

## 7. Manager system prompt (high-level shape)

The full text lives in `src/reverser/profiles/manager.py`. Sections:

1. **Role** — "You are the lead operator coordinating an assumed-breach / network red-team engagement. You direct specialists who have deep expertise in their domain. You do not perform offensive actions yourself except for cheap recon — you delegate."

2. **Hypothesis-driven methodology** — Every dispatch must be tied to a hypothesis. Workflow: read KB → propose hypotheses → dispatch to test → update on return → spawn child hypotheses or pivot. The hypothesis tree IS the engagement plan.

3. **Specialist menu** — One-paragraph description of each specialty:
   - `webrecon` — perimeter footprinting, subdomain/path enumeration, tech fingerprinting
   - `webpentest` — web app exploitation (sqli/xss/auth bypass) on known endpoints
   - `webapi` — API enumeration and abuse (REST/GraphQL/auth flow flaws)
   - `pentest` — general network enumeration, service exploitation, post-exploit pivoting
   - `ad` — Active Directory: assumed-breach methodology, kerberos abuse, BloodHound, lateral movement

4. **Dispatch checklist** — Before calling `dispatch_specialist`, confirm in a thinking block: hypothesis_id is set, sub_goal is one sentence and falsifiable, target_subset is bounded, budget is sized appropriately. Default to small budgets and re-dispatch rather than over-budgeting upfront.

5. **Reading the return** — When a specialist returns: (a) update the hypothesis (`kb_update_hypothesis`), (b) write a short `kb_add_note(kind="decision", body="...")` capturing what to do next and why, (c) choose next action.

6. **Termination criteria** — Manager declares the engagement complete (final report via `kb_export_report` and a wrap-up message) when one of:
   - All proposed hypotheses are resolved (confirmed/refuted/abandoned/blocked) AND no new hypotheses worth pursuing
   - User says wrap up
   - Budget effectively exhausted (>80% of global pool)

7. **Scope and safety** — Reiterate scope.toml respect; remind that `no_dos`, `no_account_lockout`, and `allowed_hours` constraints get inherited by every dispatched specialist (the wrapper passes the scope envelope into the dispatch context block).

## 8. Manager skills (TUI shortcuts)

| Key | Name | Prompt |
|---|---|---|
| `k` | Kickoff | Read the KB, propose 3–5 root hypotheses with rationale, pick the one with highest expected value, dispatch the appropriate specialist. |
| `s` | Status | Print the current hypothesis tree, recent dispatches, remaining budget, and your recommended next action. |
| `r` | Report | Generate the engagement report via `kb_export_report` and a written executive summary. |
| `p` | Pivot | Reassess the hypothesis tree given recent findings; abandon hypotheses no longer worth pursuing; propose new ones based on what we've learned. |
| `b` | Budget | Show current spend vs cap (global + per-dispatch defaults). Then ask the user for a new global cap or per-dispatch default; raise it to that value and confirm. |
| `w` | Wrap up | Mark all unresolved hypotheses as 'blocked' or 'abandoned' with reason, generate report, and stop. |

## 9. Tool surface enforcement

Add to `Profile` dataclass:

```python
@dataclass
class Profile:
    name: str
    key: str
    description: str
    system_addendum: str
    skills: list[Skill] = field(default_factory=list)
    tools_allowlist: list[str] | None = None  # NEW; None = all tools
```

`tui/session.py` reads `profile.tools_allowlist` and, when non-None, plumbs it through to `ClaudeAgentOptions(allowed_tools=...)` instead of the current wildcard `mcp__re__*`. All existing profiles default to `None`, preserving current behavior.

Manager's allowlist (17 tools — using actual registered names):

```python
MANAGER_TOOLS = [
    # KB read intelligence (6)
    "mcp__re__kb_show",
    "mcp__re__kb_list_creds",
    "mcp__re__kb_list_hosts",
    "mcp__re__kb_list_services",
    "mcp__re__kb_list_hypotheses",
    "mcp__re__kb_export_report",
    # Hypothesis CRUD (3 — list is in the read group above)
    "mcp__re__kb_add_hypothesis",
    "mcp__re__kb_update_hypothesis",
    "mcp__re__kb_get_hypothesis",
    # KB writes (2)
    "mcp__re__kb_add_note",
    "mcp__re__kb_add_finding",   # manager records findings from its own recon
    # Light recon (4)
    "mcp__re__nmap_scan",         # default: top-1000 ports, no scripts; manager can pass options to extend
    "mcp__re__dns_recon",
    "mcp__re__whatweb_scan",
    "mcp__re__nbtscan",
    # Coordination (1)
    "mcp__re__dispatch_specialist",
    # Bash (1)
    "mcp__re__bash",              # for whois, whoami, date, ad-hoc shell
]
```

**Note on read paths for findings/notes:** The manager reads findings and notes via `kb_show` (which returns the full target snapshot) rather than dedicated `kb_list_findings` / `kb_list_notes` tools — those don't exist today, and adding them is out of scope for this work. If the manager wants to filter or sort, it does so over the `kb_show` output. Whois is invoked via `bash whois <domain>` rather than a dedicated tool.

## 10. Report extension (attack tree)

`kb_export_report` gets a new section: **"Attack tree."** Renders the hypothesis tree as nested markdown bullets with status emoji:

```markdown
## Attack tree

- ✅ **DC has SMB signing disabled** (confirmed, 95%) — evidence: finding #12
  - ✅ **NTLM relay viable from compromised host to DC** (confirmed, 80%) — evidence: finding #18
    - 🔄 **Coerce DC auth via PetitPotam** (testing — dispatched to `ad`)
- ❌ **Anonymous LDAP enumeration possible** (refuted)
- 💭 **MS-SQL on 10.10.10.6 has weak sa password** (proposed, 30%)
```

Implementation: `kb.store.hypothesis_tree(target)` returns a nested list; the report renderer walks it and emits markdown.

## 11. File change set

### Add

| Path | Purpose |
|---|---|
| `src/reverser/tools/dispatch.py` | `dispatch_specialist` tool + report parser + dispatch context composer |
| `src/reverser/profiles/__init__.py` | Package init re-exporting `Profile`, `Skill`, `PROFILES`, `get_profile`, `list_profiles` |
| `src/reverser/profiles/_skills.py` | Shared skill constants extracted from monolithic `profiles.py` |
| `src/reverser/profiles/<each profile>.py` | One file per profile (general, linux, windows, android, chrome, managed, api, pentest, webpentest, webapi, webrecon, ad, ctf, manager) |
| `src/reverser/profiles/manager.py` | The new manager profile |
| `tests/test_dispatch.py` | Dispatch wrapper unit tests (mock SDK) |
| `tests/test_kb_hypotheses.py` | CRUD + tree rendering for the new table |
| `tests/test_profiles_manager.py` | Profile registration + skills + tools_allowlist |
| `tests/test_dispatch_e2e.py` | End-to-end with mocked SDK simulating multi-dispatch sequences |
| `tests/manual/manager_smoke.md` | 30-minute HTB AD lab walkthrough |

### Modify

| Path | Change |
|---|---|
| `src/reverser/profiles/__init__.py` (post-split) | Add `tools_allowlist: list[str] \| None = None` to `Profile` dataclass; import the `manager` module so its profile registers |
| `src/reverser/kb/schema.py` | Add `hypotheses` table + indexes; bump `SCHEMA_VERSION`; add migration |
| `src/reverser/kb/store.py` | `add_hypothesis`/`update_hypothesis`/`get_hypothesis`/`list_hypotheses`/`hypothesis_tree`/`resolve_evidence_refs` helpers |
| `src/reverser/tools/kb.py` | 4 new `kb_*_hypothesis` tools; extend `kb_export_report` to render the attack tree section |
| `src/reverser/tools/__init__.py` | Register `dispatch_specialist` + 4 new hypothesis tools. `ALL_TOOLS` grows from 63 → 68 entries (note: 2 of the existing 63 are accidental duplicate registrations of `nmap_scan` and `nikto_scan` — out of scope to fix here, but worth tracking) |
| `src/reverser/tui/session.py` | Plumb `profile.tools_allowlist` to `ClaudeAgentOptions.allowed_tools`; add `--max-parallel` plumbing |
| `src/reverser/cli.py` | `--max-parallel N` argument (default 1); update `--profile` help text and `--list-profiles` output |
| `src/reverser/backends/claude.py` | Honor profile-scoped `allowed_tools` (currently uses `mcp__re__*` wildcard) |
| `README.md` | Add `manager` profile row to the table; "Manager-led engagements" usage section |

### Does not change

- `kb.authz` layer
- `kb.scope` machinery
- The existing 13 profiles' system_addendum content (only their *file location* moves with the package split)
- `dotenv`, `devenv.nix`, `incus/profile.yaml` — no new system deps

## 12. Testing strategy

### Unit (no SDK calls)

- `test_kb_hypotheses.py` — CRUD on `hypotheses` table; tree rendering; status transitions; `evidence_refs` JSON roundtrip; cascade behavior on parent delete; scope authz still applies.
- `test_dispatch.py` — Mock `claude_agent_sdk.query()` returning canned messages; verify (a) dispatch wrapper composes the correct combined system prompt, (b) `### Hypothesis outcome` parser handles well-formed / missing / malformed reports, (c) `status="budget_exhausted"` surfaces correctly when SDK reports it, (d) errors surface gracefully.
- `test_profiles_manager.py` — Manager profile registered with key `manager`; has 6 skills; has correct `tools_allowlist`; system_addendum includes the dispatch checklist + specialist menu.
- `test_tool_registry.py` — Extend existing test: `ALL_TOOLS` now equals 68.

### Integration (mocked SDK)

- `test_dispatch_e2e.py` — In-memory KB; mock SDK to simulate a 2-dispatch sequence (manager dispatches `ad`, gets a confirmed hypothesis, dispatches a child hypothesis, gets refuted). Verify the resulting hypothesis tree matches expectation.

### Regression

- All existing 320 tests still pass after the schema bump and profile package split.

### Manual (out-of-suite, on real infrastructure)

- `tests/manual/manager_smoke.md` — 30-minute walkthrough: launch `reverser i -p manager 10.10.10.x` against an HTB AD lab, observe kickoff, watch one full dispatch round, verify hypothesis updates land in the KB, exercise the interrupt path, generate the engagement report, confirm attack-tree section renders.

## 13. Risks & open questions

| Risk | Mitigation |
|---|---|
| Manager dispatches without hypothesis_id, breaking the audit trail. | Dispatch wrapper logs a warning when `hypothesis_id is None`; system prompt strongly requires it; future v2 could make it required. |
| Specialist's report omits `### Hypothesis outcome` section. | Graceful parse: defaults to `inconclusive` and surfaces a warning in the report. Manager sees the warning and can re-dispatch with a stricter brief. |
| Concurrent dispatches collide on real infrastructure (e.g. simultaneous LDAP from same host). | Sequential default. Parallelism is opt-in. Documented gotcha in the smoke doc. |
| Profile module split breaks imports elsewhere. | `profiles/__init__.py` re-exports the same names; mechanical refactor; existing tests catch regressions. |
| Token budget for nested agents underestimated. | Per-dispatch defaults are intentionally small ($0.50). Manager sees `cost_usd` in every dispatch return and adapts. Global cap is the hard brake. |
| `tools_allowlist` enforcement subtly broken (manager invokes a disallowed tool and SDK errors mid-engagement). | Unit test verifies `tools_allowlist` plumbs through to `ClaudeAgentOptions`. Smoke test confirms manager actually can't call e.g. `netexec_smb`. |

## 14. Future work (explicitly v2+)

- Approval-gated dispatches for "loud" actions (a `D`-tier from Section 4 of brainstorming).
- Phase-level budget envelopes (recon/foothold/AD enum).
- Manager auto-resumes a budget-exhausted dispatch with a higher cap.
- Configurable specialist pool per session (`--specialists ad,pentest,...`).
- Cross-target manager (one manager coordinating multiple engagements).
- Recursive managers (a manager spawning another manager — likely useful for very large engagements).
- Cost/turn tracking per hypothesis (extend the `hypotheses` table with summary columns updated by the dispatch wrapper).
