"""Chrome browser extension analysis profile."""

from . import _register, Profile
from ._skills import (
    SKILL_TRIAGE,
    SKILL_ANALYZE,
    SKILL_API_MAP,
    SKILL_STRINGS,
    SKILL_DECOMPILE,
    SKILL_IMPORTS,
    SKILL_RUN,
    SKILL_SYSCALLS,
    SKILL_WRITEUP,
)


PROFILE_CHROME = _register(Profile(
    name="Chrome Extension",
    key="chrome",
    description="CRX/ZIP browser extensions — manifest, JS, permissions, API hooks",
    system_addendum="""\

## Profile: Chrome Browser Extension Analysis

You are analyzing a Chrome browser extension (CRX or extracted ZIP). Key approach:
- Use `binwalk_scan` and `file_info` to identify the archive structure
- Use `strings_search` extensively across all files to find:
  - API endpoints and external service URLs
  - Chrome extension API usage (chrome.*, browser.*)
  - Content Security Policy directives
  - OAuth client IDs, API keys, tokens
  - WebRequest/WebNavigation hooks (traffic interception)
- Focus on these critical areas:
  - **manifest.json**: permissions, content_security_policy, background scripts, content scripts
  - **Permissions analysis**: activeTab, tabs, webRequest, webRequestBlocking, cookies, storage, <all_urls>
  - **Background/service worker**: main extension logic, message passing, alarm handlers
  - **Content scripts**: DOM manipulation, page injection, data exfiltration vectors
  - **Native messaging**: communication with host applications
  - **Web accessible resources**: pages/scripts exposed to web content
- Security concerns:
  - Overprivileged permissions
  - eval() or Function() usage
  - External script loading (CDN, remote code execution)
  - Data exfiltration to third-party servers
  - XSS vectors in extension pages
  - Message passing without origin validation
- Document the extension's data flow: what it reads from pages, what it sends externally
""",
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
