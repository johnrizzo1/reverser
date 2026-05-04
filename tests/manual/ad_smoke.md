# Manual smoke test — Active Directory engagement (~30 min)

This walkthrough exercises the full AD capability stack (Plans 1–5) end-to-end against a
known-stable HackTheBox AD lab box. **Do not skip this before declaring the AD feature
complete.** Each numbered step lists the command the LLM should issue and the expected KB
state immediately after — verify both before moving on.

**Recommended boxes (any of):**
- HTB Forest — easiest; AS-REP roastable user, kerberoastable svc account
- HTB Sauna — easy; AS-REP roastable user, AutoLogon credentials in registry
- HTB Active — easy; readable Groups.xml on SYSVOL with cpassword

**Prerequisites (do these BEFORE Step 1):**
- VPN connected; lab box pingable.
- `REVERSER_PENTEST_AUTHORIZED=1` exported in shell.
- `REVERSER_AD_ALLOW_SPRAY=1` exported (only needed for Step 3 if you choose to test spray).
- `direnv` reloaded so `nxc`, `neo4j`, and `bloodhound-python` resolve on PATH.
- Working directory at the repo root (so `targets/` is created here).

---

## Step 1 — Launch reverser with the `ad` profile

Command:
```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p ad <BOX_IP>
```

Expected:
- TUI opens with "Active Directory" profile selected (top bar).
- F1 menu lists 11 AD skills.
- The system prompt section shown via `?` mentions "assumed-breach", "hypothesis-driven loop",
  and the new tool surface (`netexec_*`, `bloodhound_*`, `kb_*`).

KB state: empty (no `targets/<BOX_IP>/` directory yet).

---

## Step 2 — Initial recon

Trigger: F1 → "Initial recon" (or type the prompt manually).

Expected tool calls (in parallel where possible):
- `nmap_scan` with version detection on top-1000 TCP ports.
- `smb_enum` for shares + SMB security mode.
- `ldap_search` with anonymous bind for the root DSE.
- `nbtscan_scan` for NetBIOS names.

KB state after:
- `targets/<BOX_IP>/state.db` exists.
- `kb_list_hosts` returns at least one host (the box IP).
- `kb_list_services` returns at least: 53/tcp (DNS), 88/tcp (Kerberos), 135/tcp (RPC),
  139/tcp (NetBIOS), 389/tcp (LDAP), 445/tcp (SMB), 464/tcp (kpasswd), 593/tcp (RPC over HTTP),
  636/tcp (LDAPS), 3268/tcp (Global Catalog), 3269/tcp (LDAPS GC).
- `targets/<BOX_IP>/state.db` has a populated `services` table; `kb_show` renders without errors.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT host_ip, port, service FROM services ORDER BY port"
```

---

## Step 3 — Identify the Domain Controller and domain name

Trigger: F1 → "Identify DCs".

Expected tool calls:
- `kerberos_enum` action=`userenum` (uses nmap krb5-enum-users).
- `ldap_search` filter `(objectClass=domainDNS)` and the SERVER_TRUST_ACCOUNT bit-test filter.

KB state after:
- The host now has `is_dc=1` in the `hosts` table.
- The `targets` table has the discovered domain in the `domain` column (or a note records it).
- `kb_list_hosts` shows is_dc=True.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT ip, hostname, os, domain, is_dc FROM hosts"
```

If `is_dc` is still 0 here, the LDAP parser missed the SERVER_TRUST_ACCOUNT flag — file a bug.

---

## Step 4 — AS-REP roast

Trigger: F1 → "AS-REP roast".

Expected tool calls:
- `ldap_search` with the DONT_REQ_PREAUTH filter to harvest a userlist (anon LDAP only).
- `kerberos_enum` action=`asreproast` against the DC, supplying the userlist.

KB state after:
- For each AS-REP-roastable user, a row appears in the `credentials` table with:
  - `username` set
  - `kerberos_ticket` populated (the `$krb5asrep$23$...` blob)
  - `status = 'untested'`
  - `source_tool = 'kerberos_enum'`
- An entry in the `artifacts` table with `kind = 'asreproast_hashes'` pointing at a file
  under `targets/<BOX_IP>/loot/`.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT username, substr(kerberos_ticket, 1, 30), status FROM credentials WHERE kerberos_ticket IS NOT NULL"
sqlite3 targets/<BOX_IP>/state.db "SELECT kind, path FROM artifacts"
```

For HTB Forest, you should see at least `svc-alfresco`. For Sauna, `fsmith` (after manual user
discovery from the website).
