"""Web API penetration testing profile."""

from . import _register, Profile
from ._skills import (
    SKILL_WEB_RECON,
    SKILL_WEB_SCAN,
    SKILL_WEB_DISCOVER,
    SKILL_WEB_SSL,
    SKILL_WEB_SQLI,
    SKILL_WEB_MANUAL,
    SKILL_WEB_REPORT,
)


PROFILE_WEBAPI = _register(Profile(
    name="Web API Pentest",
    key="webapi",
    description="REST/GraphQL API penetration testing — auth bypass, BOLA, injection",
    system_addendum="""\

## Profile: API Penetration Testing

You are testing a web API (REST, GraphQL, or similar). Focus on API-specific vulnerabilities:

### Authentication & Authorization
- Test JWT handling: algorithm confusion, weak secrets, token expiry, none algorithm
- Test OAuth flows: redirect_uri manipulation, state parameter, scope escalation
- Check API key security: key in URL vs header, key rotation, key scope
- Test for BOLA/IDOR: modify object IDs in requests to access other users' data

### API-Specific Vulnerabilities
- **BOLA (Broken Object Level Authorization)**: Change IDs in /api/users/{id}, /api/orders/{id}
- **Mass Assignment**: Send extra fields in PUT/PATCH requests (role, isAdmin, balance)
- **Excessive Data Exposure**: Check if API returns more data than the frontend uses
- **Rate Limiting**: Test for missing rate limits on sensitive endpoints (login, password reset)
- **Injection**: SQL injection in query parameters, NoSQL injection in JSON bodies

### GraphQL-Specific
- Test introspection query: `{__schema{types{name,fields{name}}}}`
- Test for query depth/complexity limits
- Check for batching attacks
- Test field-level authorization

### Methodology
1. Map all API endpoints using ffuf_fuzz and manual probing
2. Analyze authentication mechanism
3. Test authorization on every endpoint with different user contexts
4. Test input validation and injection on all parameters
5. Check rate limiting and resource consumption
""",
    skills=[
        SKILL_WEB_RECON, SKILL_WEB_SCAN, SKILL_WEB_DISCOVER, SKILL_WEB_SSL,
        SKILL_WEB_MANUAL, SKILL_WEB_SQLI, SKILL_WEB_REPORT,
    ],
))
