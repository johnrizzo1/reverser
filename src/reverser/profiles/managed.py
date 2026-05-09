"""Java / .NET managed code analysis profile."""

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


PROFILE_MANAGED = _register(Profile(
    name="Java / .NET",
    key="managed",
    description="JVM bytecode (JAR/class) and .NET assemblies (DLL/EXE with IL)",
    system_addendum="""\

## Profile: Java / .NET Managed Code Analysis

You are analyzing managed code — either JVM bytecode or .NET IL. Key approach:
- First determine the type: use `file_info` and check for:
  - Java: JAR files (ZIP with META-INF/MANIFEST.MF), .class files (0xCAFEBABE magic)
  - .NET: PE with _CorExeMain import, CLI header, mscoree.dll dependency
- Use `strings_search` extensively — managed code is rich in string literals:
  - Class names, method names, package/namespace names
  - SQL queries, connection strings
  - API endpoints, URLs
  - Reflection targets, serialization hints
  - Resource file references
- For Java:
  - `binwalk_scan` to list JAR contents
  - Look for obfuscation: single-letter class/method names, string encryption, class loading tricks
  - Identify frameworks: Spring, Hibernate, Apache libraries
  - Check for deserialization gadgets (ObjectInputStream, XMLDecoder)
- For .NET:
  - `pe_info` for the PE structure and imports
  - Look for P/Invoke declarations (native interop)
  - Identify framework usage: ASP.NET, WCF, Entity Framework
  - Check for reflection-based calls, dynamic assembly loading
- Security focus:
  - Hardcoded credentials and connection strings
  - Insecure deserialization
  - SQL injection patterns
  - Cryptographic key material
  - Debug/trace code left in production
""",
    skills=[
        SKILL_TRIAGE, SKILL_ANALYZE, SKILL_API_MAP, SKILL_STRINGS, SKILL_DECOMPILE,
        SKILL_IMPORTS, SKILL_RUN, SKILL_SYSCALLS, SKILL_WRITEUP,
    ],
))
