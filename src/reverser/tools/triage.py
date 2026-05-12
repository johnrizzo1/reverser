"""Triage tools: quick binary identification and initial assessment."""

from claude_agent_sdk import tool

from ._common import arun_cmd, paginate, format_tool_result, format_error, cmd_result_to_tool_result, is_pe


@tool(
    "file_info",
    "Identify file type, architecture, and format using the `file` command.",
    {"path": {"type": "string", "description": "Path to the binary file"}},
)
async def file_info(args: dict) -> dict:
    result = await arun_cmd(["file", "-b", args["path"]])
    return cmd_result_to_tool_result(result)


@tool(
    "strings_search",
    "Extract printable strings from a binary. Use grep_pattern to filter, offset/limit to paginate.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary file"},
            "min_length": {"type": "integer", "description": "Minimum string length", "default": 8},
            "grep_pattern": {"type": "string", "description": "Optional grep pattern to filter strings"},
            "offset": {"type": "integer", "description": "Line offset for pagination", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 100},
        },
        "required": ["path"],
    },
)
async def strings_search(args: dict) -> dict:
    min_len = args.get("min_length", 8)
    cmd = ["strings", "-n", str(min_len), args["path"]]
    result = await arun_cmd(cmd, max_output=50000)  # grab more, paginate below

    if result["returncode"] != 0:
        return cmd_result_to_tool_result(result)

    text = result["stdout"]
    grep = args.get("grep_pattern")
    if grep:
        import re
        try:
            pattern = re.compile(grep, re.IGNORECASE)
            text = "\n".join(line for line in text.split("\n") if pattern.search(line))
        except re.error:
            text = "\n".join(line for line in text.split("\n") if grep.lower() in line.lower())

    page = paginate(text, args.get("offset", 0), args.get("limit", 100))
    return format_tool_result(f"{page['showing']}\n\n{page['content']}")


@tool(
    "checksec_binary",
    "Check binary security properties: NX, PIE, RELRO, stack canary, FORTIFY. Returns JSON.",
    {"path": {"type": "string", "description": "Path to the binary file"}},
)
async def checksec_binary(args: dict) -> dict:
    result = await arun_cmd(["checksec", "--output=json", f"--file={args['path']}"])
    return cmd_result_to_tool_result(result)


@tool(
    "readelf_info",
    "Display ELF file information. Pass flags like '-h' (header), '-S' (sections), '-s' (symbols), '-d' (dynamic), '-l' (segments), '-r' (relocations), or '-a' (all).",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the ELF binary"},
            "flags": {"type": "string", "description": "readelf flags (e.g. '-h', '-S', '-s', '-a')", "default": "-h"},
        },
        "required": ["path"],
    },
)
async def readelf_info(args: dict) -> dict:
    flags = args.get("flags", "-h").split()
    result = await arun_cmd(["readelf"] + flags + [args["path"]])
    return cmd_result_to_tool_result(result)


@tool(
    "binwalk_scan",
    "Scan a binary for embedded file signatures, compressed archives, and firmware headers.",
    {"path": {"type": "string", "description": "Path to the binary file"}},
)
async def binwalk_scan(args: dict) -> dict:
    result = await arun_cmd(["binwalk", args["path"]])
    return cmd_result_to_tool_result(result)


@tool(
    "pe_info",
    "Analyze a Windows PE executable: headers, sections, imports, exports, and security features. "
    "Use this instead of readelf for PE/DLL/EXE files.",
    {"path": {"type": "string", "description": "Path to the PE file (.exe, .dll, .sys)"}},
)
async def pe_info(args: dict) -> dict:
    try:
        import pefile
    except ImportError:
        return format_error("pefile not installed. Run inside devenv shell.")

    try:
        pe = pefile.PE(args["path"])
    except pefile.PEFormatError as e:
        return format_error(f"Not a valid PE file: {e}")

    lines = []

    # Basic info
    machine = pefile.MACHINE_TYPE.get(pe.FILE_HEADER.Machine, f"0x{pe.FILE_HEADER.Machine:x}")
    lines.append(f"Machine: {machine}")
    lines.append(f"Subsystem: {pefile.SUBSYSTEM_TYPE.get(pe.OPTIONAL_HEADER.Subsystem, 'unknown')}")
    lines.append(f"Entry point: 0x{pe.OPTIONAL_HEADER.AddressOfEntryPoint:x}")
    lines.append(f"Image base: 0x{pe.OPTIONAL_HEADER.ImageBase:x}")
    lines.append(f"Timestamp: {pe.FILE_HEADER.TimeDateStamp}")

    is_64 = pe.OPTIONAL_HEADER.Magic == 0x20b
    lines.append(f"Architecture: {'x86-64' if is_64 else 'x86 (32-bit)'}")

    # DLL characteristics (security features)
    chars = pe.OPTIONAL_HEADER.DllCharacteristics
    security = []
    if chars & 0x0040:
        security.append("ASLR (Dynamic Base)")
    if chars & 0x0100:
        security.append("NX (DEP)")
    if chars & 0x4000:
        security.append("Control Flow Guard")
    if chars & 0x0400:
        security.append("No SEH")
    if chars & 0x0020:
        security.append("High Entropy ASLR")
    lines.append(f"Security: {', '.join(security) if security else 'None detected'}")

    # Sections
    lines.append(f"\nSections ({pe.FILE_HEADER.NumberOfSections}):")
    for s in pe.sections:
        name = s.Name.decode("utf-8", errors="replace").rstrip("\x00")
        lines.append(f"  {name:10s}  VA=0x{s.VirtualAddress:08x}  Size={s.SizeOfRawData:8d}  Entropy={s.get_entropy():.2f}")

    # Imports
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        lines.append(f"\nImports ({len(pe.DIRECTORY_ENTRY_IMPORT)} DLLs):")
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            dll_name = entry.dll.decode("utf-8", errors="replace")
            funcs = [imp.name.decode("utf-8", errors="replace") for imp in entry.imports if imp.name]
            if len(funcs) > 10:
                funcs_str = ", ".join(funcs[:10]) + f" ... (+{len(funcs) - 10} more)"
            else:
                funcs_str = ", ".join(funcs)
            lines.append(f"  {dll_name}: {funcs_str}")

    # Exports
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        exports = pe.DIRECTORY_ENTRY_EXPORT.symbols
        lines.append(f"\nExports ({len(exports)}):")
        for exp in exports[:20]:
            name = exp.name.decode("utf-8", errors="replace") if exp.name else f"ordinal_{exp.ordinal}"
            lines.append(f"  {name}  @ 0x{exp.address:x}")
        if len(exports) > 20:
            lines.append(f"  ... (+{len(exports) - 20} more)")

    pe.close()

    output = "\n".join(lines)
    if len(output) > 8000:
        output = output[:8000] + "\n\n[OUTPUT TRUNCATED]"
    return format_tool_result(output)


TOOLS = [file_info, strings_search, checksec_binary, readelf_info, pe_info, binwalk_scan]
