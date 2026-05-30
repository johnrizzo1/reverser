"""Network reconnaissance tools for IP-targeted penetration testing."""

import os
import shlex
import shutil

from claude_agent_sdk import tool

from ._common import arun_cmd, format_tool_result, format_error, cmd_result_to_tool_result, get_sudo_password


# ── Wordlist discovery ──────────────────────────────────────────────

_SECLISTS_SEARCH_PATHS = [
    # Explicit env var (set by devenv.nix)
    os.environ.get("SECLISTS_PATH", ""),
    # devenv / NixOS profile
    os.path.join(os.environ.get("DEVENV_PROFILE", ""), "share", "wordlists", "seclists"),
    # Common Linux locations
    "/usr/share/seclists",
    "/usr/share/wordlists/seclists",
    "/opt/seclists",
    # Home directory
    os.path.expanduser("~/wordlists/seclists"),
    os.path.expanduser("~/SecLists"),
]


def _find_seclists() -> str | None:
    """Find the seclists base directory."""
    for path in _SECLISTS_SEARCH_PATHS:
        if path and os.path.isdir(path):
            return path
    return None


def _find_wordlist(name: str) -> str | None:
    """Resolve a wordlist name to an absolute path.

    Tries the literal path first, then searches seclists.
    Common shortcuts:
      'common.txt'     -> Discovery/Web-Content/common.txt
      'big.txt'        -> Discovery/Web-Content/big.txt
      'rockyou.txt'    -> Passwords/Leaked-Databases/rockyou.txt (if present)
      'usernames.txt'  -> Usernames/top-usernames-shortlist.txt
    """
    # Already an absolute path that exists
    if os.path.isfile(name):
        return name

    seclists = _find_seclists()
    if not seclists:
        return None

    # Try as relative path within seclists
    candidate = os.path.join(seclists, name)
    if os.path.isfile(candidate):
        return candidate

    # Common shortcuts
    shortcuts = {
        "common.txt": "Discovery/Web-Content/common.txt",
        "big.txt": "Discovery/Web-Content/big.txt",
        "directory-list-2.3-medium.txt": "Discovery/Web-Content/directory-list-2.3-medium.txt",
        "directory-list-2.3-small.txt": "Discovery/Web-Content/directory-list-2.3-small.txt",
        "rockyou.txt": "Passwords/Leaked-Databases/rockyou.txt",
        "darkc0de.txt": "Passwords/darkc0de.txt",
        "usernames.txt": "Usernames/top-usernames-shortlist.txt",
        "top-usernames-shortlist.txt": "Usernames/top-usernames-shortlist.txt",
        "xato-net-10-million-usernames.txt": "Usernames/xato-net-10-million-usernames.txt",
        "names.txt": "Usernames/Names/names.txt",
    }
    if name in shortcuts:
        candidate = os.path.join(seclists, shortcuts[name])
        if os.path.isfile(candidate):
            return candidate

    # Walk seclists looking for the filename
    for root, _dirs, files in os.walk(seclists):
        if name in files:
            return os.path.join(root, name)

    return None


def _resolve_wordlist(requested: str, default_shortcut: str) -> tuple[str, str | None]:
    """Resolve a wordlist path. Returns (resolved_path, error_message).

    If resolution fails, returns a helpful error with available paths.
    """
    path = _find_wordlist(requested) if requested else _find_wordlist(default_shortcut)
    if path:
        return path, None

    seclists = _find_seclists()
    if seclists:
        err = (
            f"Wordlist not found: {requested or default_shortcut}\n"
            f"Seclists is at: {seclists}\n"
            f"Use a path relative to seclists (e.g. 'Discovery/Web-Content/common.txt') "
            f"or an absolute path."
        )
    else:
        err = (
            f"Wordlist not found: {requested or default_shortcut}\n"
            f"Seclists not found. Install the seclists package or provide an absolute path.\n"
            f"Searched: {', '.join(p for p in _SECLISTS_SEARCH_PATHS if p)}"
        )
    return "", err


# ── Sudo helper ─────────────────────────────────────────────────────

async def _run_sudo_cmd(cmd: list[str], use_sudo: bool, **kwargs) -> dict:
    """Run a command, optionally with sudo -S piping the stored password via stdin."""
    if not use_sudo:
        return await arun_cmd(cmd, **kwargs)

    password = get_sudo_password()
    if password is not None:
        # Use sudo -S to read password from stdin
        full_cmd = ["sudo", "-S"] + cmd
        result = await arun_cmd(full_cmd, stdin_data=password + "\n", **kwargs)
        # Strip the sudo password prompt from stderr (noise from -S)
        if result.get("stderr"):
            lines = result["stderr"].split("\n")
            result["stderr"] = "\n".join(
                line for line in lines
                if not line.startswith("[sudo]") and "Password:" not in line
            ).strip()
        return result
    else:
        # No stored password — try plain sudo (will fail without tty, but
        # the error message will tell the user to set the password via /sudo)
        full_cmd = ["sudo"] + cmd
        result = await arun_cmd(full_cmd, **kwargs)
        if result["returncode"] != 0 and "password" in result.get("stderr", "").lower():
            result["stderr"] = (
                "sudo requires a password but none is configured. "
                "Use the /sudo command in the TUI to enter your password."
            )
        return result


# ── Network tools ───────────────────────────────────────────────────

@tool(
    "nmap_scan",
    "Run an nmap scan against a target IP or hostname. Supports various scan types "
    "including TCP SYN, service/version detection, OS detection, and scripted scans. "
    "This is the primary reconnaissance tool for network targets. "
    "Automatically uses sudo for operations that require root privileges.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP address or hostname"},
            "ports": {
                "type": "string",
                "description": "Port specification (e.g. '80,443', '1-1024', '-' for all, default: top 1000)",
                "default": "",
            },
            "scan_type": {
                "type": "string",
                "description": "Scan type: tcp (SYN), connect, udp, version, aggressive",
                "default": "tcp",
                "enum": ["tcp", "connect", "udp", "version", "aggressive"],
            },
            "scripts": {
                "type": "string",
                "description": (
                    "NSE scripts to run. ONLY use real nmap script names. "
                    "Valid examples: 'vuln', 'default', 'smb-enum-shares', 'smb-os-discovery', "
                    "'smb-enum-users', 'ssl-enum-ciphers', 'ssl-cert', 'http-enum', "
                    "'http-title', 'http-headers', 'krb5-enum-users', 'ldap-rootdse', "
                    "'ldap-search', 'dns-zone-transfer', 'ftp-anon', 'smtp-commands'. "
                    "Do NOT invent script names."
                ),
                "default": "",
            },
            "script_args": {
                "type": "string",
                "description": "NSE script arguments (e.g. 'krb5-enum-users.realm=CORP.LOCAL,userdb=/path/to/users.txt')",
                "default": "",
            },
            "extra_args": {
                "type": "string",
                "description": "Additional nmap flags (e.g. '-O' for OS detection, '--reason', '-Pn' to skip ping)",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def nmap_scan(args: dict) -> dict:
    target = args["target"]
    ports = args.get("ports", "")
    scan_type = args.get("scan_type", "tcp")
    scripts = args.get("scripts", "")
    script_args = args.get("script_args", "")
    extra_args = args.get("extra_args", "")

    cmd = ["nmap"]

    # Determine if sudo is needed: SYN, UDP, aggressive, OS detection,
    # version detection, and NSE scripts all require raw sockets / root
    needs_root = (
        scan_type in ("tcp", "udp", "aggressive")
        or scripts
        or "-O" in extra_args
        or "-sV" in extra_args
        or "-sS" in extra_args
        or "-sU" in extra_args
        or "--script" in extra_args
    )

    if scan_type == "tcp":
        cmd.append("-sS")
    elif scan_type == "connect":
        cmd.append("-sT")
    elif scan_type == "udp":
        cmd.append("-sU")
    elif scan_type == "version":
        cmd.extend(["-sV", "--version-intensity", "5"])
        needs_root = True
    elif scan_type == "aggressive":
        cmd.append("-A")

    if ports:
        cmd.extend(["-p", ports])

    if scripts:
        cmd.extend(["--script", scripts])

    if script_args:
        cmd.extend(["--script-args", script_args])

    if extra_args:
        cmd.extend(extra_args.split())

    cmd.append(target)

    result = await _run_sudo_cmd(cmd, needs_root, timeout=120, max_output=16000)

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nmap_output
        kb = for_target(target)
        for nmap_host in parse_nmap_output(result["stdout"]):
            kb.record_host(nmap_host.host)
            for svc in nmap_host.services:
                kb.record_service(svc)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nmap_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────

    return cmd_result_to_tool_result(result)


@tool(
    "nikto_scan",
    "Run a Nikto web server vulnerability scanner against a target. Checks for "
    "dangerous files, outdated server software, and common misconfigurations.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target URL or IP (e.g. 'http://10.0.0.1' or '10.0.0.1')"},
            "port": {
                "type": "integer",
                "description": "Port to scan (default: 80)",
                "default": 80,
            },
            "ssl": {
                "type": "boolean",
                "description": "Force SSL/TLS connection",
                "default": False,
            },
            "tuning": {
                "type": "string",
                "description": "Scan tuning: 1=files, 2=misconfig, 3=info, 4=XSS, 5=RFI, 6=DoS checks (skip), 7=RCE, 8=SQLi, 9=upload, 0=file upload",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def nikto_scan(args: dict) -> dict:
    target = args["target"]
    port = args.get("port", 80)
    ssl = args.get("ssl", False)
    tuning = args.get("tuning", "")

    cmd = ["nikto", "-h", target, "-p", str(port)]

    if ssl:
        cmd.append("-ssl")

    if tuning:
        cmd.extend(["-Tuning", tuning])

    # Nikto can be slow; give it 3 minutes
    result = await arun_cmd(cmd, timeout=180, max_output=16000)
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nikto_findings
        from ..gui_service.kb_emitter import emit_recorded_finding
        kb = for_target(target)
        for finding in parse_nikto_findings(result["stdout"]):
            fid = kb.record_finding(finding)
            emit_recorded_finding("create", fid, finding)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nikto_scan (network): %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


@tool(
    "gobuster_scan",
    "Run Gobuster for directory and file brute-forcing on a web server. "
    "Discovers hidden paths, admin panels, backup files, and API endpoints. "
    "Wordlists are auto-resolved from seclists if installed.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target URL (e.g. 'http://10.0.0.1')"},
            "mode": {
                "type": "string",
                "description": "Scan mode: dir (directory brute-force), dns (subdomain enum), vhost",
                "default": "dir",
                "enum": ["dir", "dns", "vhost"],
            },
            "wordlist": {
                "type": "string",
                "description": (
                    "Wordlist path or shortcut name. Shortcuts: 'common.txt', 'big.txt', "
                    "'directory-list-2.3-medium.txt'. Or a path relative to seclists "
                    "(e.g. 'Discovery/Web-Content/common.txt'). Default: common.txt"
                ),
                "default": "common.txt",
            },
            "extensions": {
                "type": "string",
                "description": "File extensions to check (e.g. 'php,html,txt,bak')",
                "default": "",
            },
            "extra_args": {
                "type": "string",
                "description": "Additional gobuster flags",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def gobuster_scan(args: dict) -> dict:
    target = args["target"]
    mode = args.get("mode", "dir")
    wordlist_name = args.get("wordlist", "common.txt")
    extensions = args.get("extensions", "")
    extra_args = args.get("extra_args", "")

    wordlist, err = _resolve_wordlist(wordlist_name, "common.txt")
    if err:
        return format_error(err)

    cmd = ["gobuster", mode, "-u", target, "-w", wordlist, "--no-color"]

    if extensions and mode == "dir":
        cmd.extend(["-x", extensions])

    if extra_args:
        cmd.extend(extra_args.split())

    result = await arun_cmd(cmd, timeout=180, max_output=16000)
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        import json
        from pathlib import Path
        from ..kb import for_target, ArtifactFact
        from ..kb.parsers import parse_gobuster_paths
        kb = for_target(target)
        paths = parse_gobuster_paths(result["stdout"])
        if paths:
            artifact_path = str(kb.root / "loot" / "gobuster_paths.json")
            Path(artifact_path).write_text(json.dumps(paths, indent=2))
            kb.record_artifact(ArtifactFact(
                kind="discovered_paths",
                path=artifact_path,
                source_tool="gobuster_scan",
            ))
            kb.record_note(
                f"gobuster {target}: discovered {len(paths)} paths — "
                + ", ".join(paths[:10])
                + (" ..." if len(paths) > 10 else "")
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in gobuster_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


@tool(
    "curl_request",
    "Make HTTP requests to a target. Use for banner grabbing, API probing, "
    "testing endpoints, checking headers, and general web reconnaissance.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to request"},
            "method": {
                "type": "string",
                "description": "HTTP method (GET, POST, PUT, DELETE, HEAD, OPTIONS)",
                "default": "GET",
            },
            "headers": {
                "type": "string",
                "description": "Custom headers as 'Key: Value' separated by newlines",
                "default": "",
            },
            "data": {
                "type": "string",
                "description": "Request body data",
                "default": "",
            },
            "include_headers": {
                "type": "boolean",
                "description": "Include response headers in output",
                "default": True,
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Follow HTTP redirects",
                "default": True,
            },
            "insecure": {
                "type": "boolean",
                "description": "Skip TLS certificate verification",
                "default": True,
            },
        },
        "required": ["url"],
    },
)
async def curl_request(args: dict) -> dict:
    url = args["url"]
    method = args.get("method", "GET")
    headers = args.get("headers", "")
    data = args.get("data", "")
    include_headers = args.get("include_headers", True)
    follow_redirects = args.get("follow_redirects", True)
    insecure = args.get("insecure", True)

    cmd = ["curl", "-s", "-X", method, "--max-time", "30"]

    if include_headers:
        cmd.append("-i")
    if follow_redirects:
        cmd.append("-L")
    if insecure:
        cmd.append("-k")

    if headers:
        for header in headers.strip().split("\n"):
            header = header.strip()
            if header:
                cmd.extend(["-H", header])

    if data:
        cmd.extend(["-d", data])

    cmd.append(url)

    result = await arun_cmd(cmd, timeout=30, max_output=16000)
    return cmd_result_to_tool_result(result)


@tool(
    "ssl_scan",
    "Analyze SSL/TLS configuration of a target. Checks certificate details, "
    "supported protocols, cipher suites, and common TLS vulnerabilities.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target hostname or IP"},
            "port": {
                "type": "integer",
                "description": "Port to scan (default: 443)",
                "default": 443,
            },
        },
        "required": ["target"],
    },
)
async def ssl_scan(args: dict) -> dict:
    target = args["target"]
    port = args.get("port", 443)

    # Try sslscan first, fall back to nmap ssl scripts
    result = await arun_cmd(["sslscan", "--no-colour", f"{target}:{port}"], timeout=60, max_output=16000)

    if result["returncode"] != 0 and "not found" in result["stderr"].lower():
        # Fall back to nmap SSL scripts
        result = await _run_sudo_cmd(
            ["nmap", "-sV", "--script", "ssl-enum-ciphers,ssl-cert", "-p", str(port), target],
            True, timeout=60, max_output=16000,
        )

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_ssl_findings
        from ..gui_service.kb_emitter import emit_recorded_finding
        kb = for_target(target)
        out = parse_ssl_findings(result["stdout"])
        for f in out["findings"]:
            fid = kb.record_finding(f)
            emit_recorded_finding("create", fid, f)
        if out["note"]:
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in ssl_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


@tool(
    "whatweb_scan",
    "Identify web technologies used by a target — CMS, frameworks, server software, "
    "JavaScript libraries, analytics tools, and more.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target URL or IP"},
            "aggression": {
                "type": "integer",
                "description": "Aggression level: 1=stealthy, 3=aggressive (default: 1)",
                "default": 1,
            },
        },
        "required": ["target"],
    },
)
async def whatweb_scan(args: dict) -> dict:
    target = args["target"]
    aggression = args.get("aggression", 1)

    cmd = ["whatweb", "--color=never", f"-a{aggression}", target]
    result = await arun_cmd(cmd, timeout=60, max_output=16000)

    # Detect Ruby env breakage (getoptlong removed from stdlib in Ruby 3.3+;
    # nixpkgs whatweb's wrapper doesn't load the gem). Delegate to
    # whatweb_fingerprint which has a curl-based fallback so the agent gets
    # useful output instead of a Ruby LoadError.
    combined_output = (result.get("stderr") or "") + (result.get("stdout") or "")
    if result["returncode"] != 0 and (
        "LoadError" in combined_output or "getoptlong" in combined_output
    ):
        from .web import whatweb_fingerprint
        handler = getattr(whatweb_fingerprint, "handler", None) or whatweb_fingerprint
        return await handler({"target": target, "aggression": aggression})

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from urllib.parse import urlparse
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_whatweb_plugins
        parsed_url = urlparse(target if "://" in target else f"http://{target}")
        host_ip = parsed_url.hostname or target
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        kb = for_target(target)
        out = parse_whatweb_plugins(result["stdout"], host_ip=host_ip, port=port)
        if out.get("service"):
            kb.record_host(HostFact(ip=host_ip))
            kb.record_service(out["service"])
        if out.get("note"):
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in whatweb_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


@tool(
    "dns_recon",
    "Perform DNS reconnaissance on a target. Enumerates DNS records, checks zone "
    "transfers, and resolves hostnames.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target domain or IP address"},
            "record_type": {
                "type": "string",
                "description": "DNS record type to query (A, AAAA, MX, NS, TXT, CNAME, SOA, ANY)",
                "default": "ANY",
            },
            "reverse": {
                "type": "boolean",
                "description": "Perform reverse DNS lookup on IP",
                "default": False,
            },
        },
        "required": ["target"],
    },
)
async def dns_recon(args: dict) -> dict:
    target = args["target"]
    record_type = args.get("record_type", "ANY")
    reverse = args.get("reverse", False)

    if reverse:
        cmd = ["dig", "-x", target, "+noall", "+answer"]
    else:
        cmd = ["dig", target, record_type, "+noall", "+answer", "+authority", "+additional"]

    result = await arun_cmd(cmd, timeout=15, max_output=8000)
    return cmd_result_to_tool_result(result)


@tool(
    "banner_grab",
    "Grab service banners from open ports using netcat. Useful for identifying "
    "services, versions, and potential attack surface.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP or hostname"},
            "port": {"type": "integer", "description": "Port to connect to"},
            "send_data": {
                "type": "string",
                "description": "Data to send (e.g. 'HEAD / HTTP/1.0\\r\\n\\r\\n' for HTTP)",
                "default": "",
            },
        },
        "required": ["target", "port"],
    },
)
async def banner_grab(args: dict) -> dict:
    target = args["target"]
    port = args["port"]
    send_data = args.get("send_data", "")

    cmd = ["bash", "-c"]
    if send_data:
        cmd.append(f"echo -e {repr(send_data)} | nc -w 5 {target} {port}")
    else:
        cmd.append(f"nc -w 5 {target} {port}")

    result = await arun_cmd(cmd, timeout=10, max_output=8000)

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_banner_first_line
        kb = for_target(target)
        svc = parse_banner_first_line(result["stdout"], host_ip=target, port=int(port))
        if svc is not None:
            kb.record_host(HostFact(ip=target))
            kb.record_service(svc)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in banner_grab: %s", e)
    # ───────────────────────────────────────────────────────────────────

    return cmd_result_to_tool_result(result)


@tool(
    "ldap_search",
    "Query an LDAP directory service using Python ldap3. Use for Active Directory "
    "enumeration — discover users, groups, computers, OUs, GPOs, SPNs, and domain trusts. "
    "Start with an anonymous bind to check if unauthenticated queries are allowed.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "LDAP server IP or hostname"},
            "base_dn": {
                "type": "string",
                "description": "Base DN for search (e.g. 'DC=corp,DC=local'). If empty, tries to auto-discover via rootDSE.",
                "default": "",
            },
            "filter": {
                "type": "string",
                "description": "LDAP search filter (e.g. '(objectClass=user)', '(sAMAccountName=admin*)', '(servicePrincipalName=*)')",
                "default": "(objectClass=*)",
            },
            "attributes": {
                "type": "string",
                "description": "Comma-separated attributes to return (e.g. 'cn,sAMAccountName,memberOf'). Empty = all.",
                "default": "",
            },
            "scope": {
                "type": "string",
                "description": "Search scope: BASE, LEVEL, SUBTREE (default: SUBTREE)",
                "default": "SUBTREE",
                "enum": ["BASE", "LEVEL", "SUBTREE"],
            },
            "username": {
                "type": "string",
                "description": "Username for authenticated bind (e.g. 'user@corp.local' or 'CORP\\\\user')",
                "default": "",
            },
            "password": {
                "type": "string",
                "description": "Password for authenticated bind",
                "default": "",
            },
            "port": {
                "type": "integer",
                "description": "LDAP port (default: 389, use 636 for LDAPS)",
                "default": 389,
            },
            "use_ssl": {
                "type": "boolean",
                "description": "Use LDAPS (SSL/TLS)",
                "default": False,
            },
            "size_limit": {
                "type": "integer",
                "description": "Maximum number of entries to return (default: 100)",
                "default": 100,
            },
        },
        "required": ["target"],
    },
)
async def ldap_search(args: dict) -> dict:
    target = args["target"]
    base_dn = args.get("base_dn", "")
    ldap_filter = args.get("filter", "(objectClass=*)")
    attributes_str = args.get("attributes", "")
    scope = args.get("scope", "SUBTREE")
    username = args.get("username", "")
    password = args.get("password", "")
    port = args.get("port", 389)
    use_ssl = args.get("use_ssl", False)
    size_limit = args.get("size_limit", 100)

    try:
        import ldap3
        from ldap3 import Server, Connection, ALL, SUBTREE, LEVEL, BASE, ANONYMOUS, SIMPLE
    except ImportError:
        return format_error(
            "Python ldap3 module is not installed. "
            "Add 'ldap3' to your Python requirements in devenv.nix."
        )

    scope_map = {"SUBTREE": SUBTREE, "LEVEL": LEVEL, "BASE": BASE}
    search_scope = scope_map.get(scope, SUBTREE)

    attributes = [a.strip() for a in attributes_str.split(",") if a.strip()] if attributes_str else ["*"]

    try:
        server = Server(target, port=port, use_ssl=use_ssl, get_info=ALL, connect_timeout=10)

        if username and password:
            conn = Connection(server, user=username, password=password, authentication=SIMPLE,
                              auto_bind=True, receive_timeout=15)
        else:
            conn = Connection(server, authentication=ANONYMOUS, auto_bind=True, receive_timeout=15)

        # Auto-discover base DN from server info if not provided
        if not base_dn:
            if server.info and server.info.naming_contexts:
                base_dn = str(server.info.naming_contexts[0])
            elif server.info and hasattr(server.info, 'other') and 'defaultNamingContext' in server.info.other:
                base_dn = str(server.info.other['defaultNamingContext'][0])
            else:
                # Try to get from rootDSE
                conn.search('', '(objectClass=*)', search_scope=BASE,
                            attributes=['defaultNamingContext', 'namingContexts'])
                if conn.entries:
                    entry = conn.entries[0]
                    if hasattr(entry, 'defaultNamingContext'):
                        base_dn = str(entry.defaultNamingContext)
                    elif hasattr(entry, 'namingContexts'):
                        base_dn = str(entry.namingContexts[0]) if entry.namingContexts else ""

            if not base_dn:
                # Return server info even if we can't find base DN
                info_text = f"Connected but could not auto-discover base DN.\n"
                if server.info:
                    info_text += f"Server info:\n{server.info}\n"
                info_text += "Provide base_dn explicitly (e.g. 'DC=corp,DC=local')."
                return format_tool_result(info_text)

        conn.search(
            base_dn,
            ldap_filter,
            search_scope=search_scope,
            attributes=attributes,
            size_limit=size_limit,
        )

        results = []
        for entry in conn.entries:
            results.append(str(entry))

        output = f"Search base: {base_dn}\nFilter: {ldap_filter}\nResults: {len(conn.entries)}\n\n"
        output += "\n---\n".join(results) if results else "(no results)"

        if len(conn.entries) >= size_limit:
            output += f"\n\n[Results limited to {size_limit} entries — increase size_limit for more]"

        conn.unbind()

        # ── KB write (new) ─────────────────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_ldap_entries
            kb = for_target(target)
            parsed = parse_ldap_entries(output)
            for h in parsed["hosts"]:
                kb.record_host(h)
            if parsed["note"]:
                kb.record_note(parsed["note"])
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("KB write failed in ldap_search: %s", e)
        # ───────────────────────────────────────────────────────────────────

        return format_tool_result(output)

    except ldap3.core.exceptions.LDAPBindError as e:
        return format_error(f"LDAP bind failed: {e}")
    except ldap3.core.exceptions.LDAPSocketOpenError as e:
        return format_error(f"Cannot connect to LDAP server {target}:{port}: {e}")
    except Exception as e:
        return format_error(f"LDAP error: {type(e).__name__}: {e}")


@tool(
    "smb_enum",
    "Enumerate SMB shares, users, and sessions on a target. Uses smbclient for share "
    "listing and nmap SMB scripts for deeper enumeration.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP or hostname"},
            "mode": {
                "type": "string",
                "description": "Enumeration mode: shares (list shares), scripts (nmap SMB scripts), all",
                "default": "all",
                "enum": ["shares", "scripts", "all"],
            },
            "username": {
                "type": "string",
                "description": "Username for authenticated enumeration (empty = anonymous/null session)",
                "default": "",
            },
            "password": {
                "type": "string",
                "description": "Password for authenticated enumeration",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def smb_enum(args: dict) -> dict:
    target = args["target"]
    mode = args.get("mode", "all")
    username = args.get("username", "")
    password = args.get("password", "")

    outputs = []

    if mode in ("shares", "all"):
        # Try smbclient for share listing
        smb_cmd = ["smbclient", "-L", target, "--no-pass"]
        if username:
            smb_cmd = ["smbclient", "-L", target, "-U", f"{username}%{password}"]

        result = await arun_cmd(smb_cmd, timeout=15, max_output=8000)
        if result["returncode"] != 0 and "not found" in result.get("stderr", "").lower():
            # smbclient not available, try nmap
            result = await _run_sudo_cmd(
                ["nmap", "-sV", "--script", "smb-enum-shares", "-p", "445", target],
                True, timeout=30, max_output=8000,
            )
        outputs.append(f"=== SMB Shares ===\n{result['stdout']}")
        if result.get("stderr") and result["returncode"] != 0:
            outputs.append(f"[stderr]: {result['stderr'][:500]}")

    if mode in ("scripts", "all"):
        # Run nmap SMB enumeration scripts
        nmap_result = await _run_sudo_cmd(
            ["nmap", "-sV", "--script",
             "smb-os-discovery,smb-enum-shares,smb-enum-users,smb-security-mode,smb2-security-mode",
             "-p", "139,445", target],
            True, timeout=60, max_output=8000,
        )
        outputs.append(f"=== Nmap SMB Scripts ===\n{nmap_result['stdout']}")
        if nmap_result.get("stderr") and nmap_result["returncode"] != 0:
            outputs.append(f"[stderr]: {nmap_result['stderr'][:500]}")

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_smbclient_shares, parse_nmap_smb_scripts
        kb = for_target(target)
        kb.record_host(HostFact(ip=target))
        joined = "\n\n".join(outputs)
        smb_out = parse_smbclient_shares(joined)
        if smb_out["host"].domain:
            kb.record_host(HostFact(ip=target, domain=smb_out["host"].domain))
        if smb_out["shares_note"]:
            kb.record_note(smb_out["shares_note"])
        nmap_out = parse_nmap_smb_scripts(joined)
        if nmap_out["host"].ip == target or nmap_out["host"].ip == "":
            merged = HostFact(
                ip=target,
                hostname=nmap_out["host"].hostname,
                os=nmap_out["host"].os,
                domain=nmap_out["host"].domain,
                smb_signing=nmap_out["host"].smb_signing,
            )
            kb.record_host(merged)
        for svc in nmap_out["services"]:
            kb.record_service(svc)
        if nmap_out["note"]:
            kb.record_note(nmap_out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in smb_enum: %s", e)
    # ───────────────────────────────────────────────────────────────────

    return format_tool_result("\n\n".join(outputs))


@tool(
    "nbtscan",
    "Scan a network for NetBIOS name information. Discovers Windows hostnames, "
    "workgroups/domains, MAC addresses, and logged-in users. Useful for mapping "
    "Windows networks and identifying domain controllers.",
    {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Target IP, hostname, or CIDR range (e.g. '192.168.1.0/24')",
            },
            "verbose": {
                "type": "boolean",
                "description": "Verbose output with full NetBIOS name table",
                "default": False,
            },
        },
        "required": ["target"],
    },
)
async def nbtscan_scan(args: dict) -> dict:
    target = args["target"]
    verbose = args.get("verbose", False)

    cmd = ["nbtscan"]
    if verbose:
        cmd.append("-v")
    cmd.append(target)

    result = await arun_cmd(cmd, timeout=60, max_output=16000)

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nbtscan_output
        kb = for_target(target)
        for host in parse_nbtscan_output(result["stdout"]):
            kb.record_host(host)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nbtscan_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────

    return cmd_result_to_tool_result(result)


@tool(
    "kerberos_enum",
    "Enumerate Kerberos users and test credentials against a domain controller. "
    "Uses impacket GetNPUsers.py for AS-REP roasting and GetUserSPNs.py for kerberoasting. "
    "Falls back to nmap krb5-enum-users script for user enumeration.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Domain controller IP or hostname"},
            "domain": {"type": "string", "description": "Target domain (e.g. 'corp.local')"},
            "mode": {
                "type": "string",
                "description": (
                    "Mode: userenum (enumerate valid usernames via nmap krb5-enum-users), "
                    "asreproast (find AS-REP roastable users via impacket GetNPUsers), "
                    "kerberoast (find kerberoastable SPNs via impacket GetUserSPNs)"
                ),
                "default": "userenum",
                "enum": ["userenum", "asreproast", "kerberoast"],
            },
            "userlist": {
                "type": "string",
                "description": (
                    "Path to username wordlist, or a shortcut name from seclists "
                    "(e.g. 'top-usernames-shortlist.txt', 'xato-net-10-million-usernames.txt'). "
                    "Required for userenum mode."
                ),
                "default": "",
            },
            "username": {
                "type": "string",
                "description": "Single username for targeted queries (or user@domain for authenticated modes)",
                "default": "",
            },
            "password": {
                "type": "string",
                "description": "Password for authenticated kerberoast queries",
                "default": "",
            },
        },
        "required": ["target", "domain"],
    },
)
async def kerberos_enum(args: dict) -> dict:
    target = args["target"]
    domain = args["domain"]
    mode = args.get("mode", "userenum")
    userlist = args.get("userlist", "")
    username = args.get("username", "")
    password = args.get("password", "")

    if mode == "userenum":
        # Use nmap krb5-enum-users (the correct script name)
        if not userlist and not username:
            return format_error(
                "userenum mode requires a userlist or username. "
                "Provide a wordlist path or shortcut name (e.g. 'top-usernames-shortlist.txt')."
            )

        if userlist:
            resolved, err = _resolve_wordlist(userlist, "top-usernames-shortlist.txt")
            if err:
                return format_error(err)

            nmap_cmd = [
                "nmap", "-p", "88", "--script", "krb5-enum-users",
                "--script-args", f"krb5-enum-users.realm={domain},userdb={resolved}",
                "-Pn", target,
            ]
            result = await _run_sudo_cmd(nmap_cmd, True, timeout=120, max_output=16000)
        else:
            # Single user check — create a temp file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(username + "\n")
                tmpfile = f.name
            try:
                nmap_cmd = [
                    "nmap", "-p", "88", "--script", "krb5-enum-users",
                    "--script-args", f"krb5-enum-users.realm={domain},userdb={tmpfile}",
                    "-Pn", target,
                ]
                result = await _run_sudo_cmd(nmap_cmd, True, timeout=30, max_output=16000)
            finally:
                os.unlink(tmpfile)

        return cmd_result_to_tool_result(result)

    elif mode == "asreproast":
        # Use impacket GetNPUsers.py
        cmd = ["python3", "-m", "impacket.examples.GetNPUsers",
               f"{domain}/", "-dc-ip", target, "-no-pass", "-format", "hashcat"]
        if userlist:
            resolved, err = _resolve_wordlist(userlist, "")
            if err:
                return format_error(err)
            cmd.extend(["-usersfile", resolved])
        elif username:
            # Replace the domain/ with domain/username
            cmd[3] = f"{domain}/{username}"
        else:
            # Try without specifying users (requires anonymous LDAP)
            cmd.append("-request")

        result = await arun_cmd(cmd, timeout=60, max_output=16000)

        # Fall back to script path if module invocation fails
        if result["returncode"] != 0 and "No module" in result.get("stderr", ""):
            getnp = shutil.which("GetNPUsers.py") or shutil.which("impacket-GetNPUsers")
            if getnp:
                cmd[0:3] = [getnp]
                result = await arun_cmd(cmd, timeout=60, max_output=16000)
            else:
                return format_error(
                    "impacket GetNPUsers not found. Install impacket: pip install impacket"
                )

        # ── KB write (new — asreproast) ────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_asreproast_hashes
            kb = for_target(target)
            for cred in parse_asreproast_hashes(result["stdout"]):
                kb.record_credential(cred)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "KB write failed in kerberos_enum/asreproast: %s", e)
        # ───────────────────────────────────────────────────────────────────

        return cmd_result_to_tool_result(result)

    elif mode == "kerberoast":
        # Use impacket GetUserSPNs.py
        if not username or not password:
            return format_error(
                "kerberoast mode requires valid credentials (username and password)."
            )

        cmd = ["python3", "-m", "impacket.examples.GetUserSPNs",
               f"{domain}/{username}:{password}", "-dc-ip", target, "-request"]

        result = await arun_cmd(cmd, timeout=60, max_output=16000)

        if result["returncode"] != 0 and "No module" in result.get("stderr", ""):
            getspn = shutil.which("GetUserSPNs.py") or shutil.which("impacket-GetUserSPNs")
            if getspn:
                cmd[0:3] = [getspn]
                result = await arun_cmd(cmd, timeout=60, max_output=16000)
            else:
                return format_error(
                    "impacket GetUserSPNs not found. Install impacket: pip install impacket"
                )

        # ── KB write (new — kerberoast) ────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_kerberoast_hashes
            kb = for_target(target)
            for cred in parse_kerberoast_hashes(result["stdout"]):
                kb.record_credential(cred)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "KB write failed in kerberos_enum/kerberoast: %s", e)
        # ───────────────────────────────────────────────────────────────────

        return cmd_result_to_tool_result(result)

    return format_error(f"Unknown mode: {mode}")


TOOLS = [
    nmap_scan, nikto_scan, gobuster_scan, curl_request,
    ssl_scan, whatweb_scan, dns_recon, banner_grab,
    ldap_search, smb_enum, nbtscan_scan, kerberos_enum,
]
