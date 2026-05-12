# Hypothesis-Driven Pentest/Webpentest Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the AD profile's hypothesis-driven discipline to the two generic pentest profiles (`pentest` and `webpentest`) in profile-tuned form, so the 10.13.38.23 grind-without-pivot failure mode is harder to repeat.

**Architecture:** Pure prompt-text changes — three Python files modified, two test files created, one Markdown file updated. No new tools, no new schema, no new code paths. The existing `hypotheses` KB table and its 4 CRUD tools (shipped with the manager profile) are reused verbatim. Discipline is enforced at the prompt level: a "Hypothesis-driven loop (NON-NEGOTIABLE)" section in each profile's `system_addendum`, plus hypothesis-CRUD direction added to 6 high-leverage skill prompts (3 per profile).

**Tech Stack:**
- Python 3.13 (existing harness)
- `pytest` (existing test harness; same `pytest` invocation pattern as `tests/test_profiles_ad.py`)
- No new dependencies

**Spec:** `docs/superpowers/specs/2026-05-12-hypothesis-driven-prompts-design.md` — references to "D1"…"D10", "§5", "§6", "§7", "§8" in this plan map to the spec's architectural decisions and sections.

**Branch / worktree:** `feature/hypothesis-driven-prompts` at `.worktrees/hypothesis-driven-prompts/` (already created, based on `main` at `e254b37`).

**Test runner:** `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest`

**Baseline:** 524 passing tests, 1 skipped. Target after this plan: 538 passing tests (524 + 14 new metadata assertions), 1 skipped.

**K values:**
- Pentest profile: K=3 (3 failed exploitation attempts → mark refuted, pivot), cadence every 5 tool calls
- Webpentest profile: K=5, cadence every 8 tool calls

---

## File structure

### Add

| Path | Responsibility |
|---|---|
| `tests/test_profiles_pentest.py` | 7 metadata assertions on pentest profile (registration, hypothesis-loop block present, K=3 / every-5-calls present, CRUD tool names present, "what counts as a failed attempt" present, augmented skills mention CRUD, untouched skills remain tool-focused). |
| `tests/test_profiles_webpentest.py` | 7 metadata assertions on webpentest profile (same shape, K=5 / every-8-calls). |

### Modify

| Path | Change |
|---|---|
| `src/reverser/profiles/pentest.py` | Insert "Hypothesis-driven loop (NON-NEGOTIABLE)" section into `system_addendum`, placed between the existing "Methodology" block and the "Nmap NSE script names" reference material. Augment 3 skill prompts: `SKILL_RECON`, `SKILL_EXPLOIT`, `SKILL_CREDS`. |
| `src/reverser/profiles/webpentest.py` | Insert "Hypothesis-driven loop (NON-NEGOTIABLE)" section at the top of `system_addendum`. (The webpentest skills live in `_skills.py`, see next row.) |
| `src/reverser/profiles/_skills.py` | Augment 3 web skills: `SKILL_WEB_RECON`, `SKILL_WEB_SQLI`, `SKILL_WEB_MANUAL`. |
| `CAPABILITY_ROADMAP.md` | Flip Top 5 #5 and Cross-Cutting "Failure analysis trigger" both to `[x]` with status notes. Bump the "As of …" snapshot line (date + test count). |

### Does not change

- `src/reverser/profiles/ad.py` — its existing hypothesis block is the precedent we ported from; left alone per spec D-Open-2.
- `src/reverser/profiles/__init__.py`, `_register`, `get_profile`, `list_profiles` — no profile registration changes.
- `src/reverser/kb/` — KB schema, CRUD tools, anything KB-related (D2: prompt-only enforcement).
- `src/reverser/tools/` — no new tools.
- `tests/test_profiles_ad.py` — AD's existing tests stay untouched.
- `devenv.nix`, backends, the TUI app structure.

---

## Phase plan (6 tasks)

| Phase | Tasks | Description |
|---|---|---|
| 1 | 1 | Pentest tests + addendum (test_profiles_pentest.py + pentest.py addendum block) |
| 2 | 2 | Pentest skill augmentations (SKILL_RECON, SKILL_EXPLOIT, SKILL_CREDS) |
| 3 | 3 | Webpentest tests + addendum (test_profiles_webpentest.py + webpentest.py addendum block) |
| 4 | 4 | Webpentest skill augmentations (SKILL_WEB_RECON, SKILL_WEB_SQLI, SKILL_WEB_MANUAL) in _skills.py |
| 5 | 5 | CAPABILITY_ROADMAP.md updates |
| 6 | 6 | Final validation |

Each task except 5 and 6 is a TDD cycle (write failing tests → run → implement → run → commit). Task 5 is a doc-only edit (verify with grep). Task 6 is full-suite validation.

---

## Task 1: Pentest tests + system addendum

**Files:**
- Create: `tests/test_profiles_pentest.py`
- Modify: `src/reverser/profiles/pentest.py` (system_addendum only — skills come in Task 2)

This task adds all 7 tests AND inserts the hypothesis-loop addendum block. After this task, 5 of the 7 tests pass — the 2 that assert skill-prompt content (`test_pentest_augmented_skills_mention_hypothesis_crud` and `test_pentest_untouched_skills_remain_tool_focused`) still fail because the skills haven't been augmented yet. Task 2 closes those.

The split is intentional: Task 1 makes ~70% of pentest tests pass on system-addendum alone, validating the addendum text in isolation. Task 2 then has a tight failure-driven scope for the skill edits.

- [ ] **Step 1: Write the test file**

Create `tests/test_profiles_pentest.py` with this exact content:

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
    # Pentest tuning: every 5 calls, 3-failure pivot
    assert "5 tool calls" in addendum or "every 5" in addendum.lower()
    assert "3 failed" in addendum.lower() or "three-failure" in addendum.lower()


def test_pentest_addendum_names_hypothesis_crud_tools():
    p = get_profile("pentest")
    addendum = p.system_addendum
    for tool in ("kb_add_hypothesis", "kb_update_hypothesis", "kb_list_hypotheses"):
        assert tool in addendum, f"addendum missing CRUD tool: {tool}"


def test_pentest_addendum_defines_failed_attempt():
    """The strengthened formulation requires explicit triggers, not vague language."""
    p = get_profile("pentest")
    addendum = p.system_addendum.lower()
    assert "what counts as a failed attempt" in addendum


def test_pentest_augmented_skills_mention_hypothesis_crud():
    """Per spec §7: SKILL_RECON, SKILL_EXPLOIT, SKILL_CREDS got hypothesis-CRUD direction."""
    p = get_profile("pentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_RECON (r) creates root hypotheses after recon
    assert "kb_add_hypothesis" in skills_by_key["r"].prompt
    # SKILL_EXPLOIT (x) reads + updates hypotheses; mentions the three-failure pivot
    assert "kb_list_hypotheses" in skills_by_key["x"].prompt
    assert "kb_update_hypothesis" in skills_by_key["x"].prompt
    assert "three-failure" in skills_by_key["x"].prompt.lower() or \
           "3 failed" in skills_by_key["x"].prompt.lower()
    # SKILL_CREDS (c) calls out spray as the 10.13.38.23 antipattern
    assert "kb_add_hypothesis" in skills_by_key["c"].prompt or \
           "kb_list_hypotheses" in skills_by_key["c"].prompt
    assert "kb_update_hypothesis" in skills_by_key["c"].prompt


def test_pentest_untouched_skills_remain_tool_focused():
    """Confirm we didn't accidentally bloat the skills we said we'd leave alone."""
    p = get_profile("pentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_PORTSCAN (p), SKILL_SSLCHECK (l), SKILL_VULNSCAN (v) — should NOT mention hypothesis CRUD
    for key in ("p", "l", "v"):
        assert "kb_add_hypothesis" not in skills_by_key[key].prompt, \
            f"skill {key!r} unexpectedly mentions kb_add_hypothesis"
```

- [ ] **Step 2: Run tests to verify they all fail**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py -v
```

Expected: `test_pentest_profile_registered` PASSES (profile is already registered). The other 6 FAIL because the addendum doesn't mention "hypothesis-driven loop", doesn't list CRUD tools, doesn't specify cadence/threshold, and the skill prompts haven't been augmented. Exact output will include AssertionError on `hypothesis-driven loop`, `kb_add_hypothesis`, `5 tool calls`, etc.

- [ ] **Step 3: Insert the hypothesis-loop block into pentest.py**

Edit `src/reverser/profiles/pentest.py`. Find this section in the `system_addendum` (around line 161 — it ends "Reporting: Structured findings with severity ratings"):

```
6. **Reporting**: Structured findings with severity ratings

**Nmap NSE script names — ONLY use these exact names (do NOT invent names):**
```

Insert the new "Hypothesis-driven loop (NON-NEGOTIABLE)" section BETWEEN them, so the addendum reads:

```
6. **Reporting**: Structured findings with severity ratings

### Hypothesis-driven loop (NON-NEGOTIABLE)

Pentest engagements fail when the operator keeps swinging at the same wall. The
10.13.38.23 report in this repo is the cautionary tale — ~1700 password
attempts, no foothold, no lessons retained. Discipline:

**At the start of every engagement** (right after recon completes), use
`kb_add_hypothesis` to record 3 root hypotheses about the likely foothold path.
Each hypothesis should be falsifiable in one sentence — e.g. "The Tomcat
manager at 10.10.10.5:8080 has default creds" or "The exposed SMB share on
\\\\10.10.10.5\\sysvol leaks credentials via a script". A hypothesis is a CLAIM
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

**Nmap NSE script names — ONLY use these exact names (do NOT invent names):**
```

Note: the file's existing addendum is wrapped in a triple-quoted Python string with line-continuation `\` at line ends (look at the existing `system_addendum="""\ ` block). Don't try to preserve those continuations — just paste the block as plain prose, no `\` at line ends. The string is multi-line; Python literal-paste works fine.

Implementation hint: in Python the easiest path is to use the `Edit` tool with the existing `**Nmap NSE script names — ONLY use these exact names ...` line as the `old_string` anchor and prepend the new section. Like:

```python
# Edit pattern (conceptual):
old_string = "**Nmap NSE script names — ONLY use these exact names (do NOT invent names):**"
new_string = "<hypothesis-loop block>\n\n**Nmap NSE script names — ONLY use these exact names (do NOT invent names):**"
```

The SMB UNC-path example contains backslashes — in a Python triple-quoted string they need to be doubled OR you can use a raw string OR you can replace the example. Easiest is to write `\\\\10.10.10.5\\sysvol` in the source (renders as `\\10.10.10.5\sysvol` to the agent). If that's awkward, simplify the example to `the SMB share \\\\10.10.10.5\\public leaks credentials via a script` or even drop the UNC example and write `the SMB share on 10.10.10.5 leaks credentials via a config file` instead. The test only checks for the substring `"Hypothesis-driven loop"` etc., not the SMB example wording.

- [ ] **Step 4: Run tests to verify 5 of 7 now pass**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py -v
```

Expected: 5 PASS, 2 FAIL. The 5 passing:
- `test_pentest_profile_registered`
- `test_pentest_addendum_mentions_hypothesis_loop`
- `test_pentest_addendum_specifies_cadence_and_threshold`
- `test_pentest_addendum_names_hypothesis_crud_tools`
- `test_pentest_addendum_defines_failed_attempt`

The 2 failing:
- `test_pentest_augmented_skills_mention_hypothesis_crud` — fails on `assert "kb_add_hypothesis" in skills_by_key["r"].prompt` because SKILL_RECON's prompt hasn't been augmented yet (Task 2).
- `test_pentest_untouched_skills_remain_tool_focused` — PASSES (no skill has `kb_add_hypothesis` yet, so the untouched-skills check is trivially satisfied). Actually re-reading: this test ASSERTS that p/l/v skills do NOT mention `kb_add_hypothesis`. Since no pentest skill mentions it yet, this passes.

So actual expectation: **6 PASS, 1 FAIL** (only `test_pentest_augmented_skills_mention_hypothesis_crud` fails). If you see something different (e.g. the untouched-skills test fails), look for an unintended skill change.

- [ ] **Step 5: Commit**

```bash
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts add tests/test_profiles_pentest.py src/reverser/profiles/pentest.py
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts commit -m "feat(profiles): pentest hypothesis-loop block + test file"
```

---

## Task 2: Pentest skill augmentations

**Files:**
- Modify: `src/reverser/profiles/pentest.py` (SKILL_RECON, SKILL_EXPLOIT, SKILL_CREDS prompts)

Adds hypothesis-CRUD direction to the three high-leverage skills per spec D3. Each augmentation is a paragraph appended to the existing skill's `prompt` string — not a rewrite.

- [ ] **Step 1: Confirm the failing test from Task 1**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py::test_pentest_augmented_skills_mention_hypothesis_crud -v
```

Expected: FAIL on `assert "kb_add_hypothesis" in skills_by_key["r"].prompt`.

- [ ] **Step 2: Augment SKILL_RECON**

Edit `src/reverser/profiles/pentest.py`. Find the existing `SKILL_RECON` definition (around line 8):

```python
SKILL_RECON = Skill(
    name="Recon",
    key="r",
    description="Full network reconnaissance of the target",
    prompt="Perform comprehensive reconnaissance of the target. Run these in parallel:\n"
           "1. nmap_scan with version detection (scan_type='version')\n"
           "2. dns_recon to enumerate DNS records\n"
           "3. If web ports are likely, run whatweb_scan\n\n"
           "Summarize all open ports, services, versions, and OS fingerprinting results.",
)
```

Replace the closing `,)` of the `prompt=` string with the appended hypothesis-CRUD paragraph. New definition:

```python
SKILL_RECON = Skill(
    name="Recon",
    key="r",
    description="Full network reconnaissance of the target",
    prompt="Perform comprehensive reconnaissance of the target. Run these in parallel:\n"
           "1. nmap_scan with version detection (scan_type='version')\n"
           "2. dns_recon to enumerate DNS records\n"
           "3. If web ports are likely, run whatweb_scan\n\n"
           "Summarize all open ports, services, versions, and OS fingerprinting results.\n\n"
           "After recon completes, propose 3 root hypotheses about the cheapest foothold "
           "path. Call kb_add_hypothesis for each one — a hypothesis is a falsifiable CLAIM "
           "(\"The Tomcat manager at /manager/html has default creds\"), not a TODO item "
           "(\"Try Tomcat\"). Confidence values: 80+ = strong evidence pointing this way, "
           "50 = plausible, <30 = long shot. The Exploit and Cred Attack skills work "
           "through these in confidence order.",
)
```

- [ ] **Step 3: Augment SKILL_EXPLOIT**

Find the existing `SKILL_EXPLOIT` definition (around line 100):

```python
SKILL_EXPLOIT = Skill(
    name="Exploit",
    key="x",
    description="Search for and attempt known exploits against discovered services",
    prompt="Based on previously discovered service versions, attempt exploitation:\n"
           "1. Run `searchsploit <service> <version>` via bash for each discovered service\n"
           "2. Prioritize unauthenticated remote exploits with critical/high severity\n"
           "3. For promising exploits, review the exploit code, stage it, and attempt execution via bash\n"
           "4. Check Metasploit modules with `msfconsole -q -x 'search <service>'` if available\n"
           "5. Document each attempt: exploit used, target, outcome, and any obtained access\n\n"
           "Focus on confirmed vulnerabilities from the vuln scan phase first. "
           "Do not attempt DoS or destructive exploits.",
)
```

Replace with the augmented version (prompt extended with hypothesis-CRUD direction):

```python
SKILL_EXPLOIT = Skill(
    name="Exploit",
    key="x",
    description="Search for and attempt known exploits against discovered services",
    prompt="Based on previously discovered service versions, attempt exploitation:\n"
           "1. Run `searchsploit <service> <version>` via bash for each discovered service\n"
           "2. Prioritize unauthenticated remote exploits with critical/high severity\n"
           "3. For promising exploits, review the exploit code, stage it, and attempt execution via bash\n"
           "4. Check Metasploit modules with `msfconsole -q -x 'search <service>'` if available\n"
           "5. Document each attempt: exploit used, target, outcome, and any obtained access\n\n"
           "Focus on confirmed vulnerabilities from the vuln scan phase first. "
           "Do not attempt DoS or destructive exploits.\n\n"
           "Before attempting, call kb_list_hypotheses status=proposed and pick the "
           "highest-confidence unconfirmed one. State the hypothesis OUT LOUD before "
           "running the exploit. After each attempt, call kb_update_hypothesis(id=X, "
           "status=...) with confirmed/refuted/inconclusive plus a one-line outcome. "
           "Three-failure pivot: if you've made 3 failed exploitation attempts against "
           "this hypothesis, mark it refuted, STOP, and propose three orthogonal "
           "hypotheses (different service/protocol/credential class) before continuing.",
)
```

- [ ] **Step 4: Augment SKILL_CREDS**

Find the existing `SKILL_CREDS` definition (around line 114):

```python
SKILL_CREDS = Skill(
    name="Cred Attack",
    key="c",
    description="Credential attacks: brute force, default creds, hash cracking",
    prompt="Perform credential attacks against discovered services:\n"
           "1. Test default credentials on all services (admin/admin, root/root, service-specific defaults)\n"
           "2. For SSH, FTP, SMB, HTTP Basic — run `hydra -L top-usernames-shortlist.txt -P rockyou.txt "
           "<target> <service>` via bash (use small wordlists first)\n"
           "3. For Kerberos: use kerberos_enum for AS-REP roasting and kerberoasting\n"
           "4. If hashes were obtained, attempt cracking with `hashcat` or `john` via bash\n"
           "5. Test password reuse across all discovered services\n\n"
           "Document all valid credentials found and the services they grant access to.",
)
```

Replace with:

```python
SKILL_CREDS = Skill(
    name="Cred Attack",
    key="c",
    description="Credential attacks: brute force, default creds, hash cracking",
    prompt="Perform credential attacks against discovered services:\n"
           "1. Test default credentials on all services (admin/admin, root/root, service-specific defaults)\n"
           "2. For SSH, FTP, SMB, HTTP Basic — run `hydra -L top-usernames-shortlist.txt -P rockyou.txt "
           "<target> <service>` via bash (use small wordlists first)\n"
           "3. For Kerberos: use kerberos_enum for AS-REP roasting and kerberoasting\n"
           "4. If hashes were obtained, attempt cracking with `hashcat` or `john` via bash\n"
           "5. Test password reuse across all discovered services\n\n"
           "Document all valid credentials found and the services they grant access to.\n\n"
           "Credential spraying is the canonical \"grind past the point of usefulness\" "
           "failure mode. Before spraying ANYTHING, call kb_list_hypotheses — if a "
           "hypothesis about credential reuse / default creds / a specific wordlist is "
           "already in the tree, fold this attempt under that hypothesis. If not, create "
           "one first with kb_add_hypothesis. One spray attempt = one wordlist + one "
           "service + one user-list. A spray that exhausts its wordlist with no valid "
           "login is a FAILED ATTEMPT. After 3 failed credential-attack attempts against "
           "the same hypothesis, mark it refuted via kb_update_hypothesis and pivot. Do "
           "not keep adding wordlists to a doomed primitive.",
)
```

- [ ] **Step 5: Run tests + commit**

Run all pentest tests:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py -v
```

Expected: 7/7 PASS.

Commit:
```bash
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts add src/reverser/profiles/pentest.py
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts commit -m "feat(profiles): pentest skill augmentations (RECON/EXPLOIT/CREDS)"
```

---

## Task 3: Webpentest tests + system addendum

**Files:**
- Create: `tests/test_profiles_webpentest.py`
- Modify: `src/reverser/profiles/webpentest.py` (system_addendum only — skills come in Task 4)

Mirror of Task 1 but for the webpentest profile. After this task, 5-6 of the 7 tests pass — the 1-2 that assert skill-prompt content fail because `_skills.py` hasn't been touched yet. Task 4 closes those.

- [ ] **Step 1: Write the test file**

Create `tests/test_profiles_webpentest.py` with this exact content:

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
    # Webpentest tuning: every 8 calls, 5-failure pivot
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
    """Per spec §7: SKILL_WEB_RECON, SKILL_WEB_SQLI, SKILL_WEB_MANUAL got hypothesis-CRUD direction."""
    p = get_profile("webpentest")
    skills_by_key = {s.key: s for s in p.skills}
    # SKILL_WEB_RECON (r) creates root hypotheses after recon
    assert "kb_add_hypothesis" in skills_by_key["r"].prompt
    # SKILL_WEB_SQLI (q) reads + updates hypotheses; mentions five-failure pivot
    assert "kb_list_hypotheses" in skills_by_key["q"].prompt
    assert "kb_update_hypothesis" in skills_by_key["q"].prompt
    assert "five-failure" in skills_by_key["q"].prompt.lower() or \
           "5 failed" in skills_by_key["q"].prompt.lower()
    # SKILL_WEB_MANUAL (m) calls out manual probing as the second-most-common grind
    assert "kb_update_hypothesis" in skills_by_key["m"].prompt


def test_webpentest_untouched_skills_remain_tool_focused():
    """SKILL_WEB_SCAN (v), SKILL_WEB_DISCOVER (d), SKILL_WEB_SSL (l), SKILL_WEB_REPORT (w)
    should NOT mention hypothesis CRUD."""
    p = get_profile("webpentest")
    skills_by_key = {s.key: s for s in p.skills}
    for key in ("v", "d", "l", "w"):
        assert "kb_add_hypothesis" not in skills_by_key[key].prompt, \
            f"skill {key!r} unexpectedly mentions kb_add_hypothesis"
```

- [ ] **Step 2: Run tests to verify they all (or mostly) fail**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_webpentest.py -v
```

Expected:
- `test_webpentest_profile_registered` PASS
- `test_webpentest_untouched_skills_remain_tool_focused` PASS (vacuously — no skill has `kb_add_hypothesis` yet)
- 5 others FAIL — addendum missing all hypothesis content, skill prompts missing CRUD references.

- [ ] **Step 3: Insert the hypothesis-loop block into webpentest.py**

Edit `src/reverser/profiles/webpentest.py`. The existing `system_addendum` (lines 19-37) currently reads:

```python
    system_addendum="""\

## Profile: Web Application Penetration Testing

You are performing a web application penetration test. Follow OWASP Testing Guide methodology:
1. **Reconnaissance**: Fingerprint technologies, enumerate subdomains, scan ports, detect WAFs
2. **Enumeration**: Discover directories, files, hidden endpoints, API routes
3. **Vulnerability scanning**: Run automated scanners (nuclei, nikto), check TLS
4. **Manual testing**: Focus on OWASP Top 10 — injection, broken access control, misconfig, auth failures
5. **Exploitation**: Confirm vulnerabilities with proof-of-concept when safe to do so
6. **Reporting**: Document findings with severity, evidence, and remediation

Key priorities:
- Always start with passive recon before active scanning
- Check security headers, CORS, cookies on every target
- Test authentication and session management thoroughly
- Look for IDOR, path traversal, and privilege escalation
- Check for information disclosure in error pages, headers, and comments
- Test input validation on all user-controllable parameters
""",
```

Replace with the version that has the hypothesis-loop block inserted at the top (between the `## Profile:` header and the "You are performing..." paragraph, per spec §6):

```python
    system_addendum="""\

## Profile: Web Application Penetration Testing

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

### Methodology

You are performing a web application penetration test. Follow OWASP Testing Guide methodology:
1. **Reconnaissance**: Fingerprint technologies, enumerate subdomains, scan ports, detect WAFs
2. **Enumeration**: Discover directories, files, hidden endpoints, API routes
3. **Vulnerability scanning**: Run automated scanners (nuclei, nikto), check TLS
4. **Manual testing**: Focus on OWASP Top 10 — injection, broken access control, misconfig, auth failures
5. **Exploitation**: Confirm vulnerabilities with proof-of-concept when safe to do so
6. **Reporting**: Document findings with severity, evidence, and remediation

Key priorities:
- Always start with passive recon before active scanning
- Check security headers, CORS, cookies on every target
- Test authentication and session management thoroughly
- Look for IDOR, path traversal, and privilege escalation
- Check for information disclosure in error pages, headers, and comments
- Test input validation on all user-controllable parameters
""",
```

Note the `### Methodology` heading was added before "You are performing..." to give the existing methodology block a header (since the new hypothesis-loop is also an `### …` section, the methodology needs one too for visual parity).

Implementation hint: use `Edit` with `old_string = "## Profile: Web Application Penetration Testing\n\nYou are performing"` and the new_string starting `## Profile: ...\n\n### Hypothesis-driven loop ...\n\n...\n\n### Methodology\n\nYou are performing`. The triple-quoted string boundaries are preserved by the edit.

- [ ] **Step 4: Run tests to verify 5-6 of 7 pass**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_webpentest.py -v
```

Expected: 6 PASS, 1 FAIL. The 6 passing:
- `test_webpentest_profile_registered`
- `test_webpentest_addendum_mentions_hypothesis_loop`
- `test_webpentest_addendum_specifies_cadence_and_threshold`
- `test_webpentest_addendum_names_hypothesis_crud_tools`
- `test_webpentest_addendum_defines_failed_attempt`
- `test_webpentest_untouched_skills_remain_tool_focused` (vacuous)

The 1 failing:
- `test_webpentest_augmented_skills_mention_hypothesis_crud` — fails on `assert "kb_add_hypothesis" in skills_by_key["r"].prompt` because SKILL_WEB_RECON hasn't been augmented yet (Task 4).

- [ ] **Step 5: Commit**

```bash
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts add tests/test_profiles_webpentest.py src/reverser/profiles/webpentest.py
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts commit -m "feat(profiles): webpentest hypothesis-loop block + test file"
```

---

## Task 4: Webpentest skill augmentations (in _skills.py)

**Files:**
- Modify: `src/reverser/profiles/_skills.py` (SKILL_WEB_RECON, SKILL_WEB_SQLI, SKILL_WEB_MANUAL prompts)

Mirror of Task 2 but for the 3 web skills, which live in the shared `_skills.py` module (not in `webpentest.py`).

- [ ] **Step 1: Confirm the failing test from Task 3**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_webpentest.py::test_webpentest_augmented_skills_mention_hypothesis_crud -v
```

Expected: FAIL on `assert "kb_add_hypothesis" in skills_by_key["r"].prompt`.

- [ ] **Step 2: Augment SKILL_WEB_RECON**

Edit `src/reverser/profiles/_skills.py`. Find `SKILL_WEB_RECON` (around line 114):

```python
SKILL_WEB_RECON = Skill(
    name="Web Recon",
    key="r",
    description="Reconnaissance: subdomain enum, port scan, fingerprinting, WAF detection",
    prompt="Perform web reconnaissance on the target. Run in parallel: nmap_scan for open "
           "ports, subfinder_enum for subdomains, whatweb_fingerprint for technology stack, "
           "and wafw00f_detect for WAF detection. Summarize the attack surface.",
)
```

Replace with the augmented version:

```python
SKILL_WEB_RECON = Skill(
    name="Web Recon",
    key="r",
    description="Reconnaissance: subdomain enum, port scan, fingerprinting, WAF detection",
    prompt="Perform web reconnaissance on the target. Run in parallel: nmap_scan for open "
           "ports, subfinder_enum for subdomains, whatweb_fingerprint for technology stack, "
           "and wafw00f_detect for WAF detection. Summarize the attack surface.\n\n"
           "After recon completes, propose 3 root hypotheses about the most exploitable "
           "surface. Call kb_add_hypothesis for each one — a hypothesis is a falsifiable "
           "CLAIM (\"The /api/users endpoint is vulnerable to IDOR via the id parameter\"), "
           "not a TODO item (\"Look at the API\"). Confidence values: 80+ = strong "
           "evidence, 50 = plausible, <30 = long shot. Subsequent skills work through "
           "these in confidence order.",
)
```

- [ ] **Step 3: Augment SKILL_WEB_SQLI**

Find `SKILL_WEB_SQLI` (around line 148):

```python
SKILL_WEB_SQLI = Skill(
    name="SQLi Test",
    key="q",
    description="Test for SQL injection vulnerabilities",
    prompt="Test the target for SQL injection. Start by using http_request to identify "
           "forms and parameters, then use sqlmap_test on promising endpoints. Report findings.",
)
```

Replace with:

```python
SKILL_WEB_SQLI = Skill(
    name="SQLi Test",
    key="q",
    description="Test for SQL injection vulnerabilities",
    prompt="Test the target for SQL injection. Start by using http_request to identify "
           "forms and parameters, then use sqlmap_test on promising endpoints. Report findings.\n\n"
           "Before running, call kb_list_hypotheses status=proposed and pick the "
           "highest-confidence unconfirmed one mentioning SQLi / injection. State the "
           "hypothesis OUT LOUD. After sqlmap_test returns, call "
           "kb_update_hypothesis(id=X, status=...) with confirmed/refuted/inconclusive "
           "plus a one-line outcome. Five-failure pivot: if you've made 5 failed SQLi "
           "attempts against this hypothesis, mark it refuted, STOP, and propose three "
           "orthogonal hypotheses (different endpoint, different injection class like "
           "SSRF or auth bypass, or different auth tier).",
)
```

- [ ] **Step 4: Augment SKILL_WEB_MANUAL**

Find `SKILL_WEB_MANUAL` (around line 156):

```python
SKILL_WEB_MANUAL = Skill(
    name="Manual Test",
    key="m",
    description="Manual HTTP probing: headers, cookies, auth, CORS",
    prompt="Perform manual HTTP testing on the target. Use http_request to: check security "
           "headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, etc.), test CORS "
           "configuration, examine cookies (HttpOnly, Secure, SameSite flags), check for "
           "information disclosure in headers and error pages. Report all findings.",
)
```

Replace with:

```python
SKILL_WEB_MANUAL = Skill(
    name="Manual Test",
    key="m",
    description="Manual HTTP probing: headers, cookies, auth, CORS",
    prompt="Perform manual HTTP testing on the target. Use http_request to: check security "
           "headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, etc.), test CORS "
           "configuration, examine cookies (HttpOnly, Secure, SameSite flags), check for "
           "information disclosure in headers and error pages. Report all findings.\n\n"
           "Manual probing is the second-most-common place to grind past usefulness "
           "(after fuzzing). Anchor every probe to a hypothesis: before checking a "
           "header / cookie / CORS / form, state which hypothesis you're testing. After "
           "5 failed manual probes against the same hypothesis (5 different "
           "headers/cookies/parameters that all came back clean), mark the hypothesis "
           "refuted via kb_update_hypothesis and pivot.",
)
```

- [ ] **Step 5: Run tests + commit**

Run all webpentest tests:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_webpentest.py -v
```

Expected: 7/7 PASS.

Also re-run pentest tests to confirm Task 2's work isn't broken:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py -v
```

Expected: 7/7 PASS.

Also re-run AD profile tests to confirm we haven't broken the precedent profile:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_ad.py -v
```

Expected: 7/7 PASS (existing AD tests untouched).

Commit:
```bash
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts add src/reverser/profiles/_skills.py
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts commit -m "feat(profiles): webpentest skill augmentations (WEB_RECON/WEB_SQLI/WEB_MANUAL)"
```

---

## Task 5: CAPABILITY_ROADMAP.md updates

**Files:**
- Modify: `CAPABILITY_ROADMAP.md`

Per spec §9 (D9). Two checkbox flips + snapshot-line bump. Doc-only — no tests; verify via grep.

- [ ] **Step 1: Flip Top 5 #5**

Find this in `CAPABILITY_ROADMAP.md` (currently around line 219):

```markdown
- [ ] **5. (was #5) — Hypothesis-driven pentest/webpentest prompts.**
  Restructure `pentest` and `webpentest` profile system prompts around
  hypothesis → cheap experiment → update → pivot. Reuse the
  `hypotheses` KB table (already shipped with manager profile) for
  storage. Add explicit "stop spraying, propose three new attack surfaces"
  trigger after K failed exploitation attempts (the 10.13.38.23 report's
  failure mode). Small implementation, big behavior change.
```

Replace with:

```markdown
- [x] **5. (was #5) — Hypothesis-driven pentest/webpentest prompts.**
  - **Status (2026-05-12):** Shipped via this work. Hypothesis-loop block
    inserted into `pentest` (K=3, every-5-calls) and `webpentest` (K=5,
    every-8-calls) system addenda. Skill-level reinforcement added to RECON,
    EXPLOIT, CREDS (pentest) and WEB_RECON, WEB_SQLI, WEB_MANUAL (webpentest).
    Specs/plans: `2026-05-12-hypothesis-driven-prompts-{design,plan}.md`.
```

- [ ] **Step 2: Flip Cross-Cutting "Failure analysis trigger"**

Find this in `CAPABILITY_ROADMAP.md` (currently around line 175):

```markdown
- [ ] Failure analysis trigger: after K failed exploit attempts, force "stop, summarize, propose orthogonal directions"
```

Replace with:

```markdown
- [x] Failure analysis trigger: after K failed exploit attempts, force "stop, summarize, propose orthogonal directions"
  - **Status (2026-05-12):** Shipped. After K failed exploit attempts against
    a hypothesis, the pentest (K=3) and webpentest (K=5) profile prompts now
    require mark-refuted + propose-three-orthogonal-surfaces. AD profile
    already had this discipline; manager profile achieves the same end via
    `dispatch_specialist`. Other profiles (general/linux/windows/etc.) are
    out of scope — they don't have an exploit-attempt surface.
```

- [ ] **Step 3: Bump the snapshot line**

Find the existing snapshot line (around line 15):

```markdown
**As of 2026-05-12:** 15 profiles registered, 77 MCP tools (75 unique), Claude
+ Ollama + LM Studio backends, per-target SQLite KB, session stop/resume,
manager profile (sub-agent coordination), exploit profile + msfrpc bridge,
524 passing tests.
```

Replace with:

```markdown
**As of 2026-05-12:** 15 profiles registered, 77 MCP tools (75 unique), Claude
+ Ollama + LM Studio backends, per-target SQLite KB, session stop/resume,
manager profile (sub-agent coordination), exploit profile + msfrpc bridge,
hypothesis-driven discipline in pentest/webpentest, 538 passing tests.
```

(Note: date unchanged at 2026-05-12; the line already had today's date from the earlier roadmap refresh that landed with the metasploit-bridge merge.)

- [ ] **Step 4: Verify with grep**

Run:
```
grep -nE "^- \[x\] \*\*5\.|^- \[x\] Failure analysis trigger|^\*\*As of 2026-05-12:" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/CAPABILITY_ROADMAP.md
```

Expected output: 3 matches, one for each edit. If you see `[ ]` (unchecked) for either Top 5 #5 or Failure analysis trigger, the edit didn't take.

Also verify the "Remaining work order" line at the bottom of the Top 5 section needs updating:

```
grep -n "Remaining work order" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/CAPABILITY_ROADMAP.md
```

It currently says `#3 → #5 (items #1, #2, and #4 already complete)`. Edit to:

```
> **Remaining work order:** #3 (items #1, #2, #4, and #5 already complete).
```

- [ ] **Step 5: Commit**

```bash
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts add CAPABILITY_ROADMAP.md
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts commit -m "docs(roadmap): mark Top 5 #5 + Failure analysis trigger shipped"
```

---

## Task 6: Final validation

**Files:**
- None (validation only)

Confirms the full suite passes and the change-set summary matches the spec's expectations.

- [ ] **Step 1: Run the full test suite**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest -q 2>&1 | tail -10
```

Expected: `538 passed, 1 skipped` (or `538 passed, 1 skipped in N.NNs`). 14 new tests on top of the 524 baseline.

If the count is different:
- 538 expected from 524 baseline + 14 new (7 pentest + 7 webpentest).
- If you see 537 or fewer, one of the new tests didn't get added.
- If you see 525-537 with failures, something regressed.

- [ ] **Step 2: Confirm profile-test files specifically**

Run:
```
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_pentest.py /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_webpentest.py /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts/tests/test_profiles_ad.py -v 2>&1 | tail -25
```

Expected: All tests pass. 7 + 7 + 7 = 21 profile-specific tests (AD existing + pentest new + webpentest new).

- [ ] **Step 3: Confirm the diff against main**

Run:
```
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts diff --stat main
```

Expected diff:
- `CAPABILITY_ROADMAP.md` — ~15 lines changed
- `src/reverser/profiles/_skills.py` — ~30 lines changed (3 skills augmented)
- `src/reverser/profiles/pentest.py` — ~70 lines changed (3 skills + addendum block)
- `src/reverser/profiles/webpentest.py` — ~50 lines changed (addendum block)
- `tests/test_profiles_pentest.py` — new file, ~55 lines
- `tests/test_profiles_webpentest.py` — new file, ~55 lines

Total: ~275 lines added/changed across 6 files. If the diff stat differs significantly (e.g. >500 lines or fewer than 200), check what was touched.

- [ ] **Step 4: List the commits**

Run:
```
git -C /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.worktrees/hypothesis-driven-prompts log --oneline main..HEAD
```

Expected: 5 commits (Tasks 1-5 each landed one):
- `feat(profiles): pentest hypothesis-loop block + test file`
- `feat(profiles): pentest skill augmentations (RECON/EXPLOIT/CREDS)`
- `feat(profiles): webpentest hypothesis-loop block + test file`
- `feat(profiles): webpentest skill augmentations (WEB_RECON/WEB_SQLI/WEB_MANUAL)`
- `docs(roadmap): mark Top 5 #5 + Failure analysis trigger shipped`

- [ ] **Step 5: (no further commits — task 6 is validation only)**

No commit step. The implementation is complete. Per the subagent-driven-development workflow, the next step is to invoke `superpowers:finishing-a-development-branch` to land the work.

Roadmap status update happens automatically because Task 5 already flipped the checkboxes.

---

## Plan complete — handoff

After Task 6 passes:

1. Optionally: spawn a code-review subagent for the diff against `main`.
2. Use `superpowers:finishing-a-development-branch` skill to merge / push / discard.
