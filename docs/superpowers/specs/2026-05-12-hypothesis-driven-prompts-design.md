# Hypothesis-driven pentest/webpentest prompts — design

**Date:** 2026-05-12
**Status:** Design approved; ready for implementation plan
**Roadmap entry:** Top 5 #5: "Hypothesis-driven pentest/webpentest prompts"
**Predecessor specs:** Manager profile (2026-05-09), AD pack (2026-05-03)

---

## 1. Goal

Port the AD profile's hypothesis-driven discipline to the two generic pentest
profiles (`pentest` and `webpentest`) in profile-tuned form, so the 10.13.38.23
grind-without-pivot failure mode (~1700 password attempts, no foothold, no
lessons retained) is harder to repeat.

The existing `hypotheses` KB table and the 4 CRUD tools (`kb_add_hypothesis`,
`kb_update_hypothesis`, `kb_list_hypotheses`, `kb_get_hypothesis`) — all
shipped with the manager profile — are reused as-is. No new code paths, no
schema changes. The work is entirely system-prompt and skill-prompt edits plus
metadata-assertion tests.

## 2. Non-goals

- No new tools.
- No new CRUD operations on the `hypotheses` table.
- No schema changes.
- No KB-backed failure counter — enforcement stays prompt-only (per
  Q2 decision; see §3 D2).
- No new "show-the-tree" skills on these profiles — the manager profile
  already covers that use case via `dispatch_specialist`.
- No `_skills.py` extraction of the hypothesis block into a shared helper
  (deferred; see §12 future work).
- No changes to the AD profile's existing hypothesis-loop block — it's the
  precedent we're porting from, but we don't harmonize it in this work
  (deferred; see §12).

## 3. Architectural decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Tuned per profile, not verbatim copies of the AD block | Web targets have legitimately more failable attempts before pivot. AD's K=3 / every-5-calls works for AD; webpentest gets K=5 / every-8-calls. Cheap to tune, hard to get back from a verbatim copy. |
| D2 | Prompt-only enforcement of the "K failed attempts → pivot" rule | Moving failure-counting from prompt to KB-backed storage shifts the discipline from "agent counts" to "agent calls kb_update_hypothesis after each failure" — same fragility, just relocated. Leverage is in the prompt formulation, not the storage. Strengthened the formulation with explicit triggers and consequences. |
| D3 | Augment entry-point + exploit-style skills only; leave tool-flow skills alone | The 10.13.38.23 failure happened in credential spraying. Putting hypothesis-CRUD direction directly in the SKILL_CREDS prompt is the highest-leverage intervention. Tool-only skills (port scan, ssl, dir-discover) don't have a "grind past failure" risk and stay focused. |
| D4 | K=3 for pentest, K=5 for webpentest | Web testing pattern: trying 3-5 payloads on one parameter is normal exploratory probing, not "grinding." Pentest K=3 matches AD precedent (3 orthogonal exploit attempts is a strong "this isn't working" signal). |
| D5 | Cadence: every 5 tool calls for pentest, every 8 for webpentest | Web pentest naturally takes more shallow probes between meaningful decisions; every-5-calls would interrupt momentum. Pentest's tighter cadence matches AD's. |
| D6 | "What counts as a failed attempt" gets an explicit list per profile | The AD block trusts the agent to define "failed attempt." We strengthen this — concrete triggers ("sqlmap returns 'not injectable' on all params") not vague language ("an unsuccessful try"). Removes the ambiguity that lets the 10.13.38.23 failure mode slip through. |
| D7 | Metadata-only tests (no behavioral / LLM-stub tests) | System prompts aren't executable. Behavioral verification (agent stub LLM that grinds and verifies pivot) is a separate, much bigger project. Metadata assertions catch the regression we're guarding against: someone silently deletes the hypothesis block. |
| D8 | Skill-augmentation testing asserts BOTH augmented AND untouched skills | Scope-creep guard: future maintainer adds hypothesis-CRUD to every skill, defeating Q3-B's "augment only the high-leverage ones" decision. An assertion that PORTSCAN does NOT contain `kb_add_hypothesis` catches that drift. |
| D9 | Update Cross-Cutting "Failure analysis trigger" roadmap item to ✅ shipped | This work IS that trigger, constrained to two profiles. Manager profile achieves the same end via dispatch. AD already had it. Marking shipped is accurate, with a status note explaining per-profile scope. |
| D10 | Reference "10.13.38.23" by name in the pentest addendum, not webpentest | The case study is AD-flavored (internal AD spray failure). Citing it in pentest reinforces "the cautionary tale." Webpentest gets its own generic framing ("a useful test costs 30 seconds; a useless one costs 30 minutes and tells you the same nothing") that isn't anchored to a specific past incident. |

## 4. File change set

### Add

| Path | Responsibility |
|---|---|
| `tests/test_profiles_pentest.py` | 7 metadata assertions on pentest profile (registration, hypothesis-loop presence, K=3 / every-5-calls present, CRUD tool names present, "what counts as a failed attempt" present, augmented skills mention CRUD, untouched skills remain tool-focused). |
| `tests/test_profiles_webpentest.py` | 7 metadata assertions on webpentest profile (same shape, K=5 / every-8-calls). |

### Modify

| Path | Change |
|---|---|
| `src/reverser/profiles/pentest.py` | Insert "Hypothesis-driven loop (NON-NEGOTIABLE)" section in `system_addendum`, between scope confirmation and methodology. Augment `SKILL_RECON`, `SKILL_EXPLOIT`, `SKILL_CREDS` prompts with hypothesis-CRUD direction. |
| `src/reverser/profiles/webpentest.py` | Insert "Hypothesis-driven loop (NON-NEGOTIABLE)" section at the top of `system_addendum`. (Skills live in `_skills.py`; webpentest's own file only owns the addendum and profile registration.) |
| `src/reverser/profiles/_skills.py` | Augment `SKILL_WEB_RECON`, `SKILL_WEB_SQLI`, `SKILL_WEB_MANUAL` prompts with hypothesis-CRUD direction. |
| `CAPABILITY_ROADMAP.md` | Mark Top 5 #5 ✅ shipped with status note. Also mark Cross-Cutting "Failure analysis trigger" ✅ shipped with per-profile scope note (per D9). |

### Does not change

- KB schema (`hypotheses` table already has everything needed)
- KB CRUD tools (`kb_add_hypothesis`, `kb_update_hypothesis`, `kb_list_hypotheses`, `kb_get_hypothesis`)
- Manager profile, AD profile, exploit profile (out of scope per non-goals)
- `tests/test_profiles_ad.py` (AD's existing test stays)
- Any code in `src/reverser/tools/`, `src/reverser/kb/`, `src/reverser/tui/`
- `devenv.nix`, backends, the TUI app structure

## 5. Pentest profile — system addendum addition

The new "Hypothesis-driven loop (NON-NEGOTIABLE)" section inserts between
"Methodology" (existing) and the "Nmap NSE script names" guidance (existing).
Position chosen so the discipline is read AFTER the agent knows what tools
exist, BEFORE it dives into the specific tool-name reference material.

```
### Hypothesis-driven loop (NON-NEGOTIABLE)

Pentest engagements fail when the operator keeps swinging at the same wall. The
10.13.38.23 report in this repo is the cautionary tale — ~1700 password
attempts, no foothold, no lessons retained. Discipline:

**At the start of every engagement** (right after recon completes), use
`kb_add_hypothesis` to record 3 root hypotheses about the likely foothold path.
Each hypothesis should be falsifiable in one sentence — e.g. "The Tomcat
manager at 10.10.10.5:8080 has default creds" or "The exposed SMB share on
\\10.10.10.5\sysvol leaks credentials via a script". A hypothesis is a CLAIM
about reality, not a TODO item.

**Every 5 tool calls**, stop and explicitly state:
- (a) Your current best hypothesis about the cheapest foothold path.
- (b) The single cheapest experiment that would disconfirm it.
- (c) What you would pivot to if (b) fails.

**Three-failure pivot rule.** After 3 failed exploitation attempts against the
same hypothesis, you MUST:
1. `kb_update_hypothesis(id=X, status=refuted)` with a one-line reason.
2. Stop attacking that surface. Do not keep tuning the same primitive.
3. Propose THREE orthogonal attack surfaces via `kb_add_hypothesis`. Orthogonal
   means: different service, different protocol, different credential class, or
   different vulnerability class. Same service with different payloads is NOT
   orthogonal.

**What counts as a failed attempt:**
- A credential spray that exhausts its wordlist without a valid login.
- An exploit module that runs to completion with no session opened.
- An RCE attempt that returns no callback / no command output within the timeout.
- A SQLi attempt where `sqlmap` exits with "not injectable" on all tested params.

What does NOT count: a recon scan, a service enumeration, an SSL/TLS probe.
Failures are exploitation primitives that didn't yield access.

The hypothesis tree is your record of WHAT YOU LEARNED. Update it as you go.
`kb_list_hypotheses` at the start of every new session shows you where you
left off. Don't re-derive things you already disproved.
```

## 6. Webpentest profile — system addendum addition

Inserted at the top of `system_addendum` (since the existing webpentest
addendum is short and the hypothesis block becomes the first major section).

```
### Hypothesis-driven loop (NON-NEGOTIABLE)

Web pentests fail when the operator fuzzes forever and never confirms anything.
A useful test costs 30 seconds; a useless one costs 30 minutes and tells you
the same nothing. Discipline:

**At the start of every engagement** (right after recon completes), use
`kb_add_hypothesis` to record 3 root hypotheses about the most exploitable
surface. Each hypothesis should be falsifiable in one sentence — e.g. "The
/api/users endpoint is vulnerable to IDOR via the id parameter" or "The login
form at /auth permits SQL injection via the username field". A hypothesis is a
CLAIM about reality, not a TODO item.

**Every 8 tool calls**, stop and explicitly state:
- (a) Your current best hypothesis about the most exploitable surface.
- (b) The single cheapest experiment that would disconfirm it.
- (c) What you would pivot to if (b) fails.

**Five-failure pivot rule.** After 5 failed exploitation attempts against the
same hypothesis, you MUST:
1. `kb_update_hypothesis(id=X, status=refuted)` with a one-line reason.
2. Stop testing that surface. Do not keep tuning payloads on the same parameter.
3. Propose THREE orthogonal attack surfaces via `kb_add_hypothesis`. Orthogonal
   means: different endpoint, different injection class (SQLi → SSRF → auth
   bypass), different vulnerability category, or different authentication tier
   (anon → authed → admin). Same endpoint with different payloads is NOT
   orthogonal.

**What counts as a failed attempt:**
- A `sqlmap_test` run that completes without finding an injection.
- An `ffuf_fuzz` directory scan that returns no novel endpoints (only known
  4xx/5xx noise).
- A `nuclei_scan` that reports no findings at severity high+.
- A manual payload probe (XSS, SSRF, auth bypass) that doesn't trigger the
  expected response.

What does NOT count: a passive recon scan, a `whatweb_fingerprint`, a
`wafw00f_detect`, a header check. Failures are payload-class probes that
returned nothing.

The hypothesis tree is your record of WHAT YOU LEARNED. Update it as you go.
`kb_list_hypotheses` at the start of every new session shows you where you
left off. Don't re-derive things you already disproved.
```

## 7. Skill prompt augmentations

Each augmentation is appended to the existing skill prompt — not a rewrite.
Six skills total: 3 in `pentest.py`, 3 in `_skills.py`.

### `pentest.py` skills

**`SKILL_RECON`** (key `r`) — entry point.

> After recon completes, propose 3 root hypotheses about the cheapest foothold
> path. Call `kb_add_hypothesis` for each one — a hypothesis is a falsifiable
> CLAIM ("The Tomcat manager at /manager/html has default creds"), not a TODO
> item ("Try Tomcat"). Confidence values: 80+ = strong evidence pointing this
> way, 50 = plausible, <30 = long shot. The Exploit and Cred Attack skills
> work through these in confidence order.

**`SKILL_EXPLOIT`** (key `x`) — exploit-attempt surface.

> Before attempting, call `kb_list_hypotheses status=proposed` and pick the
> highest-confidence unconfirmed one. State the hypothesis OUT LOUD before
> running the exploit. After each attempt, call `kb_update_hypothesis(id=X,
> status=...)` with confirmed/refuted/inconclusive plus a one-line outcome.
> **Three-failure pivot**: if you've made 3 failed exploitation attempts
> against this hypothesis, mark it refuted, STOP, and propose three orthogonal
> hypotheses (different service/protocol/credential class) before continuing.

**`SKILL_CREDS`** (key `c`) — the 10.13.38.23 antipattern surface.

> Credential spraying is the canonical "grind past the point of usefulness"
> failure mode. Before spraying ANYTHING, call `kb_list_hypotheses` — if a
> hypothesis about credential reuse / default creds / a specific wordlist is
> already in the tree, fold this attempt under that hypothesis. If not, create
> one first with `kb_add_hypothesis`. One spray attempt = one wordlist + one
> service + one user-list. A spray that exhausts its wordlist with no valid
> login is a FAILED ATTEMPT. After 3 failed credential-attack attempts against
> the same hypothesis, mark it refuted and pivot. Do not keep adding wordlists
> to a doomed primitive.

### `_skills.py` skills (used by webpentest)

**`SKILL_WEB_RECON`** (key `r`) — entry point.

> After recon completes, propose 3 root hypotheses about the most exploitable
> surface. Call `kb_add_hypothesis` for each one — a hypothesis is a falsifiable
> CLAIM ("The /api/users endpoint is vulnerable to IDOR via the id parameter"),
> not a TODO item ("Look at the API"). Confidence values: 80+ = strong
> evidence, 50 = plausible, <30 = long shot. Subsequent skills work through
> these in confidence order.

**`SKILL_WEB_SQLI`** (key `q`) — canonical web-exploit skill.

> Before running, call `kb_list_hypotheses status=proposed` and pick the
> highest-confidence unconfirmed one mentioning SQLi / injection. State the
> hypothesis OUT LOUD. After `sqlmap_test` returns, call
> `kb_update_hypothesis(id=X, status=...)` with confirmed/refuted/inconclusive
> plus a one-line outcome. **Five-failure pivot**: if you've made 5 failed
> SQLi attempts against this hypothesis, mark it refuted, STOP, and propose
> three orthogonal hypotheses (different endpoint, different injection class
> like SSRF or auth bypass, or different auth tier).

**`SKILL_WEB_MANUAL`** (key `m`) — open-ended probe skill.

> Manual probing is the second-most-common place to grind past usefulness
> (after fuzzing). Anchor every probe to a hypothesis: before checking a
> header / cookie / CORS / form, state which hypothesis you're testing. After
> 5 failed manual probes against the same hypothesis (5 different
> headers/cookies/parameters that all came back clean), mark the hypothesis
> refuted via `kb_update_hypothesis` and pivot.

### Skills left untouched

These existing skills do NOT get touched (per D3):

- **`pentest.py`**: `SKILL_PORTSCAN`, `SKILL_WEBSCAN`, `SKILL_SSLCHECK`, `SKILL_ENUM`, `SKILL_VULNSCAN`, `SKILL_PENTEST_WRITEUP`
- **`_skills.py`** (used by webpentest): `SKILL_WEB_SCAN`, `SKILL_WEB_DISCOVER`, `SKILL_WEB_SSL`, `SKILL_WEB_REPORT`

These are tool-flow skills (scans, port enumeration, vuln signature checks,
reporting) — no "grind past failure" risk because they're not
exploit-attempt surfaces.

## 8. Testing strategy

### `tests/test_profiles_pentest.py` (NEW — 7 assertions)

```python
"""Regression tests for the pentest profile's hypothesis discipline."""

from reverser.profiles import PROFILES, get_profile, list_profiles


def test_pentest_profile_registered():
    assert "pentest" in PROFILES
    p = get_profile("pentest")
    assert p.name == "Pentest"


def test_pentest_addendum_mentions_hypothesis_loop():
    p = get_profile("pentest")
    addendum = p.system_addendum.lower()
    assert "hypothesis-driven loop" in addendum
    assert "non-negotiable" in addendum
    assert "10.13.38.23" in p.system_addendum  # case-sensitive — exact match


def test_pentest_addendum_specifies_cadence_and_threshold():
    p = get_profile("pentest")
    addendum = p.system_addendum
    assert "5 tool calls" in addendum or "every 5" in addendum.lower()
    assert "3 failed" in addendum.lower() or "three-failure" in addendum.lower()


def test_pentest_addendum_names_hypothesis_crud_tools():
    p = get_profile("pentest")
    addendum = p.system_addendum
    for tool in ("kb_add_hypothesis", "kb_update_hypothesis", "kb_list_hypotheses"):
        assert tool in addendum, f"addendum missing CRUD tool: {tool}"


def test_pentest_addendum_defines_failed_attempt():
    p = get_profile("pentest")
    addendum = p.system_addendum.lower()
    assert "what counts as a failed attempt" in addendum


def test_pentest_augmented_skills_mention_hypothesis_crud():
    p = get_profile("pentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_RECON (r)
    assert "kb_add_hypothesis" in skills_by_key["r"].prompt
    # SKILL_EXPLOIT (x)
    assert "kb_list_hypotheses" in skills_by_key["x"].prompt
    assert "kb_update_hypothesis" in skills_by_key["x"].prompt
    assert "three-failure" in skills_by_key["x"].prompt.lower() or \
           "3 failed" in skills_by_key["x"].prompt.lower()
    # SKILL_CREDS (c)
    assert "kb_add_hypothesis" in skills_by_key["c"].prompt or \
           "kb_list_hypotheses" in skills_by_key["c"].prompt
    assert "kb_update_hypothesis" in skills_by_key["c"].prompt


def test_pentest_untouched_skills_remain_tool_focused():
    p = get_profile("pentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_PORTSCAN (p), SKILL_SSLCHECK (l), SKILL_VULNSCAN (v) — should NOT mention hypothesis CRUD
    for key in ("p", "l", "v"):
        assert "kb_add_hypothesis" not in skills_by_key[key].prompt, \
            f"skill {key!r} unexpectedly mentions kb_add_hypothesis"
```

### `tests/test_profiles_webpentest.py` (NEW — 7 assertions)

Same shape, web-tuned constants (every-8-calls, 5-failure-pivot).

```python
"""Regression tests for the webpentest profile's hypothesis discipline."""

from reverser.profiles import PROFILES, get_profile, list_profiles


def test_webpentest_profile_registered():
    assert "webpentest" in PROFILES
    p = get_profile("webpentest")
    assert p.name == "Web Pentest"


def test_webpentest_addendum_mentions_hypothesis_loop():
    p = get_profile("webpentest")
    addendum = p.system_addendum.lower()
    assert "hypothesis-driven loop" in addendum
    assert "non-negotiable" in addendum


def test_webpentest_addendum_specifies_cadence_and_threshold():
    p = get_profile("webpentest")
    addendum = p.system_addendum
    assert "8 tool calls" in addendum or "every 8" in addendum.lower()
    assert "5 failed" in addendum.lower() or "five-failure" in addendum.lower()


def test_webpentest_addendum_names_hypothesis_crud_tools():
    p = get_profile("webpentest")
    addendum = p.system_addendum
    for tool in ("kb_add_hypothesis", "kb_update_hypothesis", "kb_list_hypotheses"):
        assert tool in addendum, f"addendum missing CRUD tool: {tool}"


def test_webpentest_addendum_defines_failed_attempt():
    p = get_profile("webpentest")
    addendum = p.system_addendum.lower()
    assert "what counts as a failed attempt" in addendum


def test_webpentest_augmented_skills_mention_hypothesis_crud():
    p = get_profile("webpentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_WEB_RECON (r)
    assert "kb_add_hypothesis" in skills_by_key["r"].prompt
    # SKILL_WEB_SQLI (q)
    assert "kb_list_hypotheses" in skills_by_key["q"].prompt
    assert "kb_update_hypothesis" in skills_by_key["q"].prompt
    assert "five-failure" in skills_by_key["q"].prompt.lower() or \
           "5 failed" in skills_by_key["q"].prompt.lower()
    # SKILL_WEB_MANUAL (m)
    assert "kb_update_hypothesis" in skills_by_key["m"].prompt


def test_webpentest_untouched_skills_remain_tool_focused():
    p = get_profile("webpentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_WEB_SCAN (v), SKILL_WEB_DISCOVER (d), SKILL_WEB_SSL (l), SKILL_WEB_REPORT (w)
    for key in ("v", "d", "l", "w"):
        assert "kb_add_hypothesis" not in skills_by_key[key].prompt, \
            f"skill {key!r} unexpectedly mentions kb_add_hypothesis"
```

### Coverage

| Test | What regression it catches |
|---|---|
| `_profile_registered` | Profile import / registration broken |
| `_addendum_mentions_hypothesis_loop` | Someone deletes the whole hypothesis section |
| `_addendum_specifies_cadence_and_threshold` | The K value gets silently changed |
| `_addendum_names_hypothesis_crud_tools` | The tool names get renamed in a future KB refactor and the prompt isn't updated |
| `_addendum_defines_failed_attempt` | The "what counts as a failed attempt" section gets removed in a copy-edit |
| `_augmented_skills_mention_hypothesis_crud` | Someone "cleans up" the skill prompts and reverts them to tool-only |
| `_untouched_skills_remain_tool_focused` | Scope-creep guard (per D8) |

### What's NOT tested

- The actual K values cross-profile (no comparative assertion — each profile asserted independently)
- That `kb_update_hypothesis` is called with `status=refuted` specifically (too brittle)
- That the agent FOLLOWS the rules at runtime (out of scope per D7; behavioral testing is a separate, much larger project)

**Total new tests:** 14 (7 per profile). Test count goes from 524 → 538.

## 9. Roadmap updates

`CAPABILITY_ROADMAP.md` gets two checkbox flips:

- **Top 5 #5** — flip `[ ]` → `[x]`. Status note:
  > **Status (2026-05-12):** Shipped via this work. Hypothesis-loop block
  > inserted into `pentest` (K=3, every-5-calls) and `webpentest` (K=5,
  > every-8-calls) system addenda. Skill-level reinforcement added to RECON,
  > EXPLOIT, CREDS (pentest) and WEB_RECON, WEB_SQLI, WEB_MANUAL (webpentest).
  > Specs/plans: `2026-05-12-hypothesis-driven-prompts-{design,plan}.md`.

- **Cross-Cutting "Failure analysis trigger"** (per D9) — flip `[ ]` → `[x]`. Status note:
  > **Status (2026-05-12):** Shipped. After K failed exploit attempts against
  > a hypothesis, the pentest (K=3) and webpentest (K=5) profile prompts now
  > require mark-refuted + propose-three-orthogonal-surfaces. AD profile
  > already had this discipline; manager profile achieves the same end via
  > `dispatch_specialist`. Other profiles (general/linux/windows/etc.) are
  > out of scope — they don't have an exploit-attempt surface.

Also update the "As of YYYY-MM-DD" snapshot line at the top of the roadmap
with bumped test count (538) and date (2026-05-12).

## 10. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Agent becomes over-cautious — pivots after K=3/K=5 even when sticking with the hypothesis is right | The "what counts as a failed attempt" sections define triggers tightly. A `sqlmap` run that finds *some* signal (e.g. "boolean-based blind injection POSSIBLY detected") is NOT a failed attempt — only "not injectable on all params" is. If over-fires in practice, raise K. |
| R2 | Prompt bloat — adding ~25 lines × 2 profiles is significant context | Bounded addition. Content is high-value-per-token. If problematic, extract to shared constant later (see §12). Not pre-optimizing. |
| R3 | Skill augmentations duplicate the addendum's pivot language; drift risk on future refactor | Trade-off accepted per D3. The duplication is intentional reinforcement at the high-leverage moment. Tests catch deletion of either; rewording one but not the other passes tests if both still contain the keyword. |
| R4 | K values may be wrong empirically | Values are explicit and easy to change (one line in each addendum, one assertion in each test). Plan to revisit after first real engagement. |
| R5 | "10.13.38.23" reference is repo-specific (would confuse a forker) | Accepted. The AD profile already does this. Pattern is "include the cautionary tale, by name." Not a blocker. |

## 11. Implementation order

This is one of the smallest specs we've shipped. Recommended task breakdown
(formal plan will be written by the writing-plans skill):

1. Add `pentest` profile addendum block + augment 3 skills → run 7 tests.
2. Add `webpentest` profile addendum block + augment 3 `_skills.py` skills → run 7 tests.
3. Update `CAPABILITY_ROADMAP.md` (both checkbox flips + snapshot line).
4. Final full-suite run (538 passing, 1 skipped expected).
5. Merge.

Estimated 6-8 plan tasks total. No phased rollout; one merge.

## 12. Future work (v2+)

- **Harmonize hypothesis blocks across all profiles** — extract the shared template into `_skills.py` (Q1 option C deferred). AD profile, pentest, webpentest, exploit all have variations on this; consolidating into one parameterized string would prevent drift. Not done now to keep this work small.
- **KB-backed failure counter** — Q2 option B deferred. If prompt-only enforcement (Q2 option A) doesn't move the needle, consider adding a `failures_count` column to the `hypotheses` table and have `kb_update_hypothesis` auto-increment it on `status=refuted` updates. The system prompt would then say "Before exploit, `kb_get_hypothesis(id)` — if failures_count >= K, hypothesis is dead, pivot."
- **Behavioral testing via stub LLM** — Q4 option C deferred. Build a deterministic test harness that feeds a stub backend predetermined "I keep grinding" transcripts and asserts the agent eventually calls `kb_update_hypothesis status=abandoned`. Significant scaffolding cost; warrants its own roadmap entry.
- **Per-profile K-value telemetry** — instrument the per-engagement KB to log "hypothesis abandoned after N attempts" so K values can be tuned empirically.
