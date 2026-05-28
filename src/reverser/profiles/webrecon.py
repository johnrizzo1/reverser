"""Web reconnaissance (non-intrusive) profile."""

from . import _register, Profile
from ._skills import (
    SKILL_WEB_RECON,
    SKILL_WEB_DISCOVER,
    SKILL_WEB_SSL,
    SKILL_WEB_MANUAL,
    SKILL_WEB_REPORT,
)


PROFILE_WEBRECON = _register(Profile(
    name="Web Recon",
    key="webrecon",
    description="Non-intrusive web reconnaissance only — no active exploitation",
    domain="web",
    system_addendum="""\

## Profile: Web Reconnaissance (Non-Intrusive)

You are performing reconnaissance ONLY. Do NOT attempt active exploitation, SQL injection, \
or other attacks. Your goal is to map the attack surface and identify potential areas of \
concern without causing any impact.

Allowed activities:
- Technology fingerprinting (whatweb)
- Subdomain enumeration (subfinder — passive only)
- Port scanning (nmap — service detection OK)
- TLS/SSL analysis (testssl)
- Security header review (http_request with HEAD/GET)
- Directory discovery (ffuf with small wordlists)
- Cookie and session analysis (http_request)
- WAF detection (wafw00f)
- robots.txt, sitemap.xml, .well-known analysis
- Public information gathering

NOT allowed:
- SQL injection testing
- XSS payload injection
- Authentication brute-forcing
- Active exploitation of any kind
- Heavy scanning that could cause service impact
""",
    skills=[
        SKILL_WEB_RECON, SKILL_WEB_DISCOVER, SKILL_WEB_SSL,
        SKILL_WEB_MANUAL, SKILL_WEB_REPORT,
    ],
))
