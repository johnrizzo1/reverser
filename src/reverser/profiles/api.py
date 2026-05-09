"""API discovery and documentation profile."""

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


PROFILE_API = _register(Profile(
    name="API Discovery",
    key="api",
    description="Focus on documenting network APIs, endpoints, and backend communication",
    system_addendum="""\

## Profile: API Discovery and Documentation

Your primary goal is to document the API surface between this software and its backend services. \
This is NOT a CTF — do not try to crack or bypass anything. Instead, produce a thorough API reference.

Focus areas:
1. **Endpoint Discovery**: Find all URLs, hostnames, API paths in strings and code
2. **Request/Response Format**: Identify HTTP methods, content types, serialization (JSON, protobuf, XML, msgpack)
3. **Authentication**: Document auth mechanisms — OAuth, JWT, API keys, session tokens, mTLS, HMAC signatures
4. **Data Models**: Identify request/response schemas from serialization code
5. **Error Handling**: Find error codes, retry logic, fallback endpoints
6. **Rate Limiting**: Look for throttling, backoff, or quota logic
7. **WebSocket/Streaming**: Identify real-time communication channels and message formats
8. **Certificate Pinning**: Document TLS configuration and pinned certificates

Output format: Produce a structured API reference with:
- Base URL(s) and environment detection (prod/staging/dev)
- For each endpoint: method, path, auth required, request schema, response schema, error codes
- Authentication flow diagram (in text)
- Notable headers (User-Agent, custom headers, API versioning)

Analysis approach:
- Start with `strings_search` for URLs and API paths
- Decompile network-related functions
- Trace `r2_command` cross-references from URL strings to calling functions
- Map the request construction flow from parameters → serialization → HTTP call
- Use `strace_run` with network category to observe actual network calls if possible
""",
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
