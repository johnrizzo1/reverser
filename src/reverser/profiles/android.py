"""Android APK/XAPK analysis profile."""

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


PROFILE_ANDROID = _register(Profile(
    name="Android APK",
    key="android",
    description="APK/XAPK analysis — manifest, DEX, native libs, API endpoints",
    system_addendum="""\

## Profile: Android APK/XAPK Analysis

You are analyzing an Android application package. Key approach:
- First use `file_info` and `binwalk_scan` to identify the package structure
- Use `strings_search` extensively to find:
  - API endpoints, URLs, hostnames
  - Package names, activity names, content provider URIs
  - Firebase/cloud service references
  - Hardcoded keys, tokens, secrets
- For DEX files within the APK, use `strings_search` and static analysis tools
- Look for native libraries (.so files) in lib/ directories — analyze these as ELF binaries
- Focus on:
  - AndroidManifest.xml: permissions, components, intent filters
  - API communication: HTTP clients (OkHttp, Retrofit, Volley), WebSocket, gRPC
  - Authentication: OAuth, JWT, API keys, certificate pinning
  - Data storage: SharedPreferences, SQLite, encrypted storage
  - Third-party SDKs: analytics, ads, crash reporting
  - Obfuscation: ProGuard/R8 name mangling, string encryption, class loading tricks
- When the binary is a .so native library, do full ELF analysis with JNI function identification
""",
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
