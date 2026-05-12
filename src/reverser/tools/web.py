"""Web application penetration testing tools."""

import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

from claude_agent_sdk import tool

from ._common import (
    DEFAULT_MAX_OUTPUT,
    WEB_TOOL_TIMEOUT,
    arun_cmd,
    check_web_authorized,
    cmd_result_to_tool_result,
    format_error,
    format_tool_result,
)


# ── Wordlist management ───────────────────────────────────────────

_WORDLIST_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "reverser", "wordlists")

_WORDLIST_URLS = {
    "common.txt": "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/common.txt",
    "directory-list-2.3-small.txt": "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/directory-list-2.3-small.txt",
}


async def _find_or_download_wordlist(name: str = "common.txt") -> str | None:
    """Find a wordlist on disk or download it from SecLists GitHub."""
    # Check common locations first
    seclists_base = os.environ.get("SECLISTS_PATH", "")
    candidates = []

    if seclists_base:
        candidates.append(os.path.join(seclists_base, "Discovery/Web-Content", name))

    candidates.extend([
        os.path.join(_WORDLIST_CACHE_DIR, name),
        f"/usr/share/wordlists/seclists/Discovery/Web-Content/{name}",
        f"/usr/share/seclists/Discovery/Web-Content/{name}",
        f"/usr/share/wordlists/dirb/{name}",
        os.path.expanduser(f"~/.local/share/seclists/Discovery/Web-Content/{name}"),
    ])

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    # Download from SecLists GitHub
    url = _WORDLIST_URLS.get(name)
    if not url:
        return None

    dest = os.path.join(_WORDLIST_CACHE_DIR, name)
    os.makedirs(_WORDLIST_CACHE_DIR, exist_ok=True)

    result = await arun_cmd(
        ["curl", "-s", "-S", "-L", "-o", dest, "--max-time", "30", url],
        timeout=35,
    )

    if result["returncode"] == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest

    # Clean up failed download
    if os.path.exists(dest):
        os.unlink(dest)
    return None


# ── HTTP request ──────────────────────────────────────────────────


@tool(
    "http_request",
    "Make an HTTP request with full control over method, headers, body, and cookies. "
    "Returns response headers and body. Use for manual probing, header inspection, "
    "XSS testing, SSRF detection, auth testing, etc.",
    {
        "url": {"type": "string", "description": "Target URL (e.g. https://example.com/api/users)"},
        "method": {"type": "string", "description": "HTTP method (GET, POST, PUT, DELETE, HEAD, OPTIONS, PATCH)", "default": "GET"},
        "headers": {"type": "object", "description": "Custom headers as key-value pairs", "default": {}},
        "data": {"type": "string", "description": "Request body (for POST/PUT/PATCH)"},
        "cookies": {"type": "string", "description": "Cookie string (e.g. 'session=abc123; token=xyz')"},
        "follow_redirects": {"type": "boolean", "description": "Follow HTTP redirects", "default": True},
        "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 15},
    },
)
async def http_request(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    url = args["url"]
    method = args.get("method", "GET").upper()
    headers = args.get("headers", {})
    data = args.get("data")
    cookies = args.get("cookies")
    follow = args.get("follow_redirects", True)
    timeout = args.get("timeout", 15)

    cmd = ["curl", "-s", "-S", "-D", "-", "-X", method]

    if not follow:
        pass  # curl follows by default only with -L
    else:
        cmd.append("-L")

    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])

    if cookies:
        cmd.extend(["-b", cookies])

    if data:
        cmd.extend(["-d", data])

    cmd.extend(["--max-time", str(timeout)])
    cmd.append(url)

    result = await arun_cmd(cmd, timeout=timeout + 5)
    return cmd_result_to_tool_result(result)


# ── Port scanning ─────────────────────────────────────────────────


def _parse_nmap_xml(xml_text: str) -> str:
    """Parse nmap XML output into a compact text summary."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text  # fallback to raw output

    lines = []
    for host in root.findall("host"):
        addr_el = host.find("address")
        addr = addr_el.get("addr", "?") if addr_el is not None else "?"
        status_el = host.find("status")
        status = status_el.get("state", "?") if status_el is not None else "?"
        lines.append(f"Host: {addr} ({status})")

        ports_el = host.find("ports")
        if ports_el is not None:
            for port in ports_el.findall("port"):
                portid = port.get("portid", "?")
                protocol = port.get("protocol", "?")
                state_el = port.find("state")
                state = state_el.get("state", "?") if state_el is not None else "?"
                svc_el = port.find("service")
                svc_name = svc_el.get("name", "") if svc_el is not None else ""
                svc_product = svc_el.get("product", "") if svc_el is not None else ""
                svc_version = svc_el.get("version", "") if svc_el is not None else ""
                svc_info = " ".join(filter(None, [svc_name, svc_product, svc_version]))
                lines.append(f"  {portid}/{protocol}  {state}  {svc_info}")

                # Script output
                for script in port.findall("script"):
                    script_id = script.get("id", "?")
                    script_out = script.get("output", "").strip()
                    if script_out:
                        lines.append(f"    |_{script_id}: {script_out[:200]}")

        # Host scripts
        hostscript = host.find("hostscript")
        if hostscript is not None:
            for script in hostscript.findall("script"):
                script_id = script.get("id", "?")
                script_out = script.get("output", "").strip()
                if script_out:
                    lines.append(f"  [host-script] {script_id}: {script_out[:300]}")

    return "\n".join(lines) if lines else xml_text


@tool(
    "nmap_scan",
    "Port scanning and service detection with nmap. Discovers open ports, identifies "
    "services and versions, and runs default scripts. Returns structured summary.",
    {
        "target": {"type": "string", "description": "Target host or IP (e.g. example.com, 192.168.1.1)"},
        "ports": {"type": "string", "description": "Port spec (e.g. '80,443,8080' or '1-1000'). Default: top 1000"},
        "scan_type": {"type": "string", "description": "Scan type: 'quick' (top 100), 'full' (all 65535), 'default' (top 1000)", "default": "default"},
        "scripts": {"type": "string", "description": "Comma-separated nmap scripts to run (e.g. 'http-headers,http-title')"},
    },
)
async def nmap_scan(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    ports = args.get("ports")
    scan_type = args.get("scan_type", "default")
    scripts = args.get("scripts")

    cmd = ["nmap", "-sV", "--open", "-oX", "-"]

    if ports:
        cmd.extend(["-p", ports])
    elif scan_type == "quick":
        cmd.extend(["--top-ports", "100"])
    elif scan_type == "full":
        cmd.extend(["-p", "1-65535"])
    # default: nmap's top 1000

    if scripts:
        cmd.extend(["--script", scripts])
    else:
        cmd.append("-sC")  # default scripts

    cmd.append(target)

    timeout = 60 if scan_type == "quick" else WEB_TOOL_TIMEOUT
    result = await arun_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT * 2)

    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)

    summary = _parse_nmap_xml(result["stdout"])
    return format_tool_result(summary)


# ── Vulnerability scanning ────────────────────────────────────────


@tool(
    "nuclei_scan",
    "Automated vulnerability scanning with Nuclei templates. Scans for CVEs, "
    "misconfigurations, exposures, and more. Returns findings as structured list.",
    {
        "target": {"type": "string", "description": "Target URL (e.g. https://example.com)"},
        "templates": {"type": "string", "description": "Template tags to use (e.g. 'cves,misconfigurations,exposures')"},
        "severity": {"type": "string", "description": "Filter by severity (e.g. 'critical,high,medium')", "default": "critical,high,medium"},
        "tags": {"type": "string", "description": "Additional tags filter (e.g. 'sqli,xss,rce')"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 120},
    },
)
async def nuclei_scan(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    templates = args.get("templates")
    severity = args.get("severity", "critical,high,medium")
    tags = args.get("tags")
    timeout = args.get("timeout", 120)

    cmd = ["nuclei", "-u", target, "-jsonl", "-silent"]

    if severity:
        cmd.extend(["-severity", severity])
    if templates:
        cmd.extend(["-tags", templates])
    if tags:
        cmd.extend(["-tags", tags])

    result = await arun_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT * 2)

    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)

    # Parse JSONL output into summary
    lines = []
    for line in result["stdout"].strip().split("\n"):
        if not line.strip():
            continue
        try:
            finding = json.loads(line)
            template_id = finding.get("template-id", "?")
            name = finding.get("info", {}).get("name", "?")
            sev = finding.get("info", {}).get("severity", "?")
            matched = finding.get("matched-at", "?")
            desc = finding.get("info", {}).get("description", "")
            entry = f"[{sev.upper()}] {template_id}: {name}\n  URL: {matched}"
            if desc:
                entry += f"\n  Desc: {desc[:200]}"
            lines.append(entry)
        except json.JSONDecodeError:
            lines.append(line)

    output = "\n\n".join(lines) if lines else "No vulnerabilities found."
    return format_tool_result(output)


# ── Web fuzzing ───────────────────────────────────────────────────


@tool(
    "ffuf_fuzz",
    "Web fuzzing for directory/file discovery and parameter fuzzing. Place FUZZ keyword "
    "in the URL where you want substitution. Returns discovered endpoints.",
    {
        "url": {"type": "string", "description": "Target URL with FUZZ keyword (e.g. https://example.com/FUZZ)"},
        "wordlist": {"type": "string", "description": "Wordlist path. Defaults to common.txt from seclists if available"},
        "method": {"type": "string", "description": "HTTP method", "default": "GET"},
        "headers": {"type": "object", "description": "Custom headers as key-value pairs", "default": {}},
        "filter_status": {"type": "string", "description": "Match only these status codes (e.g. '200,301,302')"},
        "filter_size": {"type": "string", "description": "Filter out responses of this size (e.g. '0' to hide empty)"},
        "extensions": {"type": "string", "description": "Extensions to append (e.g. '.php,.html,.js,.json')"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 60},
    },
)
async def ffuf_fuzz(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    url = args["url"]
    method = args.get("method", "GET")
    headers = args.get("headers", {})
    filter_status = args.get("filter_status")
    filter_size = args.get("filter_size")
    extensions = args.get("extensions")
    timeout = args.get("timeout", 60)

    # Find a wordlist
    wordlist = args.get("wordlist", "")

    # If a bare filename was given (not a full path), treat it as a wordlist name to resolve
    if wordlist and not os.path.isabs(wordlist) and not os.path.exists(wordlist):
        resolved = await _find_or_download_wordlist(os.path.basename(wordlist))
        if resolved:
            wordlist = resolved
        # else fall through — ffuf will report the error

    if not wordlist:
        wordlist = await _find_or_download_wordlist()
        if not wordlist:
            return format_error(
                "No wordlist found and download failed.\n"
                "Provide a wordlist path explicitly, or ensure internet access for auto-download."
            )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ["ffuf", "-u", url, "-w", wordlist, "-X", method, "-o", tmp_path, "-of", "json", "-noninteractive"]

        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

        if filter_status:
            cmd.extend(["-mc", filter_status])

        if filter_size:
            cmd.extend(["-fs", filter_size])

        if extensions:
            cmd.extend(["-e", extensions])

        result = await arun_cmd(cmd, timeout=timeout)

        if result["returncode"] != 0 and not os.path.exists(tmp_path):
            return cmd_result_to_tool_result(result)

        # Parse JSON output
        try:
            with open(tmp_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return cmd_result_to_tool_result(result)

        results = data.get("results", [])
        if not results:
            return format_tool_result("No results found.")

        lines = [f"Found {len(results)} result(s):\n"]
        for r in results[:100]:  # cap at 100
            status = r.get("status", "?")
            length = r.get("length", "?")
            words = r.get("words", "?")
            input_val = r.get("input", {}).get("FUZZ", "?")
            rurl = r.get("url", "?")
            lines.append(f"  [{status}] {rurl}  (size:{length}, words:{words}, input:{input_val})")

        if len(results) > 100:
            lines.append(f"\n  ... and {len(results) - 100} more results")

        return format_tool_result("\n".join(lines))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Fingerprinting ────────────────────────────────────────────────


@tool(
    "whatweb_fingerprint",
    "Web application fingerprinting — identifies web server, framework, CMS, "
    "programming language, JavaScript libraries, and other technologies. "
    "Uses whatweb if available, otherwise falls back to curl-based header analysis.",
    {
        "target": {"type": "string", "description": "Target URL (e.g. https://example.com)"},
        "aggression": {"type": "integer", "description": "Aggression level 1-4 (1=stealthy, 4=heavy)", "default": 1},
    },
)
async def whatweb_fingerprint(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    aggression = args.get("aggression", 1)

    # Try whatweb first
    if shutil.which("whatweb"):
        cmd = ["whatweb", f"--aggression={aggression}", "--log-json=-", target]
        result = await arun_cmd(cmd, timeout=30)
        # Check for Ruby load errors (broken on Ruby 3.4+)
        if result["returncode"] != 0 and "LoadError" in (result["stderr"] + result["stdout"]):
            pass  # fall through to curl fallback
        else:
            _kb_write_whatweb(target, result["stdout"])
            return cmd_result_to_tool_result(result)

    # Fallback: curl-based fingerprinting
    cmd = [
        "curl", "-s", "-S", "-D", "-", "-o", "/dev/null",
        "-L", "--max-time", "15",
        "-A", "Mozilla/5.0 (compatible; reverser/1.0)",
        target,
    ]
    result = await arun_cmd(cmd, timeout=20)
    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)

    headers_text = result["stdout"]

    # Also fetch the page body for technology hints
    body_cmd = [
        "curl", "-s", "-L", "--max-time", "15",
        "-A", "Mozilla/5.0 (compatible; reverser/1.0)",
        target,
    ]
    body_result = await arun_cmd(body_cmd, timeout=20, max_output=DEFAULT_MAX_OUTPUT)
    body = body_result.get("stdout", "")

    # Analyze headers and body for technology fingerprints
    lines = ["## HTTP Response Headers", headers_text, ""]

    # Extract technology hints from body
    tech_hints = []
    checks = [
        ("WordPress", ["wp-content", "wp-includes", "wordpress"]),
        ("Drupal", ["drupal", "sites/default/files"]),
        ("Joomla", ["joomla", "/media/system/js"]),
        ("React", ["react", "react-dom", "_reactroot"]),
        ("Angular", ["ng-version", "angular", "ng-app"]),
        ("Vue.js", ["vue.js", "vue.min.js", "__vue__"]),
        ("jQuery", ["jquery"]),
        ("Bootstrap", ["bootstrap"]),
        ("Next.js", ["_next/", "__next"]),
        ("Nuxt.js", ["_nuxt/", "__nuxt"]),
        ("ASP.NET", ["asp.net", "__viewstate", "x-aspnet-version"]),
        ("PHP", ["x-powered-by: php", ".php"]),
        ("Laravel", ["laravel", "csrf-token"]),
        ("Django", ["csrfmiddlewaretoken", "django"]),
        ("Spring", ["spring", "jsessionid"]),
        ("Ruby on Rails", ["x-powered-by: phusion", "rails", "csrf-token"]),
        ("Express.js", ["x-powered-by: express"]),
        ("Nginx", ["server: nginx"]),
        ("Apache", ["server: apache"]),
        ("IIS", ["server: microsoft-iis"]),
        ("Cloudflare", ["server: cloudflare", "cf-ray"]),
        ("AWS", ["x-amz-", "awselb", "awsalb"]),
    ]

    combined = (headers_text + "\n" + body).lower()
    for tech, patterns in checks:
        for pattern in patterns:
            if pattern in combined:
                tech_hints.append(tech)
                break

    if tech_hints:
        lines.append("## Detected Technologies")
        for t in tech_hints:
            lines.append(f"  - {t}")
    else:
        lines.append("## No specific technologies detected from headers/body")

    final = "\n".join(lines)
    _kb_write_whatweb(target, final)
    return format_tool_result(final)


# ── Web server scanning ──────────────────────────────────────────


@tool(
    "nikto_scan",
    "Web server vulnerability scanner. Checks for misconfigurations, default files, "
    "outdated software, and known vulnerabilities.",
    {
        "target": {"type": "string", "description": "Target URL or host:port (e.g. https://example.com)"},
        "tuning": {"type": "string", "description": "Nikto tuning options (e.g. '1' for interesting files, '2' for misconfig)"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 120},
    },
)
async def nikto_scan(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    tuning = args.get("tuning")
    timeout = args.get("timeout", 120)

    cmd = ["nikto", "-h", target, "-nointeractive"]

    if tuning:
        cmd.extend(["-Tuning", tuning])

    result = await arun_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT)
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nikto_findings
        kb = for_target(target)
        for finding in parse_nikto_findings(result["stdout"]):
            kb.record_finding(finding)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nikto_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


# ── TLS/SSL analysis ─────────────────────────────────────────────


@tool(
    "testssl_analyze",
    "TLS/SSL configuration analysis. Checks protocols, cipher suites, certificate "
    "validity, and known vulnerabilities (Heartbleed, POODLE, BEAST, etc.).",
    {
        "target": {"type": "string", "description": "Target host:port (e.g. example.com:443 or example.com)"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 90},
    },
)
async def testssl_analyze(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    timeout = args.get("timeout", 90)

    cmd = ["testssl.sh", "--color", "0", target]
    result = await arun_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT * 2)
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_ssl_findings
        kb = for_target(target)
        out = parse_ssl_findings(result["stdout"])
        for f in out["findings"]:
            kb.record_finding(f)
        if out["note"]:
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in testssl_analyze: %s", e)
    # ───────────────────────────────────────────────────────────────────
    return cmd_result_to_tool_result(result)


# ── SQL injection ─────────────────────────────────────────────────


@tool(
    "sqlmap_test",
    "SQL injection testing with sqlmap. Tests URL parameters, POST data, and forms "
    "for SQL injection vulnerabilities. Runs in batch (non-interactive) mode.",
    {
        "url": {"type": "string", "description": "Target URL with parameters (e.g. https://example.com/page?id=1)"},
        "data": {"type": "string", "description": "POST data (e.g. 'username=admin&password=test')"},
        "param": {"type": "string", "description": "Specific parameter to test (e.g. 'id')"},
        "level": {"type": "integer", "description": "Test level 1-5 (higher = more tests)", "default": 1},
        "risk": {"type": "integer", "description": "Risk level 1-3 (higher = more aggressive)", "default": 1},
        "technique": {"type": "string", "description": "Injection techniques (B=boolean, E=error, U=union, S=stacked, T=time)"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 120},
    },
)
async def sqlmap_test(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    url = args["url"]
    data = args.get("data")
    param = args.get("param")
    level = args.get("level", 1)
    risk = args.get("risk", 1)
    technique = args.get("technique")
    timeout = args.get("timeout", 120)

    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [
            "sqlmap", "-u", url,
            "--batch",
            "--level", str(level),
            "--risk", str(risk),
            "--output-dir", tmp_dir,
        ]

        if data:
            cmd.extend(["--data", data])
        if param:
            cmd.extend(["-p", param])
        if technique:
            cmd.extend(["--technique", technique])

        result = await arun_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT)
        return cmd_result_to_tool_result(result)


# ── Subdomain enumeration ─────────────────────────────────────────


@tool(
    "subfinder_enum",
    "Subdomain enumeration using passive sources. Discovers subdomains without "
    "active scanning (DNS brute-force, certificate transparency, etc.).",
    {
        "domain": {"type": "string", "description": "Target domain (e.g. example.com)"},
        "timeout": {"type": "integer", "description": "Scan timeout in seconds", "default": 60},
    },
)
async def subfinder_enum(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    domain = args["domain"]
    timeout = args.get("timeout", 60)

    cmd = ["subfinder", "-d", domain, "-json", "-silent"]
    result = await arun_cmd(cmd, timeout=timeout)

    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)

    # Parse JSON lines into a subdomain list
    subdomains = []
    for line in result["stdout"].strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            host = entry.get("host", line.strip())
            subdomains.append(host)
        except json.JSONDecodeError:
            # Some versions output plain text
            subdomains.append(line.strip())

    if subdomains:
        output = f"Found {len(subdomains)} subdomain(s):\n\n" + "\n".join(f"  {s}" for s in subdomains)
    else:
        output = "No subdomains found."

    return format_tool_result(output)


# ── WAF detection ─────────────────────────────────────────────────


@tool(
    "wafw00f_detect",
    "Web Application Firewall (WAF) detection. Identifies if a WAF is protecting "
    "the target and attempts to determine which WAF product is in use.",
    {
        "target": {"type": "string", "description": "Target URL (e.g. https://example.com)"},
    },
)
async def wafw00f_detect(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    cmd = ["wafw00f", target, "-o", "-", "-f", "json"]
    result = await arun_cmd(cmd, timeout=30)
    return cmd_result_to_tool_result(result)


# ── Tool registry ─────────────────────────────────────────────────

TOOLS = [
    http_request,
    nmap_scan,
    nuclei_scan,
    ffuf_fuzz,
    whatweb_fingerprint,
    nikto_scan,
    testssl_analyze,
    sqlmap_test,
    subfinder_enum,
    wafw00f_detect,
]


def _kb_write_whatweb(target: str, stdout: str) -> None:
    """KB write tail for whatweb_fingerprint — host_ip/port derived from URL."""
    try:
        from urllib.parse import urlparse
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_whatweb_plugins
        parsed = urlparse(target if "://" in target else f"http://{target}")
        host_ip = parsed.hostname or target
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        kb = for_target(target)
        out = parse_whatweb_plugins(stdout, host_ip=host_ip, port=port)
        if out.get("service"):
            kb.record_host(HostFact(ip=host_ip))
            kb.record_service(out["service"])
        if out.get("note"):
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in whatweb_fingerprint: %s", e)
